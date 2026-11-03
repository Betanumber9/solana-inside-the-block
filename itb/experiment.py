"""The measurement loop.

For every chosen validator we wait for its leader window, then fire a burst of probe transactions at the start of
the window. Inside a burst the probes differ only in what is being tested:
  ladder_shared  - one fee payer (all probes write-lock the same account), compute-unit prices on a ladder;
  ladder_indep   - one fee payer per probe (no shared writable account), same ladder;
  equal_indep    - one fee payer per probe, one price, wider spacing: only send order differs.
Price and send position are assigned by two independent random permutations, so their effects can be told apart.
In dry-run everything happens (schedule, timing, building, signing, block reading) except sendTransaction.
"""
import asyncio
import gzip
import json
import pathlib
import random
import time

from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

from . import chain, wallet
from .budget import Budget, CapExceeded
from .rpc import Rpc, SlotClock

LADDER = [1_000, 3_000, 10_000, 30_000, 100_000, 300_000, 1_000_000, 3_000_000]  # micro-lamports per CU
EQUAL_PRICE = 100_000
SERIES = ("ladder_shared", "ladder_indep", "equal_indep")


def now_ns():
    return time.time_ns()


class Writer:
    def __init__(self, run_dir):
        self.dir = pathlib.Path(run_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def jsonl(self, name, row):
        with open(self.dir / name, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")

    def event(self, kind, **kw):
        row = {"t": time.time(), "kind": kind, **kw}
        self.jsonl("events.jsonl", row)
        print("[%s] %s %s" % (time.strftime("%H:%M:%S"), kind,
                              " ".join("%s=%s" % (k, v) for k, v in kw.items() if not isinstance(v, (list, dict)))),
              flush=True)


class BlockStore:
    """Blocks saved once, gzip JSON, data/blocks/<slot>.json.gz. 'skipped' files mark empty slots."""

    def __init__(self, data_dir, rpc):
        self.dir = pathlib.Path(data_dir) / "blocks"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.rpc = rpc

    def path(self, slot):
        return self.dir / ("%d.json.gz" % slot)

    def load(self, slot):
        p = self.path(slot)
        if p.exists():
            with gzip.open(p, "rt", encoding="utf-8") as f:
                return json.load(f)
        return None

    async def get(self, slot):
        b = self.load(slot)
        if b is not None:
            return b
        b = await chain.block(self.rpc, slot)
        if b is None:
            b = {"slot": slot, "skipped": True}
        with gzip.open(self.path(slot), "wt", encoding="utf-8") as f:
            json.dump(b, f, separators=(",", ":"))
        return b


def pick_targets(land, by_slot, from_slot, per_client, clients=None, min_windows=8):
    """Per client, the validators with the most windows left in the epoch (more windows = more repeats)."""
    left = {}
    for w in chain.windows(by_slot, from_slot, max(by_slot)):
        left[w["leader"]] = left.get(w["leader"], 0) + 1
    by_client = {}
    for pk, v in land["validators"].items():
        if left.get(pk, 0) >= min_windows and (clients is None or v["client"] in clients):
            by_client.setdefault(v["client"], []).append((left[pk], v["stake"], pk))
    targets = {}
    for c, lst in by_client.items():
        if c.startswith("Unknown") or c == "unknown":
            continue
        for n, _, pk in sorted(lst, reverse=True)[:per_client]:
            targets[pk] = dict(land["validators"][pk], windows_left=n)
    return targets


class Experiment:
    def __init__(self, cfg, args):
        self.cfg = cfg
        self.a = args
        self.live = bool(args.live)
        self.run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + ("_live" if self.live else "_dry")
        self.w = Writer(pathlib.Path(cfg.data_dir) / "runs" / self.run_id)
        self.rpc = Rpc(cfg.rpc_url, cfg.rpc_interval, heavy_interval=cfg.block_interval)
        self.send_rpc = self.rpc if cfg.send_url == cfg.rpc_url else Rpc(cfg.send_url, 0.0)
        self.clock = SlotClock(cfg.ws_url, self.rpc)
        self.blocks = BlockStore(cfg.data_dir, self.rpc)
        self.rng = random.Random(args.seed)
        self.burst_no = 0
        self.last_burst_at = 0.0
        self.skipped = set()
        self.done = {}
        self.collectors = []
        self.budget = Budget(pathlib.Path(cfg.data_dir) / "budget.json", int(args.cap_sol * wallet.LAMPORTS))

    async def setup(self):
        key = wallet.main_key(self.cfg.secrets_dir)
        if self.live and key is None:
            raise SystemExit("--live needs a key: secrets/keypair.json or ITB_SECRET_KEY")
        self.key = key or Keypair()  # dry-run without a key signs with a throwaway key
        self.key_kind = "main" if key else "ephemeral"
        n = max(len(LADDER), self.a.burst)
        self.subs = wallet.sub_keys(self.cfg.secrets_dir, n) if key else [Keypair() for _ in range(n)]
        series = [s for s in self.a.series.split(",") if s]
        if self.live and len(self.subs) < n:
            series = [s for s in series if s == "ladder_shared"]
            self.w.event("note", msg="no funded sub-wallets: only ladder_shared will run (see `fund`)")
        self.series = series
        await self.clock.start()
        land = await chain.landscape(self.rpc)
        self.epoch = land["epoch"]
        first, last, self.by_slot = await chain.schedule(self.rpc, self.epoch)
        clients = set(self.a.clients.split(",")) if self.a.clients else None
        if self.a.validators:
            ids = [v for v in self.a.validators.split(",") if v]
            self.targets = {pk: dict(land["validators"][pk], windows_left=None) for pk in ids if pk in land["validators"]}
        else:
            self.targets = pick_targets(land, self.by_slot, self.clock.slot + 50, self.a.per_client, clients)
        self.cu_limit = self.a.cu_limit
        meta = {"run_id": self.run_id, "live": self.live, "args": vars(self.a), "endpoints": self.cfg.public(),
                "epoch": self.epoch, "epoch_first_slot": first, "epoch_last_slot": last, "key": self.key_kind,
                "payer_main": str(self.key.pubkey()), "payers_sub": [str(k.pubkey()) for k in self.subs],
                "ladder": LADDER, "equal_price": EQUAL_PRICE, "series": self.series, "cu_limit": self.cu_limit,
                "targets": self.targets, "started_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
        (self.w.dir / "meta.json").write_text(json.dumps(meta, indent=1))
        with gzip.open(self.w.dir / "landscape.json.gz", "wt", encoding="utf-8") as f:
            json.dump(land, f)
        for pk, t in self.targets.items():
            self.w.event("target", client=t["client"], version=t["version"], identity=pk,
                         stake_sol=t["stake"] // wallet.LAMPORTS, windows_left=t["windows_left"])
        await self.calibrate()

    async def calibrate(self):
        """Simulate one probe on mainnet: real compute use against our limit. Needs a funded payer to simulate;
        without a key we borrow the fee account of the current leader (simulation only, nothing is signed for it)."""
        payer = self.key.pubkey()
        bal = await self.rpc.call("getBalance", [str(payer)])
        if bal["value"] == 0:
            bl = await self.blocks.get(self.clock.slot - 40)
            if bl.get("leader"):
                payer = Pubkey.from_string(bl["leader"])
        ixs = [set_compute_unit_limit(self.cu_limit), set_compute_unit_price(LADDER[-1]),
               Instruction(wallet.MEMO, wallet.memo_text(0, 0).encode(), [])]
        tx = Transaction.new_unsigned(Message.new_with_blockhash(ixs, payer, Hash.default()))
        r = await self.rpc.call("simulateTransaction", [wallet.b64(tx), {"encoding": "base64", "sigVerify": False,
                                                                         "replaceRecentBlockhash": True}])
        v = r["value"]
        self.w.event("calibrate", err=v.get("err"), units=v.get("unitsConsumed"), limit=self.cu_limit,
                     fee_max=wallet.fee_estimate(self.cu_limit, LADDER[-1]))
        if v.get("err") or (v.get("unitsConsumed") or 0) > self.cu_limit:
            raise SystemExit("probe does not fit its compute limit: %s" % v)

    def next_series(self, leader):
        n = self.done.get(leader, 0)
        return self.series[n % len(self.series)]

    def build(self, series, blockhash):
        n = self.a.burst
        group = [None] * n
        if series == "equal_indep":
            prices = [EQUAL_PRICE] * n
        elif series.startswith("split"):
            # confirmation series: group A = the cheaper half, sent first; group B = the dearer half, sent
            # split_gap_ms later. Inside each group, price and send order are shuffled independently.
            half = n // 2
            ladder = sorted(LADDER[:n])
            prices = self.rng.sample(ladder[:half], half) + self.rng.sample(ladder[half:], n - half)
            group = ["A"] * half + ["B"] * (n - half)
        else:
            prices = LADDER[:n] if n <= len(LADDER) else [LADDER[i % len(LADDER)] for i in range(n)]
            prices = self.rng.sample(prices, n)
        if series.startswith("split"):
            half = n // 2
            order = self.rng.sample(range(half), half) + self.rng.sample(range(half, n), n - half)
        else:
            order = self.rng.sample(range(n), n)  # order[pos] = probe k sent at position pos
        bno = self.burst_no
        probes = []
        for k in range(n):
            payer = self.key if series in ("ladder_shared",) or series.startswith("split") else self.subs[k]
            tx = wallet.probe_tx(payer, blockhash, self.cu_limit, prices[k], wallet.memo_text(bno, k))
            probes.append({"k": k, "price": prices[k], "payer": str(payer.pubkey()), "sig": str(tx.signatures[0]),
                           "memo": wallet.memo_text(bno, k), "send_pos": order.index(k), "tx": wallet.b64(tx),
                           "group": group[k]})
        return probes, order

    async def balances(self):
        keys = [str(self.key.pubkey())] + [str(k.pubkey()) for k in self.subs]
        r = await self.rpc.call("getMultipleAccounts", [keys, {"encoding": "base64", "commitment": "confirmed"}])
        return sum((a or {}).get("lamports", 0) for a in r["value"])

    async def burst(self, win, series):
        """Fire one burst into the window `win` = {start, slots, leader}."""
        t = self.targets[win["leader"]]
        self.burst_no += 1
        bno = self.burst_no
        bh = await self.rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}])
        probes, order = self.build(series, wallet.to_hash(bh["value"]["blockhash"]))
        est = sum(wallet.fee_estimate(self.cu_limit, p["price"]) for p in probes)
        key = "%s/b%d" % (self.run_id, bno)
        if self.live:
            spent = self.budget.check_balance(await self.balances())
            self.budget.reserve(key, est)
        else:
            spent = 0
        gap = self.a.gap_ms_equal if series == "equal_indep" else self.a.gap_ms
        trigger = win["start"] + self.a.trigger_offset
        if self.clock.slot >= trigger:
            self.w.event("late", burst=bno, at=self.clock.slot, trigger=trigger)
        await self.clock.wait_until(trigger, timeout=90)
        seen_slot, seen_lag = self.clock.slot, time.time() - self.clock.seen_at
        if seen_slot > trigger + self.a.max_late_slots:
            # e.g. the network dropped while waiting: firing now would hit some other leader's block
            self.w.event("abandoned_late", burst=bno, seen=seen_slot, trigger=trigger)
            if self.live:
                self.budget.settle(key, 0)
            return
        if self.a.trigger_delay_ms:
            await asyncio.sleep(self.a.trigger_delay_ms / 1000)
        sent = []

        async def fire(p, pos):
            t0 = now_ns()
            slot_at = self.clock.slot
            if self.live:
                res, err = await self.send_rpc.send_raw(p["tx"], self.a.max_retries)
            else:
                res, err = "dry-run", None
            sent.append({"k": p["k"], "dispatch_ns": t0, "ack_ns": now_ns(), "slot_at_dispatch": slot_at,
                         "resp": res if res == "dry-run" else (res == p["sig"]), "err": err})

        tasks = []
        split_gap = int(series[5:]) if series.startswith("split") else 0
        for pos, k in enumerate(order):
            tasks.append(asyncio.create_task(fire(probes[k], pos)))
            if pos < len(order) - 1:
                wait = gap + (split_gap if pos == len(order) // 2 - 1 else 0)
                if wait:
                    await asyncio.sleep(wait / 1000)
        await asyncio.gather(*tasks)
        by_k = {s["k"]: s for s in sent}
        for p in probes:
            row = {"run": self.run_id, "burst": bno, "series": series, "live": self.live, "leader": win["leader"],
                   "client": t["client"], "version": t["version"], "window_start": win["start"],
                   "window_slots": win["slots"], "trigger_slot": trigger, "seen_slot": seen_slot,
                   "seen_lag_ms": round(seen_lag * 1000, 1), "gap_ms": gap, "cu_limit": self.cu_limit,
                   "blockhash": bh["value"]["blockhash"], "send_url": self.cfg.public()["send"],
                   "max_retries": self.a.max_retries, **{x: p[x] for x in ("k", "price", "payer", "sig", "memo",
                                                                           "send_pos", "group")}, **by_k[p["k"]]}
            self.w.jsonl("sends.jsonl", row)
        self.w.event("burst", n=bno, series=series, client=t["client"], leader=win["leader"][:8],
                     start=win["start"], seen=seen_slot, est_fee=est, spent=spent,
                     errors=sum(1 for s in sent if s["err"]))
        self.done[win["leader"]] = self.done.get(win["leader"], 0) + 1
        self.last_burst_at = time.time()
        self.collectors.append(asyncio.create_task(self.collect(bno, win, probes, key, est)))

    async def collect(self, bno, win, probes, key, est):
        """After the window: where did each probe land. Stragglers are re-checked until the blockhash expires."""
        sigs = [p["sig"] for p in probes]
        end = win["start"] + win["slots"]
        landed = {}
        try:
            for wait_to in (end + 32, win["start"] + 170):
                await self.clock.wait_until(wait_to, timeout=240)
                if not self.live:
                    break
                st = (await self.rpc.call("getSignatureStatuses", [sigs, {"searchTransactionHistory": True}]))["value"]
                for p, s in zip(probes, st):
                    if s and p["sig"] not in landed:
                        landed[p["sig"]] = s["slot"]
                if len(landed) == len(sigs):
                    break
            slots = set(range(win["start"], end)) | set(landed.values())
            blocks = {s: await self.blocks.get(s) for s in sorted(slots)}
            fees = 0
            for p in probes:
                s = landed.get(p["sig"])
                row = {"run": self.run_id, "burst": bno, "sig": p["sig"], "k": p["k"], "landed_slot": s}
                if s is not None:
                    b = blocks[s]
                    hit = [x for x in b.get("txs", []) if x["sig"] == p["sig"]]
                    if hit:
                        x = hit[0]
                        fees += x["fee"] or 0
                        row.update(index=x["i"], n_block=len(b["txs"]), block_leader=b.get("leader"),
                                   fee=x["fee"], cu=x["cu"], ok=x["ok"],
                                   in_window=win["start"] <= s < end, slot_offset=s - win["start"])
                self.w.jsonl("landed.jsonl", row)
            win_blocks = [blocks[s] for s in range(win["start"], end)]
            self.w.event("collected", burst=bno, landed=len(landed), of=len(probes),
                         slots=",".join(str(s - win["start"]) for s in sorted(set(landed.values()))) or "-",
                         skipped=sum(1 for b in win_blocks if b.get("skipped")),
                         leader_ok=all(b.get("leader") in (None, win["leader"]) for b in win_blocks))
            if self.live:
                self.budget.settle(key, fees)
        except Exception as e:  # noqa: BLE001 - recorded; the ledger keeps the estimate as pending
            self.w.event("collect_error", burst=bno, err=repr(e))

    async def loop(self):
        deadline = time.time() + self.a.minutes * 60
        while time.time() < deadline:
            if all(self.done.get(pk, 0) >= self.a.reps for pk in self.targets):
                self.w.event("complete", bursts=self.burst_no)
                break
            cur = self.clock.slot
            upcoming = [w for w in chain.windows(self.by_slot, cur + self.a.prep_slots, cur + 3000)
                        if w["leader"] in self.targets and self.done.get(w["leader"], 0) < self.a.reps
                        and w["start"] not in self.skipped]
            if not upcoming:
                if cur + 3000 > max(self.by_slot):
                    _, _, nxt = await chain.schedule(self.rpc, {"absoluteSlot": max(self.by_slot) + 1, "slotIndex": 0,
                                                                "slotsInEpoch": 432000})
                    self.by_slot.update(nxt)
                await asyncio.sleep(2)
                continue
            win = upcoming[0]
            # give way to the target with the fewest bursts if two windows are close together
            close = [w for w in upcoming if w["start"] - win["start"] < 8]
            win = min(close, key=lambda w: self.done.get(w["leader"], 0))
            left = deadline - time.time()
            if (win["start"] - self.a.prep_slots - cur) * 0.4 > left:
                self.w.event("deadline", next_window_in_s=round((win["start"] - cur) * 0.4))
                break
            await self.clock.wait_until(win["start"] - self.a.prep_slots, timeout=max(30, left))
            # send endpoints rate-limit sendTransaction (public RPC: ~16 per 10 s); a burst too soon after the
            # previous one would be cut short, and the cut falls on the probes sent last
            # the burst itself fires when the window starts, (start - current slot) x ~0.4 s from now
            fire_at = time.time() + max(0, win["start"] - self.clock.slot) * 0.4
            short = self.a.min_burst_gap_s - (fire_at - self.last_burst_at)
            if short > 1.0:
                self.skipped.add(win["start"])
                self.w.event("skip_window", leader=win["leader"][:8], start=win["start"], too_soon_s=round(short, 1))
                continue
            if short > 0:
                await asyncio.sleep(short)
            try:
                await self.burst(win, self.next_series(win["leader"]))
            except CapExceeded as e:
                self.w.event("cap_stop", reason=str(e))
                break
        self.w.event("waiting_collectors", n=len([c for c in self.collectors if not c.done()]))
        await asyncio.gather(*self.collectors)

    async def run(self):
        try:
            await self.setup()
            await self.loop()
        finally:
            await self.clock.stop()
            await self.rpc.close()
            if self.send_rpc is not self.rpc:
                await self.send_rpc.close()
        print("run dir:", self.w.dir)

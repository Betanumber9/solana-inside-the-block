"""python -m itb <command>. Without --live nothing is ever sent."""
import argparse
import asyncio
import collections
import json
import pathlib
import time

from solders.pubkey import Pubkey

from . import chain, wallet
from .budget import Budget
from .config import Config
from .rpc import Rpc

HARD_CAP_SOL = 0.05


def common_live(p):
    p.add_argument("--live", action="store_true", help="really send transactions (default: dry-run)")
    p.add_argument("--cap-sol", type=float, default=HARD_CAP_SOL, help="spend cap for all live runs together")
    p.add_argument("--allow-cap-above", action="store_true", help="permit --cap-sol above %.2f" % HARD_CAP_SOL)


def check_cap(a):
    if a.cap_sol > HARD_CAP_SOL and not a.allow_cap_above:
        raise SystemExit("--cap-sol %.4f is above %.2f; add --allow-cap-above if you mean it" % (a.cap_sol, HARD_CAP_SOL))


async def cmd_landscape(cfg, a):
    rpc = Rpc(cfg.rpc_url, cfg.rpc_interval, heavy_interval=cfg.block_interval)
    land = await chain.landscape(rpc)
    await rpc.close()
    tot = sum(v["stake"] for v in land["validators"].values())
    agg = collections.defaultdict(lambda: [0, 0, collections.Counter(), 0, set()])
    for v in land["validators"].values():
        x = agg[v["client"]]
        x[0] += 1
        x[1] += v["stake"]
        x[2][v["version"]] += 1
        if v["tpu_elsewhere"]:
            x[3] += v["stake"]
            x[4].add(chain._host(v["tpu_quic"]))
    print("epoch %d slot %d" % (land["epoch"]["epoch"], land["epoch"]["absoluteSlot"]))
    print("%-20s %6s %7s %13s %6s  versions" % ("client", "nodes", "stake%", "tpu_elsewhere", "hosts"))
    for c, (n, s, ver, sx, hosts) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
        print("%-20s %6d %6.2f%% %12.0f%% %6d  %s" % (c, n, 100 * s / tot, 100 * sx / s, len(hosts),
                                                    ", ".join("%s x%d" % kv for kv in ver.most_common(3))))
    out = pathlib.Path(cfg.data_dir) / "landscape"
    out.mkdir(parents=True, exist_ok=True)
    f = out / ("%s.json" % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    f.write_text(json.dumps(land))
    print("saved", f)


async def cmd_balance(cfg, a):
    key = wallet.main_key(cfg.secrets_dir)
    if key is None:
        raise SystemExit("no key (secrets/keypair.json or ITB_SECRET_KEY)")
    subs = wallet.sub_keys(cfg.secrets_dir, a.n)
    rpc = Rpc(cfg.rpc_url, cfg.rpc_interval, heavy_interval=cfg.block_interval)
    keys = [key.pubkey()] + [k.pubkey() for k in subs]
    r = await rpc.call("getMultipleAccounts", [[str(k) for k in keys], {"encoding": "base64"}])
    await rpc.close()
    for i, (k, acc) in enumerate(zip(keys, r["value"])):
        print("%-5s %s %.6f SOL" % ("main" if i == 0 else "sub%d" % i, k, (acc or {}).get("lamports", 0) / 1e9))
    b = Budget(pathlib.Path(cfg.data_dir) / "budget.json", int(HARD_CAP_SOL * 1e9))
    print("ledger: confirmed %.6f SOL, pending %.6f SOL" % (b.state["confirmed"] / 1e9,
                                                           sum(b.state["pending"].values()) / 1e9))


async def _send_and_confirm(rpc, tx, label):
    res, err = await rpc.send_raw(wallet.b64(tx), max_retries=5)
    if err:
        raise SystemExit("%s failed: %s" % (label, err))
    sig = str(tx.signatures[0])
    for _ in range(60):
        await asyncio.sleep(1)
        st = (await rpc.call("getSignatureStatuses", [[sig]]))["value"][0]
        if st and st.get("confirmationStatus") in ("confirmed", "finalized"):
            if st.get("err"):
                raise SystemExit("%s landed with error %s (%s)" % (label, st["err"], sig))
            print("%s ok %s slot %d" % (label, sig, st["slot"]))
            return st["slot"]
    raise SystemExit("%s not confirmed in 60 s (%s)" % (label, sig))


async def cmd_fund(cfg, a):
    """Top every sub-wallet up to --sub-sol. Dry-run prints the plan."""
    check_cap(a)
    key = wallet.main_key(cfg.secrets_dir)
    if key is None:
        raise SystemExit("no key")
    subs = wallet.sub_keys(cfg.secrets_dir, a.n, create=a.live)
    if len(subs) < a.n:
        print("dry-run: would create %d sub-wallets in %s/subwallets.json" % (a.n - len(subs), cfg.secrets_dir))
    rpc = Rpc(cfg.rpc_url, cfg.rpc_interval, heavy_interval=cfg.block_interval)
    budget = Budget(pathlib.Path(cfg.data_dir) / "budget.json", int(a.cap_sol * 1e9))
    target = int(a.sub_sol * 1e9)
    for i, k in enumerate(subs):
        have = (await rpc.call("getBalance", [str(k.pubkey())]))["value"]
        need = target - have
        if need <= 0:
            print("sub%d has %.6f, skip" % (i + 1, have / 1e9))
            continue
        if not a.live:
            print("dry-run: would send %.6f SOL to sub%d %s" % (need / 1e9, i + 1, k.pubkey()))
            continue
        bh = (await rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}]))["value"]["blockhash"]
        tag = "fund/sub%d/%d" % (i + 1, int(time.time()))
        budget.reserve(tag, 5000)
        await _send_and_confirm(rpc, wallet.transfer_tx(key, k.pubkey(), need, wallet.to_hash(bh)), "fund sub%d" % (i + 1))
        budget.settle(tag, 5000)
    await rpc.close()


async def cmd_sweep(cfg, a):
    """Return everything in the sub-wallets to the main wallet."""
    key = wallet.main_key(cfg.secrets_dir)
    if key is None:
        raise SystemExit("no key")
    subs = wallet.sub_keys(cfg.secrets_dir, 64)
    rpc = Rpc(cfg.rpc_url, cfg.rpc_interval, heavy_interval=cfg.block_interval)
    budget = Budget(pathlib.Path(cfg.data_dir) / "budget.json", int(a.cap_sol * 1e9))
    for i, k in enumerate(subs):
        have = (await rpc.call("getBalance", [str(k.pubkey())]))["value"]
        if have <= 5000:
            continue
        if not a.live:
            print("dry-run: would return %.6f SOL from sub%d" % ((have - 5000) / 1e9, i + 1))
            continue
        bh = (await rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}]))["value"]["blockhash"]
        tag = "sweep/sub%d/%d" % (i + 1, int(time.time()))
        budget.reserve(tag, 5000)
        await _send_and_confirm(rpc, wallet.transfer_tx(k, key.pubkey(), have - 5000, wallet.to_hash(bh)),
                                "sweep sub%d" % (i + 1))
        budget.settle(tag, 5000)
    await rpc.close()


async def cmd_plan(cfg, a):
    """Targets and their next windows, from the live schedule. Read-only."""
    from .experiment import pick_targets
    rpc = Rpc(cfg.rpc_url, cfg.rpc_interval, heavy_interval=cfg.block_interval)
    land = await chain.landscape(rpc)
    _, _, by_slot = await chain.schedule(rpc, land["epoch"])
    await rpc.close()
    cur = land["epoch"]["absoluteSlot"]
    clients = set(a.clients.split(",")) if a.clients else None
    t = pick_targets(land, by_slot, cur, a.per_client, clients)
    soon = collections.defaultdict(list)
    for w in chain.windows(by_slot, cur, cur + int(a.minutes * 150)):
        if w["leader"] in t:
            soon[w["leader"]].append(w["start"])
    for pk, v in sorted(t.items(), key=lambda kv: kv[1]["client"]):
        first = soon[pk][:1]
        eta = "%.0fs" % ((first[0] - cur) * 0.4) if first else "-"
        print("%-18s %-14s %s  windows left %4d, next %s, in %d min: %d" % (
            v["client"], v["version"], pk, v["windows_left"], eta, a.minutes, len(soon[pk])))


async def cmd_run(cfg, a):
    check_cap(a)
    from .experiment import Experiment
    await Experiment(cfg, a).run()


def main():
    ap = argparse.ArgumentParser(prog="itb", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("clients", help="clients, versions, stake and TPU routing right now")
    p = sub.add_parser("plan", help="which validators would be measured and when")
    p.add_argument("--per-client", type=int, default=2)
    p.add_argument("--clients", default="")
    p.add_argument("--minutes", type=float, default=60)
    p = sub.add_parser("run", help="the experiment (dry-run unless --live)")
    common_live(p)
    p.add_argument("--per-client", type=int, default=2, help="validators per client")
    p.add_argument("--clients", default="", help="comma list of gossip clientIds (default: all known)")
    p.add_argument("--validators", default="", help="comma list of identities (overrides --per-client/--clients)")
    p.add_argument("--reps", type=int, default=6, help="bursts per validator")
    p.add_argument("--minutes", type=float, default=60, help="stop after this long")
    p.add_argument("--series", default=",".join(("ladder_shared", "ladder_indep", "equal_indep")))
    p.add_argument("--burst", type=int, default=8, help="probes per burst")
    p.add_argument("--cu-limit", type=int, default=6000)
    p.add_argument("--gap-ms", type=float, default=3, help="pause between sends in ladder series")
    p.add_argument("--gap-ms-equal", type=float, default=40, help="pause between sends in equal_indep")
    p.add_argument("--trigger-offset", type=int, default=0, help="send when this slot of the window is seen")
    p.add_argument("--trigger-delay-ms", type=float, default=0)
    p.add_argument("--prep-slots", type=int, default=10, help="start preparing this many slots before")
    p.add_argument("--max-retries", type=int, default=0, help="sendTransaction maxRetries (0 = one shot)")
    p.add_argument("--max-late-slots", type=int, default=2, help="abandon a burst if the trigger slot passed by more")
    p.add_argument("--min-burst-gap-s", type=float, default=10.0, help="skip windows closer than this to the last burst")
    p.add_argument("--seed", type=int, default=None)
    for name, fn in (("balance", "show wallet balances and ledger"), ("fund", "top up sub-wallets"),
                     ("sweep", "return sub-wallet funds to main")):
        p = sub.add_parser(name, help=fn)
        common_live(p)
        p.add_argument("--n", type=int, default=8)
        p.add_argument("--sub-sol", type=float, default=0.004)
    p = sub.add_parser("analyze", help="tables and figures from data/")
    p.add_argument("--runs", default="", help="comma list of run ids (default: all live runs)")
    p.add_argument("--include-dry", action="store_true")
    p.add_argument("--out", default="", help="subfolder of reports/ (e.g. one per run condition)")
    p = sub.add_parser("recollect", help="finish collection for a stopped run (statuses, blocks, ledger)")
    p.add_argument("--runs", default="", help="comma list of run ids (default: all live runs)")
    p.add_argument("--cap-sol", type=float, default=HARD_CAP_SOL)
    p = sub.add_parser("show", help="one burst as the chain has it (positions, prices, memos fetched from RPC)")
    p.add_argument("run")
    p.add_argument("burst", type=int)
    sub.add_parser("figure", help="reports/figure_distance.png from reports/pairs.csv (needs matplotlib)")
    sub.add_parser("selftest", help="offline unit tests (parsing, windows, probes, spend cap, verdict rules)")
    p = sub.add_parser("confirm", help="pre-registered tests of the confirmation runs (PREREGISTRATION.md)")
    p.add_argument("--runs", default="", help="comma list of run ids (default: live runs with split series)")
    p = sub.add_parser("verify", help="re-fetch blocks from RPC and check recorded positions")
    p.add_argument("--sample", type=int, default=50)
    a = ap.parse_args()
    cfg = Config()
    if a.cmd == "analyze":
        from .analyze import main as analyze_main
        return analyze_main(cfg, a)
    if a.cmd == "recollect":
        from .recollect import main as recollect_main
        return asyncio.run(recollect_main(cfg, a))
    if a.cmd == "show":
        from .show import main as show_main
        return show_main(cfg, a)
    if a.cmd == "figure":
        from .figure import main as figure_main
        return figure_main(cfg, a)
    if a.cmd == "selftest":
        import unittest
        suite = unittest.defaultTestLoader.loadTestsFromName("itb.tests.test_core")
        raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
    if a.cmd == "confirm":
        from .confirm import main as confirm_main
        return confirm_main(cfg, a)
    if a.cmd == "verify":
        from .verify import main as verify_main
        return asyncio.run(verify_main(cfg, a))
    fn = {"clients": cmd_landscape, "plan": cmd_plan, "run": cmd_run, "balance": cmd_balance,
          "fund": cmd_fund, "sweep": cmd_sweep}[a.cmd]
    asyncio.run(fn(cfg, a))


if __name__ == "__main__":
    main()

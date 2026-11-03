"""Finish collection for probes of a stopped run: statuses, blocks, landed rows, ledger settlement."""
import collections
import json
import pathlib

from .budget import Budget
from .experiment import BlockStore, Writer
from .rpc import Rpc


async def main(cfg, a):
    data = pathlib.Path(cfg.data_dir)
    rpc = Rpc(cfg.rpc_url, cfg.rpc_interval, heavy_interval=cfg.block_interval)
    blocks = BlockStore(cfg.data_dir, rpc)
    budget = Budget(data / "budget.json", int(a.cap_sol * 1e9))
    for d in sorted((data / "runs").glob("*_live")):
        if a.runs and d.name not in a.runs.split(","):
            continue
        w = Writer(d)
        sends = [json.loads(x) for x in (d / "sends.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        done = set()
        if (d / "landed.jsonl").exists():
            done = {json.loads(x)["sig"] for x in (d / "landed.jsonl").read_text(encoding="utf-8").splitlines() if x}
        todo = collections.defaultdict(list)
        for s in sends:
            if s["sig"] not in done:
                todo[s["burst"]].append(s)
        for bno, probes in sorted(todo.items()):
            st = (await rpc.call("getSignatureStatuses", [[p["sig"] for p in probes],
                                                          {"searchTransactionHistory": True}]))["value"]
            fees = 0
            for p, x in zip(probes, st):
                row = {"run": d.name, "burst": bno, "sig": p["sig"], "k": p["k"], "landed_slot": x and x["slot"],
                       "recollected": True}
                if x:
                    b = await blocks.get(x["slot"])
                    hit = [t for t in b.get("txs", []) if t["sig"] == p["sig"]]
                    if hit:
                        t = hit[0]
                        fees += t["fee"] or 0
                        end = p["window_start"] + p["window_slots"]
                        row.update(index=t["i"], n_block=len(b["txs"]), block_leader=b.get("leader"), fee=t["fee"],
                                   cu=t["cu"], ok=t["ok"], in_window=p["window_start"] <= x["slot"] < end,
                                   slot_offset=x["slot"] - p["window_start"])
                w.jsonl("landed.jsonl", row)
            for s in range(probes[0]["window_start"], probes[0]["window_start"] + probes[0]["window_slots"]):
                await blocks.get(s)
            key = "%s/b%d" % (d.name, bno)
            if key in budget.state["pending"]:
                budget.settle(key, fees)
            print("%s burst %d: %d/%d landed, fee %d" % (d.name, bno, sum(1 for x in st if x), len(probes), fees))
    await rpc.close()

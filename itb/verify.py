"""Check recorded positions against the chain, independently of data/blocks: for a random sample of landed probes,
fetch the block's signature list from RPC and compare slot, index and leader."""
import json
import pathlib
import random

from .rpc import Rpc


async def main(cfg, a):
    data = pathlib.Path(cfg.data_dir)
    rows = []
    for d in sorted((data / "runs").glob("*_live")):
        p = d / "landed.jsonl"
        if p.exists():
            rows += [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if r.get("index") is not None]
    if not rows:
        raise SystemExit("no landed probes in data/runs/*_live")
    sample = random.Random(1).sample(rows, min(a.sample, len(rows)))
    rpc = Rpc(cfg.rpc_url, cfg.rpc_interval, heavy_interval=cfg.block_interval)
    cache, bad = {}, 0
    for r in sample:
        s = r["landed_slot"]
        if s not in cache:
            cache[s] = await rpc.call("getBlock", [s, {"transactionDetails": "signatures", "rewards": True,
                                                       "maxSupportedTransactionVersion": 1}])
        b = cache[s]
        sigs = b["signatures"]
        leader = [x["pubkey"] for x in b.get("rewards") or [] if x.get("rewardType") == "Fee"]
        ok = r["sig"] in sigs and sigs.index(r["sig"]) == r["index"] and (leader[:1] == [r["block_leader"]])
        bad += not ok
        print("%s slot %d index %d/%d leader %s  %s" % (r["sig"][:16], s, r["index"], len(sigs),
                                                       (leader or ["?"])[0][:8], "OK" if ok else "MISMATCH"))
    await rpc.close()
    print("%d checked, %d mismatches" % (len(sample), bad))

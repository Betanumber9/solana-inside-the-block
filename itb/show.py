"""Show one burst as the chain has it: for every probe, its slot and position (getBlock), its price and memo
(getTransaction), and the block's leader (fee reward). Only the send order comes from our log
(data/runs/<run>/sends.jsonl); everything else is fetched from RPC, not from data/blocks."""
import asyncio
import json
import pathlib

import base58

from .chain import COMPUTE_BUDGET
from .rpc import Rpc


async def run(cfg, a):
    rows = [json.loads(x) for x in (pathlib.Path(cfg.data_dir) / "runs" / a.run / "sends.jsonl").read_text(
        encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if r["burst"] == a.burst]
    if not rows:
        raise SystemExit("no burst %d in run %s" % (a.burst, a.run))
    rpc = Rpc(cfg.rpc_url, cfg.rpc_interval, heavy_interval=cfg.block_interval)
    out, blocks = [], {}
    for r in rows:
        t = await rpc.call("getTransaction", [r["sig"], {"encoding": "json", "maxSupportedTransactionVersion": 1,
                                                         "commitment": "confirmed"}])
        if t is None:
            out.append((None, None, r, None, None))
            continue
        m = t["transaction"]["message"]
        keys = m["accountKeys"]
        price, memo = 0, ""
        for ix in m["instructions"]:
            data = base58.b58decode(ix["data"])
            if keys[ix["programIdIndex"]] == COMPUTE_BUDGET and data[:1] == b"\x03":
                price = int.from_bytes(data[1:9], "little")
            elif keys[ix["programIdIndex"]].startswith("Memo"):
                memo = data.decode()
        slot = t["slot"]
        if slot not in blocks:
            blocks[slot] = await rpc.call("getBlock", [slot, {"transactionDetails": "signatures", "rewards": True,
                                                              "maxSupportedTransactionVersion": 1}])
        out.append((slot, blocks[slot]["signatures"].index(r["sig"]), r, price, memo))
    await rpc.close()
    r0 = rows[0]
    print("run %s burst %d: %s, target %s (%s), window slots %d-%d" % (
        a.run, a.burst, r0["series"], r0["leader"], r0["client"], r0["window_start"],
        r0["window_start"] + r0["window_slots"] - 1))
    print("%-10s %-7s %-12s %-9s %-6s %-10s %-9s %s" % ("slot", "index", "leader", "price*", "group", "sent as #",
                                                        "memo", "signature"))
    for slot, idx, r, price, memo in sorted(out, key=lambda x: (x[0] is None, x[0] or 0, x[1] or 0)):
        leader = [x["pubkey"] for x in blocks.get(slot, {}).get("rewards") or [] if x.get("rewardType") == "Fee"]
        print("%-10s %-7s %-12s %-9s %-6s %-10s %-9s %s" % (
            slot, idx, (leader or ["-"])[0][:10], price, r.get("group") or "-", r["send_pos"] + 1, memo, r["sig"]))
    print("* micro-lamports per compute unit, read from the transaction on chain. "
          "'sent as #' is from our send log; the rest is from RPC.")


def main(cfg, a):
    asyncio.run(run(cfg, a))

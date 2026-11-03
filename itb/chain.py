"""Read-only chain data: who runs which client, the leader schedule, and blocks reduced to per-transaction rows."""
import base58

from .rpc import RpcError

COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"
VOTE = "Vote111111111111111111111111111111111111111"
# Jito tip accounts (tips are a separate mechanism; such transactions are flagged and left out of background stats)
JITO_TIP = {
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5", "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY", "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh", "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL", "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
}
SLOTS_PER_WINDOW = 4


def _host(addr):
    return addr.rsplit(":", 1)[0] if addr else None


async def landscape(rpc):
    """Every staked node with its self-reported client (gossip clientId), version and stake."""
    epoch = await rpc.call("getEpochInfo", [{"commitment": "confirmed"}])
    nodes = await rpc.call("getClusterNodes")
    votes = await rpc.call("getVoteAccounts", [{"commitment": "confirmed"}])
    stake = {v["nodePubkey"]: v["activatedStake"] for v in votes["current"] + votes["delinquent"]}
    vote_of = {v["nodePubkey"]: v["votePubkey"] for v in votes["current"] + votes["delinquent"]}
    out = {}
    for n in nodes:
        pk = n["pubkey"]
        if pk not in stake:
            continue
        out[pk] = {"identity": pk, "vote": vote_of[pk], "client": n.get("clientId") or "unknown",
                   "version": n.get("version"), "feature_set": n.get("featureSet"),
                   "stake": stake[pk], "tpu_quic": n.get("tpuQuic"), "gossip": n.get("gossip"),
                   # TPU on another host than gossip = transactions enter through a relay/proxy in front of the leader
                   "tpu_elsewhere": _host(n.get("tpuQuic")) not in (None, _host(n.get("gossip")))}
    return {"epoch": epoch, "validators": out}


async def schedule(rpc, epoch_info):
    """slot -> leader identity for the epoch that contains epoch_info['absoluteSlot']."""
    first = epoch_info["absoluteSlot"] - epoch_info["slotIndex"]
    sched = await rpc.call("getLeaderSchedule", [epoch_info["absoluteSlot"], {"commitment": "confirmed"}])
    by_slot = {}
    for leader, idxs in sched.items():
        for i in idxs:
            by_slot[first + i] = leader
    return first, first + epoch_info["slotsInEpoch"] - 1, by_slot


def windows(by_slot, start, end):
    """Leader windows (runs of consecutive slots with the same leader) that begin in [start, end]."""
    out = []
    s = start
    while s <= end and s in by_slot:
        if by_slot.get(s - 1) == by_slot[s]:
            s += 1
            continue
        leader = by_slot[s]
        n = 1
        while by_slot.get(s + n) == leader:
            n += 1
        out.append({"start": s, "slots": n, "leader": leader})
        s += n
    return out


def _cb(data_b58):
    d = base58.b58decode(data_b58)
    if d[:1] == b"\x02" and len(d) >= 5:
        return "limit", int.from_bytes(d[1:5], "little")
    if d[:1] == b"\x03" and len(d) >= 9:
        return "price", int.from_bytes(d[1:9], "little")
    return None, None


def tx_row(i, t):
    """One transaction of a json-encoded block as a flat row (position i in the block)."""
    m = t["transaction"]["message"]
    meta = t["meta"] or {}
    la = meta.get("loadedAddresses") or {}
    keys = m["accountKeys"] + la.get("writable", []) + la.get("readonly", [])
    progs, limit, price = set(), None, 0
    for ix in m["instructions"]:
        p = keys[ix["programIdIndex"]]
        progs.add(p)
        if p == COMPUTE_BUDGET:
            kind, val = _cb(ix["data"])
            if kind == "limit":
                limit = val
            elif kind == "price":
                price = val
    cfg = m.get("transactionConfig") or {}  # v1 transactions carry limit/fee here instead of instructions
    if cfg:
        limit = cfg.get("computeUnitLimit", limit)
    h = m["header"]
    n_sig = h["numRequiredSignatures"]
    n_static = len(m["accountKeys"])
    writable = (n_sig - h["numReadonlySignedAccounts"]) + (n_static - n_sig - h["numReadonlyUnsignedAccounts"]) \
        + len(la.get("writable", []))
    return {
        "i": i, "sig": t["transaction"]["signatures"][0], "payer": m["accountKeys"][0],
        "fee": meta.get("fee"), "cu": meta.get("computeUnitsConsumed"), "cu_limit": limit, "cu_price": price,
        # costUnits = what the leader's cost model charged; priority fee = fee minus 5000 per signature
        "cost": meta.get("costUnits"), "n_sig": n_sig,
        "prio_fee": (meta.get("fee") or 0) - 5000 * n_sig,
        "ok": meta.get("err") is None, "vote": VOTE in progs, "jito_tip": bool(JITO_TIP & set(keys)),
        "n_ix": len(m["instructions"]), "n_writable": writable, "version": t.get("version"),
    }


async def block(rpc, slot, commitment="confirmed"):
    """Block reduced to rows; None if the slot was skipped. Leader is read from the block's fee reward."""
    try:
        b = await rpc.call("getBlock", [slot, {"encoding": "json", "transactionDetails": "full", "rewards": True,
                                               "maxSupportedTransactionVersion": 1, "commitment": commitment}])
    except RpcError as e:
        if "-32007" in str(e) or "-32009" in str(e) or "skipped" in str(e).lower():
            return None
        raise
    if b is None:
        return None
    fee_reward = [r["pubkey"] for r in (b.get("rewards") or []) if r.get("rewardType") == "Fee"]
    return {"slot": slot, "blockhash": b["blockhash"], "parent_slot": b["parentSlot"], "block_time": b["blockTime"],
            "block_height": b.get("blockHeight"), "leader": fee_reward[0] if fee_reward else None,
            "txs": [tx_row(i, t) for i, t in enumerate(b["transactions"])]}

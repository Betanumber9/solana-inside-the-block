"""From data/ to reports/: landing, pairwise order of our own probes, and the background view of whole blocks.

Unit of evidence: an ordered pair of our probes that landed in the same block. For each pair we ask two questions:
did the higher-priced probe come first (fee rule), did the earlier-sent probe come first (arrival rule)?
Pairs from one burst are not independent, so confidence intervals come from resampling whole bursts.
"""
import collections
import csv
import gzip
import json
import pathlib
import random

RULE_THRESHOLD = 0.75  # pre-registered: a rule "holds" when the lower 95% bound of its share is above this
BOOT = 2000


def load_jsonl(p):
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def load_block(data, slot):
    p = data / "blocks" / ("%d.json.gz" % slot)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def runs(data, a):
    out = []
    for d in sorted((data / "runs").glob("*")):
        if a.runs and d.name not in a.runs.split(","):
            continue
        if d.name.endswith("_dry") and not (a.include_dry or a.runs):
            continue
        out.append(d)
    return out


def boot_ci(groups, rng):
    """groups: list of (hits, n) per burst. Share and 95% CI by resampling bursts."""
    groups = [g for g in groups if g[1]]
    if not groups:
        return None, None, None, 0
    h = sum(g[0] for g in groups)
    n = sum(g[1] for g in groups)
    stats = []
    for _ in range(BOOT):
        s = [groups[rng.randrange(len(groups))] for _ in groups]
        nn = sum(g[1] for g in s)
        stats.append(sum(g[0] for g in s) / nn)
    stats.sort()
    return h / n, stats[int(0.025 * BOOT)], stats[int(0.975 * BOOT) - 1], n


def verdict(fee, send, duel):
    """fee/send/duel: (share, lo, hi, n) or None. Fixed before the main live run (03.11.2026 18:00 Kyiv).
    Which factor dominates is read from the duels (pairs where the two rules disagree); whether each factor
    matters at all, from fee_first / sent_first; "strict" when the lower bound clears RULE_THRESHOLD."""
    def above(x, t=0.5):
        return bool(x and x[0] is not None and x[1] > t)

    def below(x, t=0.5):
        return bool(x and x[0] is not None and x[2] < t)

    def covers(x):
        return bool(x and x[0] is not None and x[1] <= 0.5 <= x[2])

    if above(duel):
        v = "fee beats send order"
    elif below(duel):
        v = "send order beats fee"
    elif above(fee) and above(send):
        v = "both matter, neither dominates"
    elif above(fee):
        v = "fee matters"
    elif above(send):
        v = "send order matters"
    elif (fee is None or covers(fee)) and covers(send):
        return "no rule visible (noise)"
    else:
        return "inconclusive"
    strict = [n for n, x in (("fee", fee), ("send", send)) if above(x, RULE_THRESHOLD)]
    return v + ("; strict: " + ",".join(strict) if strict else "")


def fmt(x):
    if not x or x[0] is None:
        return "-"
    return "%.2f [%.2f, %.2f] n=%d" % x


def spearman(x, y):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for q in range(i, j + 1):
                r[order[q]] = (i + j) / 2
            i = j + 1
        return r
    if len(x) < 3:
        return None
    rx, ry = rank(x), rank(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None


def main(cfg, a):
    data = pathlib.Path(cfg.data_dir)
    out = pathlib.Path("reports") / (a.out or "")
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    sends, landed, metas = [], {}, {}
    for d in runs(data, a):
        metas[d.name] = json.loads((d / "meta.json").read_text())
        sends += load_jsonl(d / "sends.jsonl")
        for r in load_jsonl(d / "landed.jsonl"):
            landed[r["sig"]] = r
    if not sends:
        raise SystemExit("no runs found in %s/runs (dry runs need --include-dry)" % data)
    for s in sends:
        s.update({k: v for k, v in landed.get(s["sig"], {}).items() if k not in s})

    # --- landing -------------------------------------------------------------------------------------------------
    per_val = collections.defaultdict(list)
    for s in sends:
        per_val[(s["client"], s["leader"])].append(s)
    land_rows = []
    for (client, leader), lst in sorted(per_val.items()):
        n = len(lst)
        got = [s for s in lst if s.get("index") is not None]
        inw = [s for s in got if s.get("in_window")]
        offs = collections.Counter(s["slot_offset"] for s in got)
        bursts = collections.defaultdict(set)
        for s in got:
            bursts[s["burst"]].add(s["landed_slot"])
        land_rows.append({"client": client, "validator": leader, "version": lst[0]["version"],
                          "bursts": len({s["burst"] for s in lst}), "probes": n, "landed": len(got),
                          "in_window": len(inw), "one_block_bursts": sum(1 for v in bursts.values() if len(v) == 1),
                          "slot_offsets": " ".join("%+d:%d" % kv for kv in sorted(offs.items()))})
    with open(out / "landing.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(land_rows[0]))
        w.writeheader()
        w.writerows(land_rows)

    # --- pairs of our own probes in one block ------------------------------------------------------------------
    by_burst_slot = collections.defaultdict(list)
    for s in sends:
        if s.get("index") is not None and s.get("in_window"):
            by_burst_slot[(s["run"], s["burst"], s["landed_slot"])].append(s)
    pair_rows = []
    groups = collections.defaultdict(lambda: {"fee": collections.defaultdict(lambda: [0, 0]),
                                              "send": collections.defaultdict(lambda: [0, 0]),
                                              "duel": collections.defaultdict(lambda: [0, 0])})
    for (run, burst, slot), lst in by_burst_slot.items():
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                x, y = lst[i], lst[j]
                first, second = (x, y) if x["index"] < y["index"] else (y, x)
                row = {"run": run, "burst": burst, "slot": slot, "client": x["client"], "validator": x["leader"],
                       "series": x["series"], "first_sig": first["sig"], "second_sig": second["sig"],
                       "first_index": first["index"], "second_index": second["index"],
                       "first_price": first["price"], "second_price": second["price"],
                       "first_send_pos": first["send_pos"], "second_send_pos": second["send_pos"],
                       "send_gap_ms": round((second["dispatch_ns"] - first["dispatch_ns"]) / 1e6, 2)}
                pair_rows.append(row)
                # first slot of the window: the leader's block had not started when the probes were sent
                # (only possible with an early trigger), so they could wait in its buffer together
                where = "@first_slot" if slot == x["window_start"] else "@later_slot"
                trig = "@trigger%+d" % (x["trigger_slot"] - x["window_start"])
                # exploratory (chosen after looking at the data): how far apart the two probes sit in the block.
                # Close pairs were most likely picked up by the scheduler in the same pass.
                d = abs(x["index"] - y["index"])
                dist = "@dist0-3" if d <= 3 else "@dist4-15" if d <= 15 else "@dist16-63" if d <= 63 else "@dist64+"
                near = "@near(<=15)" if d <= 15 else "@far(>15)"
                for key in ((x["client"], x["leader"], x["series"]), (x["client"], "*", x["series"]),
                            (x["client"], "*", "*"), (x["client"], "*", where), ("*", "*", where),
                            (x["client"], "*", trig), ("*", "*", x["series"]), ("*", "*", "*"),
                            (x["client"], "*", near), ("*", "*", near), (x["client"], "*", dist), ("*", "*", dist),
                            (x["client"], x["leader"], near), (x["client"], x["leader"], "*"),
                            (x["client"], "*", "@path_" + x.get("path", "rpc")),
                            ("*", "*", "@path_" + x.get("path", "rpc"))):
                    g = groups[key]
                    if first["price"] != second["price"]:
                        c = g["fee"][(run, burst)]
                        c[0] += first["price"] > second["price"]
                        c[1] += 1
                    c = g["send"][(run, burst)]
                    c[0] += first["send_pos"] < second["send_pos"]
                    c[1] += 1
                    # duel: the higher price was sent later, so the two rules predict opposite orders
                    hi, lo = (x, y) if x["price"] > y["price"] else (y, x)
                    if x["price"] != y["price"] and hi["send_pos"] > lo["send_pos"]:
                        c = g["duel"][(run, burst)]
                        c[0] += first is hi
                        c[1] += 1
    if pair_rows:
        with open(out / "pairs.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(pair_rows[0]))
            w.writeheader()
            w.writerows(pair_rows)
    rule_rows = []
    for key in sorted(groups):
        g = groups[key]
        fee = boot_ci(list(g["fee"].values()), rng) if g["fee"] else None
        send = boot_ci(list(g["send"].values()), rng)
        duel = boot_ci(list(g["duel"].values()), rng) if g["duel"] else None
        rule_rows.append({"client": key[0], "validator": key[1], "series": key[2],
                          "bursts": len(g["send"]), "fee_first": fmt(fee), "sent_first": fmt(send),
                          "fee_wins_duel": fmt(duel),
                          "verdict": verdict(fee, send, duel)})

    # --- background: every other transaction in the blocks we touched --------------------------------------------
    leaders = {s["leader"]: s["client"] for s in sends}
    slots = {s["landed_slot"] for s in sends if s.get("landed_slot")} | \
        {s["window_start"] + k for s in sends for k in range(s["window_slots"])}
    bg = collections.defaultdict(list)
    for slot in sorted(slots):
        b = load_block(data, slot)
        if not b or b.get("skipped") or b.get("leader") not in leaders:
            continue
        txs = [t for t in b["txs"] if not t["vote"] and not t["jito_tip"] and t.get("cost")]
        if len(txs) < 20:
            continue
        rho = spearman([t["i"] for t in txs], [t["prio_fee"] / t["cost"] for t in txs])
        paying = [t for t in txs if t["prio_fee"] > 0]
        rho_paying = spearman([t["i"] for t in paying], [t["prio_fee"] / t["cost"] for t in paying])
        # neighbours: among transactions that set a compute-unit price, is each one priced at least as high as the
        # next? Sorted batches push this above 0.5; arrival order alone leaves it near 0.5. Ties are skipped.
        priced = [t["cu_price"] for t in txs if t["cu_price"]]
        steps = [(x > y) for x, y in zip(priced, priced[1:]) if x != y]
        bg[leaders[b["leader"]]].append((rho, rho_paying, len(b["txs"]), sum(steps), len(steps)))
    bg_rows = []
    for client, lst in sorted(bg.items()):
        r = sorted(x[0] for x in lst if x[0] is not None)
        rp = sorted(x[1] for x in lst if x[1] is not None)
        med = lambda v: v[len(v) // 2] if v else None
        bg_rows.append({"client": client, "blocks": len(lst), "median_rho_all": med(r),
                        "median_rho_paying": med(rp), "share_rho_below_-0.3": sum(1 for v in r if v < -0.3) / len(r),
                        "neighbours_desc": boot_ci([(x[3], x[4]) for x in lst], rng)})

    # --- summary.md --------------------------------------------------------------------------------------------
    L = ["# Results (generated by `python -m itb analyze`)", "",
         "Runs: " + ", ".join("%s (%s, epoch %s)" % (k, m["started_utc"], m["epoch"]["epoch"]) for k, m in metas.items()),
         "", "## Landing", "", "| client | validator | version | bursts | probes | landed | in window | one-block bursts "
         "| slot offsets |", "|---|---|---|---|---|---|---|---|---|"]
    for r in land_rows:
        L.append("| %(client)s | `%(validator)s` | %(version)s | %(bursts)d | %(probes)d | %(landed)d | %(in_window)d "
                 "| %(one_block_bursts)d | %(slot_offsets)s |" % r)
    L += ["", "## Our probes, pairs in the same block",
          "", "`fee_first`: share of pairs (different price) where the higher price sits earlier. `sent_first`: share "
          "of pairs where the probe sent earlier sits earlier. `fee_wins_duel`: only pairs where the higher price was "
          "sent later (the two rules disagree); share where the higher price still sits earlier. 95%% CI by "
          "resampling bursts. Rule holds if the lower bound > %.2f; no rule if both intervals cover 0.5."
          % RULE_THRESHOLD, "",
          "| client | validator | series | bursts | fee_first | sent_first | fee_wins_duel | verdict |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rule_rows:
        v = r["validator"] if r["validator"] == "*" else "`%s`" % r["validator"][:8]
        L.append("| %s | %s | %s | %d | %s | %s | %s | %s |" % (r["client"], v, r["series"], r["bursts"],
                                                              r["fee_first"], r["sent_first"], r["fee_wins_duel"],
                                                              r["verdict"]))
    L += ["", "## Background: all other transactions of the same blocks (supplementary)", "",
          "Spearman rho between position in block and priority fee per cost unit (Jito tip transactions excluded). "
          "Strict priority order would give rho near -1. `neighbours sorted`: among transactions that set a "
          "compute-unit price, the share of neighbouring pairs where the earlier one pays more (ties skipped; 0.5 = no "
          "local sorting; 95% CI by resampling blocks).", "",
          "| client | blocks | median rho (all) | median rho (fee > 0) | share of blocks rho < -0.3 | neighbours sorted |",
          "|---|---|---|---|---|---|"]
    for r in bg_rows:
        f2 = lambda v: "-" if v is None else "%+.2f" % v
        L.append("| %s | %d | %s | %s | %.2f | %s |" % (r["client"], r["blocks"], f2(r["median_rho_all"]),
                                                       f2(r["median_rho_paying"]), r["share_rho_below_-0.3"],
                                                       fmt(r["neighbours_desc"]).rsplit(" n=", 1)[0]))
    (out / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    with open(out / "rules.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rule_rows[0]) if rule_rows else ["client"])
        w.writeheader()
        w.writerows(rule_rows)
    print("\n".join(L))

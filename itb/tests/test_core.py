"""Offline tests: block parsing, schedule windows, probes, spend cap, verdict rules. `python -m itb selftest`."""
import random
import tempfile
import unittest

import base58
from solders.hash import Hash
from solders.keypair import Keypair

from itb import analyze, chain, wallet
from itb.budget import Budget, CapExceeded


def cb_data(kind, value):
    if kind == "limit":
        return base58.b58encode(b"\x02" + value.to_bytes(4, "little")).decode()
    return base58.b58encode(b"\x03" + value.to_bytes(8, "little")).decode()


def json_tx(keys, ixs, header=(1, 0, 1), version="legacy", fee=5000, cost=None, config=None, err=None):
    msg = {"accountKeys": keys, "instructions": ixs,
           "header": {"numRequiredSignatures": header[0], "numReadonlySignedAccounts": header[1],
                      "numReadonlyUnsignedAccounts": header[2]}}
    if config:
        msg["transactionConfig"] = config
    return {"transaction": {"signatures": ["sig%d" % len(ixs)], "message": msg}, "version": version,
            "meta": {"fee": fee, "computeUnitsConsumed": 100, "costUnits": cost, "err": err, "loadedAddresses": {}}}


class TxRow(unittest.TestCase):
    def test_compute_budget_instructions(self):
        keys = ["Payer1111111111111111111111111111111111111", chain.COMPUTE_BUDGET]
        t = json_tx(keys, [{"programIdIndex": 1, "data": cb_data("limit", 6000), "accounts": []},
                           {"programIdIndex": 1, "data": cb_data("price", 300000), "accounts": []}],
                    fee=6800, cost=23000)
        r = chain.tx_row(5, t)
        self.assertEqual((r["i"], r["cu_limit"], r["cu_price"]), (5, 6000, 300000))
        self.assertEqual(r["prio_fee"], 1800)
        self.assertEqual(r["n_writable"], 1)
        self.assertFalse(r["jito_tip"])

    def test_v1_transaction_config(self):
        # version 1 transactions carry the limit in transactionConfig, not in instructions
        keys = ["Payer1111111111111111111111111111111111111", "Prog111111111111111111111111111111111111111"]
        t = json_tx(keys, [{"programIdIndex": 1, "data": "", "accounts": [0]}], version=1, fee=5324,
                    config={"computeUnitLimit": 24, "priorityFee": 324})
        r = chain.tx_row(0, t)
        self.assertEqual(r["cu_limit"], 24)
        self.assertEqual(r["prio_fee"], 324)

    def test_jito_tip_flag(self):
        tip = sorted(chain.JITO_TIP)[0]
        keys = ["Payer1111111111111111111111111111111111111", tip, "11111111111111111111111111111111"]
        r = chain.tx_row(0, json_tx(keys, [{"programIdIndex": 2, "data": "", "accounts": [0, 1]}]))
        self.assertTrue(r["jito_tip"])


class Windows(unittest.TestCase):
    def test_runs_of_one_leader(self):
        by_slot = {s: "A" for s in range(100, 108)}
        by_slot.update({s: "B" for s in range(108, 112)})
        w = chain.windows(by_slot, 102, 111)
        # a window that started before `start` is skipped; consecutive slots of one leader merge
        self.assertEqual(w, [{"start": 108, "slots": 4, "leader": "B"}])
        self.assertEqual(chain.windows(by_slot, 100, 111)[0], {"start": 100, "slots": 8, "leader": "A"})


class Probe(unittest.TestCase):
    def test_memo_fixed_length(self):
        self.assertEqual({len(wallet.memo_text(b, k)) for b in (1, 99999, 123456) for k in range(16)}, {9})

    def test_fee_estimate(self):
        self.assertEqual(wallet.fee_estimate(6000, 3_000_000), 5000 + 18000)
        self.assertEqual(wallet.fee_estimate(6000, 1), 5001)  # rounds up

    def test_only_payer_is_writable(self):
        kp = Keypair()
        tx = wallet.probe_tx(kp, Hash.default(), 6000, 1000, "itb000011")
        h = tx.message.header
        writable_signed = h.num_required_signatures - h.num_readonly_signed_accounts
        writable_unsigned = len(tx.message.account_keys) - h.num_required_signatures - h.num_readonly_unsigned_accounts
        self.assertEqual((writable_signed, writable_unsigned), (1, 0))
        self.assertEqual(tx.message.account_keys[0], kp.pubkey())

    def test_key_parsing(self):
        kp = Keypair()
        self.assertEqual(wallet._parse_key("﻿" + str(kp) + "\n").pubkey(), kp.pubkey())
        self.assertEqual(wallet._parse_key(str(list(bytes(kp)))).pubkey(), kp.pubkey())


class SpendCap(unittest.TestCase):
    def test_ledger_and_balance(self):
        with tempfile.TemporaryDirectory() as d:
            b = Budget(d + "/budget.json", 100_000)
            b.reserve("b1", 60_000)
            with self.assertRaises(CapExceeded):
                b.reserve("b2", 50_000)  # 60k open + 50k > cap
            b.settle("b1", 40_000)
            b.reserve("b2", 50_000)  # 40k confirmed + 50k fits
            b.check_balance(1_000_000)
            b.check_balance(950_000)
            with self.assertRaises(CapExceeded):
                b.check_balance(850_000)  # 150k gone > cap
            # the ledger survives a restart
            self.assertEqual(Budget(d + "/budget.json", 100_000).committed, 90_000)


class Verdict(unittest.TestCase):
    def test_known_answers(self):
        rng = random.Random(1)
        strong = [(8, 8)] * 30          # every pair follows the rule
        coin = [(4, 8), (3, 8), (5, 8), (4, 8)] * 10
        against = [(1, 8)] * 30
        fee, send = analyze.boot_ci(strong, rng), analyze.boot_ci(coin, rng)
        self.assertEqual(analyze.verdict(fee, send, analyze.boot_ci(strong, rng)),
                         "fee beats send order; strict: fee")
        self.assertEqual(analyze.verdict(send, fee, analyze.boot_ci(against, rng)),
                         "send order beats fee; strict: send")
        self.assertEqual(analyze.verdict(analyze.boot_ci(coin, rng), analyze.boot_ci(coin, rng), None),
                         "no rule visible (noise)")

    def test_bootstrap_interval_contains_share(self):
        share, lo, hi, n = analyze.boot_ci([(3, 4), (1, 4), (2, 4)], random.Random(2))
        self.assertEqual((share, n), (0.5, 12))
        self.assertTrue(lo <= share <= hi)


if __name__ == "__main__":
    unittest.main()

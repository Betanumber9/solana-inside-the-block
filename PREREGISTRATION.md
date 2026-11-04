# Pre-registration: confirmation runs

Written on 3 November 2026, after the main runs and before any confirmation run. The commit that adds this file is
pushed to GitHub before the first confirmation transaction is sent. The push time and the slot numbers in the run data
show the order.

## Why

The main runs suggested a model, but it came from an exploratory split: pairs close together in the block against
pairs far apart. Distance in the block is an outcome, not something we controlled. Here we control the timing
directly and say in advance what the model predicts.

**Model.** A leader, or the BAM/Harmonic front end in front of it, takes in transactions in batches in arrival order
and sorts each batch by priority fee. Validators that take transactions on their own TPU showed no fee sorting in the
main runs.

## Design

- **Series `split0` and `split120`**, alternating per validator. All 8 probes have the same fee payer (the main
  wallet).
  - Group A is the 4 cheapest prices of the ladder (1 000, 3 000, 10 000, 30 000 micro-lamports/CU). It is sent
    first, in random order.
  - Group B is the 4 dearest (100 000, 300 000, 1 000 000, 3 000 000). It is sent after A, in random order.
  - Between the last A send and the first B send we wait 0 ms (`split0`) or 120 ms (`split120`). Inside a group the
    spacing is the same as in the ladder series.
- **Validators:** the same 16 identities as in the main runs (`--validators`). Classes are fixed by where each one
  announces its TPU at run start: `front_end` if the TPU is on another host than gossip, `own_tpu` otherwise.
  Firedancer `9eGrDohd…` is also tested on its own.
- **Run settings:** `--trigger-offset -2`, `--reps 8` (4 bursts per gap per validator), at most 90 minutes, and the
  same spend cap ledger (total for the whole project stays ≤ 0.05 SOL).
- **Pairs used:** probes that landed in the same block inside the target's window. 95% intervals come from resampling
  bursts (2 000 draws). The code is `itb/confirm.py`, committed together with this file.

## Predictions

| test | prediction | counted as holding if |
|---|---|---|
| P1 | front_end: inside a group, the higher price sits earlier | lower bound > 0.5 |
| P2 | front_end: at 120 ms, group A (cheaper, sent first) sits earlier than group B | lower bound > 0.5 |
| P3 | front_end: the A-earlier share is higher at 120 ms than at 0 ms | lower bound of the difference > 0 |
| P4 | own_tpu: inside a group, no fee sorting | the interval contains 0.5 |
| P5 | own_tpu: at 120 ms, group A sits earlier | lower bound > 0.5 |
| P6 | Firedancer 9eGr: inside a group, the higher price sits earlier | lower bound > 0.6 |

- A failed prediction is reported as failed. We will not change the tests, the classes or the thresholds after
  seeing the confirmation data.
- If a network outage cuts a run short, one more run with identical settings may be added to reach the reps target.
  That is the only allowed addition, and it would be listed in REPORT.md.

---

# Addendum (4 November 2026): RPC path against the direct TPU path

Written and pushed before the path run. A short live test on 4 November, run `20261104T062417Z_live` with 2 bursts,
only checked that the direct path works. It is reported but not used for the tests below.

- **New option `--path alternate`.** The direct path sends over QUIC straight to the TPU address the leader announces
  in gossip (`itb/tpu.py`, an unstaked client with a self-signed certificate, one stream per transaction). The RPC
  path uses `sendTransaction` on the public RPC as before. The paths alternate per validator: its 1st, 3rd, … burst
  goes direct, and its 2nd, 4th, … burst goes through RPC.
- **Bursts.** 4 probes (1 000 / 10 000 / 100 000 / 1 000 000 micro-lamports/CU), one fee payer (`ladder_shared`),
  `--trigger-offset -2`, the same 16 validators, `--reps 12` (6 per path), at most 120 minutes. The spend cap stays at
  0.05 SOL in total.
- **Questions and predictions.** Same pair definitions as in the main runs, 95% bootstrap over bursts.

| test | prediction | counted as holding if |
|---|---|---|
| P7 | own_tpu: the send order survives at least as well on the direct path as through RPC (sent_first, tpu minus rpc) | the point estimate is ≥ 0, and the interval is reported |
| P8 | front_end: fee sorting of close pairs (≤15 positions) does not depend on the path | the interval of the difference (tpu minus rpc) contains 0 |

- These two are weak tests: about 6 bursts per validator and path, 6 pairs per burst. We say so in advance.
- Landing delay (slot offset) per path is reported as a plain description. There is no prediction for it.

---

# Addendum 2 (4 November 2026): the two Firedancer nodes

Written and pushed before the run. In the path run, Firedancer `JD549Hsb…` put the higher fee first in only 0.24 of 33
direct pairs, and its twin `9eGrDohd…` did so in 1.00 of 36. Both run 26.10.5. That came from 6 bursts each, so we
repeat it on fresh data.

- **Setup.** `--path tpu`, 4 probes with one fee payer (`ladder_shared`, 1 000 / 10 000 / 100 000 / 1 000 000),
  `--trigger-offset -2`, `--validators` set to only these two nodes, `--reps 15`, at most 75 minutes. The total spend
  for the project stays under 0.05 SOL.
- **Analysis.** `itb firedancer`: pairs of probes in the same block inside the window, share where the higher price
  sits earlier, 95% bootstrap over bursts.

| test | prediction | counted as holding if |
|---|---|---|
| P9 | `JD549Hsb…` puts the higher fee first less than half the time | upper bound of the interval < 0.5 |
| P10 | `9eGrDohd…` puts the higher fee first | lower bound of the interval > 0.8 |

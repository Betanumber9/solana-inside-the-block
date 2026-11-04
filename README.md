# Inside the Block: how Solana leaders order transactions

*From the author.* I ran every measurement here on 3 and 4 November 2026 from my laptop in Ukraine, paying the fees
from my own wallet [`2Kqf4idi…Hfw8`](https://solscan.io/account/2Kqf4idiM5GJaXbhhcYppZ1cCoRFDsCutS81ntmkHfw8). My
home internet dropped during one run. One burst went out ten minutes late because of it, and that is written up in the
report together with everything else that went wrong. Before each follow-up run I pushed my predictions to this repo,
so you can check that I did not fit them to the data afterwards. Two of the eight did not hold, and I left them that
way. One of them changed my main conclusion, and the report says how.

This repo answers one question for a set of mainnet validators running different clients: when our own
transactions land in a validator's block, what decides which one comes first? Is it the fee, the time we sent them,
or nothing we can see?

The method is simple. We wait for a chosen validator's leader window, send a small burst of our own transactions
into it, and then read the block. Inside a burst the transactions are identical except for the variable being
tested. The price and the send order are shuffled independently of each other, so the block can tell us which of
the two the leader followed. We sent the bursts two ways: through the public RPC, and straight to the leader's TPU
over QUIC.

**The answer for each validator is the first table in [REPORT.md](REPORT.md#answer-per-validator).**

**Result (3–4 November 2026, 16 validators, 8 clients, 859 bursts, 6 140 transactions, 0.048 SOL).**

1. **When our transactions reached a leader together, almost every validator put the higher fee first.** We sent
   them straight to the leader's TPU over QUIC, just before its block. Conflicting probes landed in fee order 0.90–1.00
   of the time on 15 of 16 validators: Jito, Rakurai, Agave, BAM, Harmonic and one Firedancer. The other Firedancer
   put the cheaper one first (0.24).
2. **Through the public RPC the same probes arrived spread over 1–3 slots, and arrival order took over.** On Jito,
   Rakurai and Agave the fee then made no visible difference.
3. **BAM and Harmonic front ends sort inside time batches.** Close transactions were ordered by fee (0.74–0.95) and
   distant ones by arrival. A 120 ms pause alone flips the order of two groups.
4. **No validator orders a whole block by fee.**

Predictions were pushed to GitHub before each follow-up run ([PREREGISTRATION.md](PREREGISTRATION.md)): 6 of 8 held.
The two that failed are reported as failed.

Results and dates of every measurement are in [REPORT.md](REPORT.md). The generated tables are in
[reports/summary.md](reports/summary.md). Raw data is in [data/](data).

## Check it yourself in 5 minutes

`itb show <run> <burst>` fetches every probe of a burst from RPC, not from our files:
- slot and position: `getBlock`;
- price and memo: `getTransaction`;
- block leader: the fee reward.

Only "sent as #" comes from our send log. Three bursts that show the result:

```bash
docker compose run --rm itb show 20261103T193631Z_live 8     # FireBAM, cheap group sent first, no pause
docker compose run --rm itb show 20261103T193631Z_live 53    # HarmonicFiredancer, dear group sent 120 ms later
docker compose run --rm itb show 20261103T155835Z_live 154   # JitoLabs, ladder with random prices
```

What you should see:

1. **Burst 8 (FireBAM).** All 8 probes in slot 465 019 052 at positions 38-66, in strict descending price order:
   3 000 000 first and 1 000 last. The cheap ones were sent first.
2. **Burst 53 (HarmonicFiredancer).** In slot 465 022 254, the cheap group A (sent first) sits at positions 46-151, in
   descending price inside the group. The dear group B (sent 120 ms later) comes after it: positions 551 and 903, and
   two probes in the next slot.
3. **Burst 154 (JitoLabs).** In slot 464 966 390, positions follow the send order 1, 2, 3, 4, 5. The prices in that
   order are 3 000, 300 000, 3 000 000, 30 000, 1 000 000.

Single bursts are illustrations. The numbers that carry the result are in [REPORT.md](REPORT.md). Without our tool, a
position is one RPC call:

```bash
curl -s https://api.mainnet-beta.solana.com -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"getBlock","params":[465019052,{"transactionDetails":"signatures","maxSupportedTransactionVersion":1}]}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['signatures'].index('3UswzKEUSReacvVyVfzQWxXvQDRdsDqmXqgGGv7aNzecervSLgtYRZWUDphqX3NowknS9S8KisaE8na6vcZK7b7'))"
```

This prints `38`: the 3 000 000 probe of burst 8.

## Run it

```bash
docker compose up                       # 10-minute dry-run: real schedule and windows, nothing is sent, no key needed
docker compose run --rm itb clients     # who runs which client right now, stake share, where their TPU points
docker compose run --rm itb plan        # which validators would be measured and when their windows come
docker compose run --rm itb selftest    # offline tests: block parsing, windows, probes, spend cap, verdict rules
```

Real sending needs a key and the explicit flag:

```bash
cp .env.example .env                    # optional: your RPC endpoint (the public one works, slowly)
# put a funded keypair.json (solana-keygen format) into ./secrets/  (git-ignored), or set ITB_SECRET_KEY in .env
docker compose run --rm itb fund --live            # 8 extra fee payers, 0.004 SOL each (parked, not spent)
docker compose run --rm itb run --live --reps 6    # the experiment, stops at the spend cap (default 0.05 SOL)
docker compose run --rm itb sweep --live           # return the sub-wallet funds to the main wallet
docker compose run --rm itb analyze                # reports/summary.md, pairs.csv, landing.csv, rules.csv
docker compose run --rm itb verify                 # re-fetch blocks from RPC and check recorded positions
docker compose run --rm itb recollect              # finish collection if a run was interrupted
docker compose run --rm itb confirm                # pre-registered tests P1-P6 (split runs)
docker compose run --rm itb paths                  # pre-registered tests P7-P8 (direct TPU against RPC)
```

The runs in `data/` were made with these settings:

```bash
# main 1
itb run --live --reps 24 --minutes 150 --seed 303
# main 2
itb run --live --reps 24 --minutes 90 --trigger-offset -2 --seed 404
# confirmation (--validators: the 16 identities in data/runs/20261103T165600Z_live/meta.json)
itb run --live --reps 8 --minutes 90 --series split0,split120 --trigger-offset -2 --seed 505 --validators ...
# path run (same 16 validators)
itb run --live --reps 12 --minutes 120 --burst 4 --series ladder_shared --path alternate --trigger-offset -2 --seed 707 --validators ...
```

Without Docker: `pip install -r requirements.txt`, then `python -m itb <command>`.

## The probe

Every probe is the same small transaction: `SetComputeUnitLimit(6000)`, `SetComputeUnitPrice(p)` and a 9-byte memo
(`itb` + burst number + probe index, so each probe can also be found by its memo). The memo uses 4 939 compute units
(measured with `simulateTransaction` at the start of every run; the run refuses to start if the limit is too low).
The fee payer is the only writable account. Size, compute limit, accounts and signature count are equal across a
burst, so the leader's cost for every probe is the same and its priority depends only on `p`.

## Three series

| series | fee payers | prices | send spacing | what it isolates |
|---|---|---|---|---|
| `ladder_shared` | one payer for all 8 probes | 8 prices, 1 000 to 3 000 000 micro-lamports/CU | 3 ms set, 4.4 ms measured* | priority among transactions that conflict (same writable account) |
| `ladder_indep` | 8 payers, one per probe | same ladder | 3 ms set, 4.4 ms measured* | priority among transactions that can run in parallel |
| `equal_indep` | 8 payers | all 100 000 | 40 ms set, 46 ms measured* | send order alone: price is constant |

\* The spacing is set with `asyncio.sleep`, and the timer on the Windows machine that ran the experiment is coarse.
The real gaps, taken from the recorded dispatch times, have a median of 4.4 ms (90% under 16 ms) in the ladder series.
A whole ladder burst spans about 40 ms. In `equal_indep` the median gap is 46 ms and a burst spans about 325 ms. The
analysis uses the recorded order of sending, not the set spacing.

In every burst, price and send position come from two independent random permutations. A fee rule and an arrival
rule then predict different orders for the same burst, and the pair counts show which one the block followed.
Series rotate per validator, so each validator gets all three.

## What decides the verdict

The unit of evidence is a pair of our probes that landed in the same block. For each pair we record whether the
higher price came first and whether the earlier send came first. The cleanest pairs are the "duels": pairs where the
higher price was sent later. There a fee rule and an arrival rule predict opposite orders. Pairs from one burst are
not independent, so the 95% intervals come from resampling whole bursts (2 000 draws). These rules are fixed in code
(`itb/analyze.py`):

- which factor dominates comes from the duels. "Fee beats send order" means the whole duel interval is above 0.5;
  "send order beats fee" means it is below 0.5;
- if the duels do not decide, each factor "matters" when its own interval (fee_first or sent_first) is above 0.5;
- "no rule visible" when the fee_first and sent_first intervals both contain 0.5;
- a factor is "strict" when its lower bound is above 0.75.

These rules were committed after the two pilot runs and before the main runs had been analysed (commit `95d35e5`).
The two follow-up runs, confirmation and path, each had their own predictions pushed before they started
([PREREGISTRATION.md](PREREGISTRATION.md); tests in `itb/confirm.py` and `itb/paths.py`).

Probes that landed in different slots are not used for order. A later slot is not a scheduling decision inside a
block. They are counted separately in the landing table.

## The points the bounty asks about

**Leader schedule and leader types.** Leaders come from `getLeaderSchedule` for the current epoch. The client comes
from the `clientId` each node announces in gossip (`getClusterNodes`), with its version string. Measured on
03.11.2026 (epoch 1076), staked nodes reported AgaveBam 37.7%, HarmonicAgave 21.8%, JitoLabs 21.8%,
HarmonicFiredancer 8.7%, Firedancer 5.7%, FireBAM 2.0%, Rakurai 1.25% and plain Agave 1.0% of stake. Plain Agave is
the only one whose scheduler is fully public, and it holds about 1% of stake. The run takes the 2 validators per
client with the most windows left in the epoch. The leader of every block we use is read from the block itself (the
`Fee` reward goes to the leader) and checked against the schedule. A snapshot of all of this is saved with every run
(`data/runs/<run>/landscape.json.gz`).

**The path of a transaction.** Our backend sends HTTP `sendTransaction` to an RPC node with `skipPreflight` and
`maxRetries=0`. In the measured runs, transactions went to the public endpoint `api.mainnet-beta.solana.com`, and all
reads (schedule, blocks, statuses) went to a separate keyed provider. The first live pilot used the keyed provider for
sending too, and its free tier rejected the later sends of each burst with HTTP 429. That biases send order, so this
path was dropped (see REPORT.md). The public endpoint also rate-limits `sendTransaction`, so bursts are spaced at least
10 s apart (`--min-burst-gap-s`). A window that comes too soon after the previous burst is skipped and logged.
The RPC node forwards over QUIC to the TPU address the leader announces in gossip. `maxRetries=0` means it sends once
and does not rebroadcast, so a probe cannot be re-sent into a later slot by the RPC. We record the dispatch time and
the RPC's reply time in nanoseconds for every probe, plus the endpoint used.

A second path, `--path tpu`, skips RPC. `itb/tpu.py` opens a QUIC connection to the TPU address the leader announces
in gossip before its window. It uses ALPN `solana-tpu` and an unstaked, self-signed client certificate, and sends one
transaction per stream. `--path alternate` switches between the two paths per validator, so they can be compared on
the same leaders at the same time. The difference turned out to be the main result (REPORT.md, "Path run"). Through
RPC a burst spreads over 1–3 slots. Sent directly, it lands in the first slot of the window, and almost every leader
then orders it by fee.

The TPU address is part of the path and differs a lot by client. On 03.11, every AgaveBam, FireBAM and
HarmonicFiredancer node announced a TPU on a different host than its own gossip address. There were only 17, 10 and
6 distinct hosts for those groups, and one host served 91 validators. These are the BAM and Harmonic front ends.
JitoLabs and Firedancer were 7% by stake, and Rakurai and Agave were 0%. So for most stake, a transaction reaches an
intermediate service before the leader, and any ordering we see is the ordering of that whole chain, not of the
validator binary alone. `itb clients` prints this table, and each target's TPU host is stored in the run metadata.

**How our own transactions interact.** In `ladder_shared` all probes write-lock the same fee payer, so the leader
cannot execute them in parallel and must serialize them. In the other two series no probe shares a writable account
with another, so they can run in parallel. Comparing the two shows whether a leader orders by priority only when
transactions conflict.

**Competition inside the block.** Our ladder goes from 1 000 to 3 000 000 micro-lamports per CU. In the blocks read
during the dry-runs on 03.11, the median priority fee per cost unit of other transactions was about 7 000
micro-lamports, the 75th percentile about 155 000, and the 90th percentile about 620 000. Our probes sit roughly
between the 30th and 90th percentiles. They are neither the cheapest nor the most expensive in the block. For every block we touched, the whole block is saved (position,
fee, cost units, compute limit and price, Jito-tip flag of every transaction). The report gives, per client, the rank
correlation between position and priority fee per cost unit for all other transactions. This is extra context only.
The bounty asks for our own transactions as primary evidence, and the background mixes transactions that arrived at
different times.

**Send order.** Send order is randomized against price in every burst. `equal_indep` removes price entirely and widens
the spacing to about 46 ms, so an arrival rule has the best chance to show. What we control is the order in which we hand
transactions to the RPC. The order in which they reach the leader can differ (see below).

**Noise versus rule.** Bursts are repeated on the same validator (`--reps`), on two validators per client, and across
three series. The intervals are computed over bursts, not over pairs. "No rule visible" is a valid outcome and the
report says so where it happens.

## What this design does not separate

- **Arrival time at the leader.** We know when we sent each probe, not when the leader received it. RPC forwarding,
  QUIC streams, and BAM or Harmonic front ends can reorder probes that we sent a few milliseconds apart. An "arrival"
  result means "order of sending through this path", nothing finer. The direct path removes the RPC hop but not the
  front ends.
- **The leader's scheduler versus the service in front of it.** For BAM and Harmonic validators we measure the
  combined pipeline.
- **Entries.** `getBlock` gives the order of transactions in the block but not the entry boundaries. Probes that do
  not conflict can be recorded in the same entry, where their relative order means little. `ladder_shared` probes
  conflict and cannot share an entry, which is why that series is the cleanest test of priority.
- **Different RPC providers and staked senders.** We used the public RPC and an unstaked direct connection. Paid
  senders with staked connections (SWQoS) were not tried. FireBAM dropped 24 of 36 direct probes, which a staked
  connection would probably avoid.
- **Jito bundles and tips.** These are out of scope. Transactions that pay a Jito tip are flagged and left out of the
  background statistics. Our probes never pay tips.

## Spend cap

- `--cap-sol` (default 0.05; higher needs `--allow-cap-above`) covers all live runs together. The ledger is
  `data/budget.json`.
- Before each burst the worst-case fee of the burst is reserved: 5 000 lamports + price × limit per probe, about
  0.000067 SOL for a ladder burst. The burst is refused if confirmed fees plus open reservations plus this burst
  would exceed the cap.
- After the window the actual fees are read from the block and the reservation is settled. A reservation stays open
  until the blockhash has expired, so a late landing cannot slip past the ledger.
- A second, independent check sums the balances of all our wallets before every burst and stops the run if they have
  dropped by more than the cap since the first live run.
- Funding sub-wallets moves SOL between our own wallets. Only the transfer fee counts as spend.

## Dry-run

`run` without `--live` does everything except `sendTransaction`:

- picks targets from the live schedule;
- waits for their windows;
- fetches a blockhash;
- builds and signs the probes (with a throwaway key if no key is mounted);
- records when it would have sent and which slot it saw at that moment;
- reads the blocks of the window.

That shows whether the timing hits the window before any money is spent. The blocks read during a dry-run are real
mainnet data and feed the background statistics.

## Data

```
data/budget.json                    spend ledger (live)
data/landscape/<time>.json          client/stake/TPU snapshots from `itb clients`
data/blocks/<slot>.json.gz          every block we read, one row per transaction (index, signature, fee, cost, ...)
data/runs/<run>/meta.json           arguments, endpoints (without API keys), targets, epoch, payer addresses
data/runs/<run>/landscape.json.gz   snapshot at run start
data/runs/<run>/sends.jsonl         one row per probe: burst, series, leader, client, price, send position,
                                    dispatch/ack time (ns), slot seen at dispatch, signature, memo
data/runs/<run>/landed.jsonl        one row per probe: landed slot, index in block, block size, block leader, fee
data/runs/<run>/events.jsonl        everything the run did, in order
reports/                            generated by `itb analyze`
```

Any position can be checked without this code: `getBlock <slot>` with `transactionDetails: "signatures"` and
`maxSupportedTransactionVersion: 1`, then find the signature. `itb verify` does this for a random sample.

## Alpenglow

All measurements were taken after the Alpenglow upgrade of 21 October 2026, and none before it. Blocks we read no
longer contain vote transactions, which is consistent with that.

# What decides where a transaction lands in a Solana block

On 3 and 4 November 2026 I sent 6 260 of my own transactions into the blocks of 16 mainnet validators. The
validators run 8 different clients, two of each. For every transaction I looked at where it landed. This report says
what I found, how I checked it, and where I am not sure. Every table and number in full is in
[reports/FULL_REPORT.md](reports/FULL_REPORT.md).

## The answer

It depends on whether transactions are waiting at the leader at the same moment.

If they are, almost every validator puts the higher fee first. In my last run I sent four transactions with
different fees straight to the leader, just before its block. On 15 of the 16 validators they came out in fee order
90–100% of the time. Jito, Agave, Rakurai, the BAM nodes, the Harmonic nodes and one of the two Firedancer nodes all
did this.

If they arrive at different moments, the one that arrived first usually goes first, whatever it paid. Through the
public RPC my transactions reached the leader spread over 1–3 slots. On Jito, Rakurai and Agave the fee then made
almost no difference.

So asking "fee or arrival?" misses the point. What matters is which transactions meet in the scheduler. Nobody
orders a whole block by fee. A block is a sequence of small groups taken in time order, and each group is sorted by
fee.

One validator did not fit. Firedancer `JD549Hsb…` does not sort reliably even when transactions arrive together.
Over two direct runs it put the dearer one first in 0.50 of 123 pairs. Its neighbour `9eGrDohd…` runs the same version
and put the dearer one first in all 126 pairs.

![Higher fee first, per validator: sent together straight to the leader against through the public RPC](reports/figure_paths.png)

The same numbers as a table:

| client | validator | sent together, direct (higher fee first) | through public RPC (higher fee first) |
|---|---|---|---|
| AgaveBam | `DRpbCBMx…` | 1.00 | 0.71 |
| AgaveBam | `5Cchr1XG…` | 1.00 | 0.70 |
| FireBAM | `9jxgosAf…` | 1.00 (few pairs) | 0.65 |
| FireBAM | `LA1NEzry…` | 1.00 (few pairs) | too little data |
| HarmonicAgave | `Fd7btgyS…` | 0.97 | 0.55 |
| HarmonicAgave | `A1vqhA2f…` | 0.92 | 0.64 |
| HarmonicFiredancer | `EvnRmnMr…` | 0.97 | 0.59 |
| HarmonicFiredancer | `Awes4Tr6…` | 0.90 | 0.65 |
| JitoLabs | `HEL1USMZ…` | 1.00 | 0.43 |
| JitoLabs | `E1r4Psq8…` | 1.00 | 0.56 |
| Rakurai | `ana2y2Yv…` | 1.00 | 0.54 |
| Rakurai | `E3mDbFMr…` | 1.00 | 0.57 |
| Agave | `7QQGNm3p…` | 0.92 | 0.52 |
| Agave | `5Us18hLZ…` | 1.00 | 0.58 |
| Firedancer | `9eGrDohd…` | 1.00 (two runs) | 0.70 |
| Firedancer | `JD549Hsb…` | **0.50** (two runs) | 0.47 |

The numbers are the share of pairs of my transactions in one block where the higher fee sat earlier. 0.5 is a coin
flip. The first column has 30–36 pairs per validator, except FireBAM, which dropped most of the transactions I sent it
directly (6 and 12 pairs). Intervals and the rest are in the full report.

## How I got there

I did it in four steps. Most of them changed what I believed.

**Step 1, through the public RPC (3 November, 554 bursts, 4 432 transactions).** I waited for each validator's leader
window and sent a burst of 8 identical small transactions. They differed only in fee and in the order I sent them,
and I shuffled the two independently. Then I read the block.

Two groups showed up:
- On the BAM and Harmonic validators, transactions that ended up close together in the block (within 15 positions)
  were in fee order 74–95% of the time. Those far apart were in send order about two times out of three.
- On Jito, Rakurai and Agave, the fee did nothing I could see.

These validators also differ in their network address. In gossip, every BAM node and nearly every Harmonic node
points its transaction port (TPU) at a different machine, a shared front end. One such machine served 91
validators. Jito, Rakurai and Agave take transactions on their own address.

The "close together" split came from looking at the data, so I did not trust it yet.

**Step 2, testing it with predictions written in advance (3 November, 122 bursts).** I wrote down six predictions
and pushed them to this repo before sending anything ([PREREGISTRATION.md](PREREGISTRATION.md), commit `0a66f92`). I
sent four cheap transactions first and four expensive ones after them, either right away or 120 ms later. If the
front ends work in short time batches, the pause should flip the order. It did:

| pause between the groups | cheap group first, BAM + Harmonic | cheap group first, FireBAM only |
|---|---|---|
| none | 0.41 | 0.12 |
| 120 ms | 0.88 | 1.00 |

Five of the six predictions held. The one that failed was about Firedancer `9eGr…`, which I expected to sort by fee
strongly. It came out at 0.70, but the interval was too wide to pass the bar I had set.

**Step 3, sending straight to the leader (4 November, 183 bursts).** One thing kept bothering me: maybe Jito and Agave
looked "first come, first served" only because the RPC delivered my transactions at different times. So I wrote a
small QUIC sender that connects to the leader's TPU address directly, without stake. All 16 accepted it. I alternated
it with the RPC on the same validators, again with predictions pushed first.

Sent directly, a whole burst landed in the first slot of the window almost every time. Through RPC the same burst
spread over several slots. And sent directly, the fee won almost everywhere: 0.90 on own-TPU validators against 0.63
through RPC, and 0.96 on the front ends against 0.79. So the "arrival order" I saw on Jito and Agave in step 1 came
mostly from the RPC path, not from the validators.

One of my two predictions here failed. I had expected the direct path to keep my send order better. Instead the
transactions met in the scheduler, and the fee decided.

**Step 4, the two Firedancer nodes (4 November, 30 bursts).** In step 3, `JD549Hsb…` put the cheaper transaction first
(0.24 over 33 pairs), while its twin `9eGrDohd…` put the dearer one first every time. That rested on 6 bursts each,
so I wrote two more predictions and repeated it: 15 direct bursts per node, all 120 transactions landed.

- `9eGrDohd…`: 90 of 90 pairs had the dearer one first. That prediction held.
- `JD549Hsb…`: 0.59 [0.39, 0.79]. I had predicted it would put the dearer one first less than half the time, and
  it did not. The "cheaper first" from step 3 was most likely chance.

The difference between the twins stays, but it is simpler than I thought. One sorts by fee every time, the other
hardly sorts at all.

## Why it works like this

The public documents of these systems fit what I measured. I cannot see any validator's settings, so this is an
explanation, not proof.

- **BAM (Jito's Block Assembly Marketplace).** A BAM validator advertises the BAM node as its TPU, which is what I saw
  in gossip. The BAM node runs "a frequent periodic auction" over short slices of the block
  ([Helius](https://www.helius.dev/blog/block-assembly-marketplace-bam)).
- **Harmonic.** Its documented strategies include 50 ms batches sequenced "by descending priority fees and tips"
  ([Harmonic docs](https://docs.harmonic.gg/concepts/scheduling-strategies.md)). My 120 ms pause is longer than that,
  and my sends a few milliseconds apart fit inside one batch.
- **Agave and Jito.** Anza says the scheduler processes transactions "as they arrive", and that conflicting ones are
  always handled in priority order
  ([Anza](https://www.anza.xyz/blog/introducing-the-central-scheduler-an-optional-feature-of-agave-v1-18)). My
  transactions all used one fee payer, so they conflicted.
- **Firedancer.** Its config has a "perf" mode that fills the block as fast as possible and a "balanced" mode that
  paces it ([default.toml](https://github.com/firedancer-io/firedancer/blob/main/src/app/firedancer/config/default.toml)).
  A node that paces its block lets transactions wait and get sorted. One that packs as fast as possible takes them
  as they come. That could be why `9eGr…` always sorted and `JD549…` did not. I cannot see their settings.

## How sure I am

- 7 of 10 predictions written in advance held. The 3 that failed are described above, not hidden.
- Intervals are computed by resampling whole bursts, so pairs from one burst are not counted as independent
  evidence.
- I checked 60 random transactions against the chain with a fresh RPC call: 60 of 60 matched in slot, position and
  leader. You can check any burst yourself with `itb show` (see the README).
- Other people's transactions in the same 2 361 blocks point the same way, weakly. Neighbouring transactions are in
  fee order 51–52% of the time on Jito, Agave and Rakurai and 54–56% on the rest. That is small but consistent.

## What I could not separate

- I know when I sent each transaction, not when the leader received it.
- For BAM and Harmonic I measure the front end and the validator together, not each one alone.
- The direct test used only transactions that share a fee payer. Whether leaders also sort independent transactions
  that arrive together, I did not test directly.
- Two validators per client is too few to call anything a property of the client. One validator even changed its
  client name overnight (HarmonicFiredancer to HarmonicAgave).
- All of this is from about 4.5 hours of sending over two days. These systems change often.

## What went wrong

1. The first dry-run got rate-limited by the public RPC on block reads. I moved reads to a keyed provider.
2. The first block parser missed the fees of the new version-1 transactions. I fixed it to use the fee and cost
   from the block itself.
3. Pilot 1 sent through a free keyed provider that rejected the later transactions of each burst. That would bias
   send order, so I did not use the pilot and switched sending to the public RPC.
4. Pilot 2 still hit the public RPC's limit. I spaced bursts at least 10 s apart.
5. In main run 1 my transactions landed 2–5 slots late, and for Agave often in the next leader's block. Main run 2
   sends 2 slots earlier.
6. Stopping a run by hand left a few transactions unrecorded. I collected them afterwards and traced 4 lost ones on
   chain.
7. My internet dropped for about 5 minutes during main run 2. One burst failed. Another fired 10 minutes late into
   someone else's block and cost 0.000067 SOL. It is excluded. The code now cancels a burst that is late.
8. I first withdrew SOL to the wallet over the wrong network. That only delayed the start.

## When I measured

All runs were after the Alpenglow upgrade of 21 October. Times are UTC.

| run | when | what | bursts |
|---|---|---|---|
| `20261103T152527Z_live`, `20261103T152855Z_live` | 3 Nov 15:25–15:33 | pilots, not used | 42 |
| `20261103T155835Z_live` | 3 Nov 15:58–16:53 | step 1, main run 1 | 251 |
| `20261103T165600Z_live` | 3 Nov 16:56–18:22 | step 1, main run 2 | 303 |
| `20261103T193631Z_live` | 3 Nov 19:36–20:53 | step 2, split test | 122 |
| `20261104T062417Z_live` | 4 Nov 06:24 | test of the direct sender, not used | 2 |
| `20261104T122307Z_live` | 4 Nov 12:23–14:16 | step 3, direct against RPC | 183 |
| `20261104T200138Z_live` | 4 Nov 20:01–20:24 | step 4, Firedancer repeat | 30 |

The whole project cost 0.049 SOL (0.048754, read from the wallet balances), under the 0.05 SOL limit. The spend
ledger is `data/budget.json`.

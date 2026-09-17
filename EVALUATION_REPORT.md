# Evaluation Report

Hospitals 2 and 3 submitted. Hospital 1 used for development and calibration.

## Sequencing

Hospital 1's labels give a 6.4% base rate, and **42 of the 58 erroneous
invoices carry an error detectable without the contract** — reused ids, wrong
contract numbers, bad dates, arithmetic that does not add up. So structural
checks were built first, then mapping, then pricing. Hospital 3 was taken next
(tabular rates, but three documents and a mid-term amendment applied by service
date) and Hospital 2 after it (one 40-page prose document, every rule embedded
in the clause stating the rate). Together they show the extractor is not tuned
to tables.

## Hospital 1 (913 invoices, 58 erroneous)

| precision | recall | F1 | TP/FP/FN/TN | mapped | expected total exact |
|---|---|---|---|---|---|
| **1.000** | **0.948** | **0.973** | 55/0/3/855 | 97.7% | 36/55 (65%) |

Zero false positives. **15 of 18 categories at 100% recall.** The three below:
`unknown_service` 10/12, `wrong_unit_basis` 10/11, `bundle_not_applied` 4/5.

**Calibration** — stated confidence against observed accuracy:

| stated | 0.80 | 0.87 | 0.90 | 0.94 | 0.95 | 0.97 |
|---|---|---|---|---|---|---|
| observed | 100% | 100% | 98.6% | 100% | 100% | 100% |
| n | 1 | 5 | 219 | 1 | 661 | 26 |

Under-confident in every bucket. The ceiling is 0.97, not 1.0, because these
values are fitted on one labelled hospital and applied to two without labels.
All three errors fall in the 0.90 bucket — invoices carrying an unresolved
line. The 661 priced in full are 100% correct.

## Hospitals 2 and 3

| | H2 | H3 |
|---|---|---|
| invoices / flagged | 1,125 / 102 (9.1%) | 932 / 80 (8.6%) |
| line items mapped | 96.7% | 98.7% |
| services | 76 | 120 (+9 amendments) |

No labels, so three indirect checks: flag rates near Hospital 1's 6.4%,
structural counts in the same 3–17 range, and extraction verified against the
documents. Hospital 3's counts taken by hand from the contract — 118 services
plus 2 added by amendment, 14 premiums, 12 uplifts, 19 discount tiers, 12 caps,
5 bundles, 10 exclusions, 9 amendments — match the ruleset exactly. Hospital 2
states each rate with *at the rate of GBP*, which occurs 76 times; extraction
returned 76.

## Failure types

**F1 — The discriminating word is absent from the description.** All three
misses. Where an abbreviation drops the word separating two contracted
services, no method recovers it: `Visit Amb Hm` fits Ambulatory Cardiac Home
Visit and Ambulatory Infectious Home Visit equally well, and the text contains
nothing to choose between them. This also explains `wrong_unit_basis` 10/11,
since that check needs a mapped service.

**F2 — Bundle detection needs both halves of the pair.** A bundled rate
substitutes only when both services appear on the same patient-day, so one
unresolved description disables the check for its partner even though the
partner mapped correctly.

**F3 — Extraction is sensitive to where the document is cut.** At a 45-line
window, Hospital 1 silently lost six premiums and one uplift: rows separated
from their heading are uninterpretable, and the rate table looked complete.
Mitigated by half-window overlap and per-rule-type floors.

**F4 — Identifying the wrong invoice beats restating its total.** Expected
totals are exact on 65% of true positives; the rest are flagged correctly with
a figure derived from partial pricing.

## Where the model helps, measured

Extraction reproduced a regex parser exactly on Hospital 1 (108 services, zero
differing entries) and is the only stage that cannot be done otherwise, since
Hospital 2 has no table. Mapping is deterministic, and the model handles only
the abstained tail (22 sent on H1, 23 on H3, 57 on H2). Pricing is
deterministic throughout, since half-up rounding on integer cents is arithmetic
rather than language.

## The failure the gates caught

Hospital 3's first extraction produced 29 daily caps against the contract's 12.
A cap that does not exist makes billable units unbillable, so the flag rate
rose to 19.4% against a 6.4% base rate: roughly 104 correct invoices accused at
high confidence. Nothing internal to Hospital 3 would have caught it. Fixed by
requiring two independent sightings of each cap.

## Not attempted

Work stopped at the eight-hour cap with Hospitals 4 and 5 outstanding. In
hindsight the sequencing was slightly wrong: time spent deciding which stages
the model should own would have been better spent on a fourth hospital.

**Hospital 5** prices by facility and plan tier — two multiplier tables applied
at steps (b) and (c), which are no-ops in the current engine because all three
contracts handled use a single facility and identical tier treatment. Needs two
new rule types, the multipliers in `price_line`, and the fields threaded
through. Roughly two hours.

**Hospital 4** states that adjustments are cumulative and cross-referenced,
which is a different rule interaction from the fixed order implemented here and
needs reading before an estimate is credible.

**Next, in order:** Hospital 5; read Hospital 4 Section 4; re-fit the
abstention thresholds per hospital; emit line-level output for the 182 flagged
invoices; cross-check extraction by diffing a deterministic parser against the
model on the same schedule.

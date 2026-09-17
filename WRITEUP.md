# Write-up

Hospitals 2 and 3 submitted. Hospital 1 used as the development set.

---

## How I measured

**Everything was measured against Hospital 1's labels before it was trusted
anywhere else.** Hospital 1 is not scored, so its only value is as an
instrument — every design decision below was made by running both options and
comparing, not by preference.

The headline figures on Hospital 1: **precision 1.000, recall 0.948, F1 0.973**,
with 55 true positives, zero false positives and three misses out of 913
invoices. Fifteen of the eighteen labelled categories are at 100% recall; the
three below are `unknown_service` 10/12, `wrong_unit_basis` 10/11 and
`bundle_not_applied` 4/5.

Beyond the headline, three measurements mattered more:

**Base rate.** Hospital 1 has a 6.4% error rate. That number is the only
external check available on the two scored hospitals, which have no labels. H2
flags 9.1% and H3 flags 8.6% — same order of magnitude, which is weak evidence
but the best available.

**Calibration.** Stated confidence against observed accuracy, bucketed. Every
bucket sits at or above its stated value, so the submission is under-confident
rather than over-confident. Critically, **all three errors fall in the 0.90
bucket**, which is the value assigned to invoices carrying a line the matcher
could not resolve. Every other bucket is 100% accurate. The confidence column
is therefore carrying real information about where the pipeline is weak, rather
than being a flat guess.

**Extraction counts against the source.** The contract rulesets are produced by
a language model, so I counted the contract by hand and compared. Hospital 3:
118 services in Appendix B plus 2 added by amendment, 14 threshold premiums, 12
uplifts, 19 discount tiers, 12 caps, 5 bundles, 10 exclusion windows, 9
amendments — all match the extracted ruleset exactly. Hospital 2 states each
rate with the phrase *at the rate of GBP*, which occurs 76 times; extraction
returned 76 services.

That last check earned its keep. Hospital 3's first extraction produced **29
daily caps against the contract's 12** — seventeen inferred from isolated
numbers in unrelated tables. A cap that does not exist makes billable units
unbillable, so expected totals came in low and the flag rate rose to 19.4%:
roughly a hundred correct invoices accused at high confidence. Nothing internal
to Hospital 3 would have caught it. It was caught by comparing the flag rate to
Hospital 1's base rate.

---

## Where I was uncertain, and why

**The abstention threshold is fitted to one hospital and cannot be re-fitted.**
The matcher separates three claims: below a score of 0.50 it asserts the
description names no contracted service, between 0.50 and 0.70 it is simply
unsure, above that it commits. The 0.50 boundary was tuned on Hospital 1 and
its sweep is a cliff — 0.50 gives F1 0.883, 0.55 gives 0.654. A boundary that
sharp is fitted to one dataset. It was carried unchanged to Hospitals 2 and 3
because there are no labels to re-fit against.

The consequence is visible: Hospital 3 yields **one** `unknown_service` finding
where Hospital 1 had twelve. I inspected the ten lowest-scoring descriptions
there and they read as contracted services written tersely rather than
uncontracted billing, so the threshold appears to be erring conservatively —
but that is a judgement I made by reading, not a measurement. It is the largest
untested assumption in the submission.

**Two contract clauses admit more than one reading.** The daily-cap clause caps
billable units and says nothing about the surplus. I priced the surplus out and
kept the remainder; the alternative is that the whole line is rejected. I
implemented both and scored both against Hospital 1 — identical results, so the
ambiguity does not discriminate on this data and either reading is defensible.
Separately, the adjustment-order clause says "any premium or uplift", which is
singular in effect and plural in wording; on Hospital 1 the two service lists
do not intersect, so the question never arises and my choice to apply both
remains untested.

**Expected totals are less reliable than the flags.** They are exact on 36 of
55 true positives. The pipeline identifies which invoices are wrong
considerably better than it restates what they should have cost, mostly because
invoices containing an unresolved line are priced from the subset of lines that
did resolve. I would rather report that gap than smooth over it.

**I do not know the true error rate on the scored hospitals.** Everything above
is an argument from consistency with a different hospital. A grader with the
real labels may find the flag rates are right for the wrong reasons.

---

## What I would do differently with another week

**Cover Hospital 5, then read Hospital 4 properly.** Hospital 5 prices by
facility and plan tier — two multiplier tables applied at steps the current
engine treats as no-ops, because all three contracts handled so far use a
single facility and identical tier treatment. The work is scoped: two new rule
types in the extractor, the multipliers applied in the pricing engine, the two
fields threaded through, and validation floors for the new types. Hospital 4
states that its adjustments are cumulative and cross-referenced, which may not
fit the fixed adjustment order implemented here; I would read that section
before estimating anything.

**Attack the one systematic weakness rather than the score.** All three
Hospital 1 misses have the same cause: the billing description does not contain
the word that separates two contracted services. `Visit Amb Hm` fits Ambulatory
Cardiac Home Visit and Ambulatory Infectious Home Visit equally, and no method
recovers what is not there. The productive response is not a better matcher but
a different output — emitting line-level results so a human reviewing the 182
flagged invoices sees exactly which line was unresolvable and why.

**Cross-check extraction two ways.** Run a deterministic parser and the model
over the same tabular schedule and diff them. Agreement raises confidence,
disagreement routes to human review. This is the right use of a model on the
highest-stakes step: as a checker whose output is verified, not as the sole
source of truth.

**And I would sequence differently.** I reached the eight-hour cap with two
hospitals submitted and two untouched. Time spent settling which stages the
model should own would have been better spent on a fourth hospital — the
architecture question was interesting, but the brief asks for coverage and an
honest account, and I traded some of the first for certainty I did not
strictly need.

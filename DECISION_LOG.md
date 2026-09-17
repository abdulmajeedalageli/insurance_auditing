# Decision Log

## Assumptions

**A1 — Extraction is model-primary, but nothing unverified reaches the engine.**
Every extracted rate must appear as a literal string in the source text, unit
bases must be one of nine known codes, and each rule type must be non-empty
before the ruleset is written. A gate failure halts the run.

**A2 — Unpriceable lines still advance the volume-discount ledger.** A line
whose description could not be matched was still billed; dropping it puts every
later line of that service on the wrong tier. The engine counts on the best
candidate while refusing to price.

**A3 — "No contracted service matches" and "we cannot tell which" are different
claims.** Below 0.50 the pipeline asserts the description names no contracted
service and reports it. Between 0.50 and 0.70 it is unsure and reports nothing.

**A4 — Reused invoice ids are detected, not merged.** 918 records for 913 ids
on H1, 1,132 for 1,125 on H2, 939 for 932 on H3.

**A5 — Erroneous invoices still count toward cumulative utilisation.** The
clause counts units "billed", not "correctly billed". The hospital's own
discount switch point matches: full rate at prior=55, discounted at 64, against
a threshold of 60.

**A6 — Where a service is billed twice, the later invoice is the error.**
Flagging both makes every genuine catch a false positive on its partner.

**A7 — Confidence values are fitted, not asserted.** Refit against the observed
H1 calibration table, capped at 0.97.

## Ambiguities found and not resolved

**Q1 — What is the expected total when a daily cap is exceeded?** The contract
caps billable units and says nothing about the surplus. **Taken:** the surplus
is unbillable, the remainder prices normally. **Alternative:** the whole line is
rejected. Both were implemented and scored on H1 — identical results, so the
ambiguity does not discriminate here.

**Q2 — Can a threshold premium and a non-business-day uplift both apply?** The
clause says "any premium or uplift". On H1 the two service lists do not
intersect, so it never arises. **Taken:** apply both, premium first. Untested.

**Q3 — H2 defines a Service Day as 07:00 to 06:59 the next day**, where H1 and
H3 use the calendar day. Its next clause defines Service Date as the date on
the line item, which has no time component. **Taken:** calendar date.

**Q4 — Does cumulative utilisation reset at an amendment?** H3 counts across
the whole term, which spans Amendment No. 1. Units are counted regardless of
which rate priced them. Natural reading, not stated.

**Q5 — H2 ships four renderings of one document**, including a scanned PDF. The
text and Markdown versions match by size and GBP count. **Taken:** plain text;
the scan was not OCR'd.

## Errors made and corrected

**E1 — Unit basis normalisation.** `per hour, per item` was collapsed to
`per_hour`, but invoices use a distinct ninth code. 88 line items reported a
mismatch that did not exist, raising H3's flag rate from 8.3% to 16.3%. Caught
by comparing against H1's base rate.

**E2 — Inferred daily caps.** H3 extraction produced 29 caps against the
contract's 12; the extra 17 came from isolated numbers in unrelated tables.
Flag rate 19.4%. Fixed by requiring two independent sightings.

**E3 — Extraction window size.** At 45 lines, H1 silently dropped six premiums
and one uplift. Fixed by overlapping windows and per-rule-type floors.

**E4 — Ambiguous abbreviations collapsed to one reading.** `obs` mapped to
`obstetric` only, but in `Obs Metab Nursing` it means `observation`. One
description, 35 false positives. Abbreviations now carry every plausible
reading.

**E5 — Whole-invoice pricing gate.** An invoice with any unresolved line got no
pricing verdict, so real errors on clean lines went unreported. Replaced with
partial pricing over patient-days containing no unresolved lines. Moved three
categories to 100%.

## Known gaps

- **`unknown_below=0.50` does not demonstrably transfer.** Its sweep is a cliff
  (0.50 → F1 0.883, 0.55 → 0.654), so it is fitted to H1 and was carried
  unchanged for lack of labels. H3 yields one `unknown_service` finding against
  H1's twelve; the lowest-scoring descriptions there read as contracted
  services described tersely, so the threshold appears conservative — an
  inspection, not a measurement. The largest untested assumption in the
  submission.
- **The two-sighting cap rule depends on window overlap.** A cap table falling
  entirely inside one window would be dropped.
- **Expected totals are exact on 65% of true positives.**
- **Hospitals 4 and 5 not attempted.** H5 needs facility and plan-tier
  multipliers, currently no-ops. H4 states that adjustments are cumulative and
  cross-referenced, which may not fit the fixed order here.

## AI assistance

Claude (Anthropic) was used for code review and debugging. At runtime the
pipeline calls `gpt-oss-120b` via Cerebras for extraction (always),
adjudication (`--adjudicate`) and review (`--review`). Prompts are under
`prompts/` with superseded versions retained.

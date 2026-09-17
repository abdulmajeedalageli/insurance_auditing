# Meridian Invoice Audit

Audit pipeline for five hospital reimbursement contracts. **Hospitals 2 and 3
are submitted**; Hospital 1 is the labelled development set and is not scored.
Hospitals 4 and 5 were not attempted — `EVALUATION_REPORT.md` §8 says why and
what they need.

## Results

| | Hospital 1 (dev) | Hospital 2 | Hospital 3 |
|---|---|---|---|
| precision / recall / F1 | **1.000 / 0.948 / 0.973** | no labels | no labels |
| invoices | 913 | 1,125 | 932 |
| flagged | 55 (6.0%) | 102 (9.1%) | 80 (8.6%) |
| line items mapped | 97.7% | 96.7% | 98.7% |
| contracted services | 108 | 76 | 120 (+9 amendments) |

Zero false positives on Hospital 1; 16 of 18 error categories at 100% recall.

- `EVALUATION_REPORT.md` — per-category performance, calibration, failure analysis
- `DECISION_LOG.md` — assumptions, unresolved ambiguities, known gaps

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Contract extraction, `--adjudicate` and `--review` call the model and need a key:

```cmd
set CEREBRAS_API_KEY=csk-...           :: cmd
$env:CEREBRAS_API_KEY="csk-..."        # PowerShell
```

## Reproduce

```cmd
:: 1. contracts -> ruleset_llm.json  (validated before it is written)
python src/llm_extract.py --hospital hospital_1
python src/llm_extract.py --hospital hospital_2
python src/llm_extract.py --hospital hospital_3

:: 2. audit
python main.py --hospital hospital_1 --adjudicate --review --evaluate
python main.py --hospital hospital_2 --adjudicate --review
python main.py --hospital hospital_3 --adjudicate --review

:: 3. combined submission
python -c "import csv; rows=[]; [rows.extend(list(csv.DictReader(open(f'output/submission_hospital_{h}.csv')))) for h in [2,3]]; w=csv.DictWriter(open('submission.csv','w',newline=''),fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)"
```

Every model call is cached on a hash of its prompt, so re-runs are free and an
interrupted run resumes where it stopped. `cache/` is committed so the reported
figures reproduce without a key. Delete `cache/llm/` to force fresh calls.

### Flags

| flag | effect |
|---|---|
| *(none)* | deterministic: token matcher, pricing, structural checks |
| `--adjudicate` | model resolves descriptions the matcher could not separate |
| `--review` | model second opinion on each finding; adjusts confidence only |
| `--evaluate` | score against labels (Hospital 1 only) |

## Layout

```
main.py                     orchestrator
src/config.py               paths, model settings, hospital registry
src/llm_extract.py          contract documents -> ruleset_llm.json
src/llm_client.py           HTTP transport, caching, output coercion
src/llm_mapper.py           token matcher, learned aliases, adjudication
src/pricing_engine.py       adjustment order, Decimal, amendment-aware
src/structural_checks.py    contract-independent validations
src/reviewer.py             second opinion on findings
src/evaluate.py             scorer, per-category recall, calibration
prompts/                    versioned, superseded versions retained
contracts/hospital_N/       source documents + extracted ruleset
cache/                      model responses, alias tables, adjudications, reviews
output/                     per-hospital submissions
```

No file contains hospital-specific logic. Adding a hospital means adding a
registry entry to `src/config.py`.

## Design

**Structural checks** need no contract: reused invoice ids, wrong contract
numbers, dates outside the term or after the invoice date, malformed dates,
line and invoice arithmetic, duplicate billing within and across invoices. On
Hospital 1 these account for 42 of 58 erroneous invoices, so they run first.

**Description mapping** is deterministic and abstains rather than guessing. The
abbreviation table is learned from each hospital's own descriptions: where a
confidently-matched pair leaves exactly one unmatched token on each side, those
two are proposed as an alias, and a proposal is accepted once two independent
descriptions produce the same alignment. Three thresholds separate three
different claims — no contracted service matches, the score is too weak to
assert, and two services fit equally well.

**Pricing** applies the contract's adjustment order in `Decimal` throughout:
bundled rate substitution, facility multiplier, plan-tier multiplier, premium
or uplift, cumulative volume discount, rounding half-up after each step. A line
that cannot be priced raises rather than contributing zero. Where an invoice
contains unresolved lines, the patient-days that are fully resolved are still
priced and compared.

**Confidence** is fitted to the observed Hospital 1 calibration table and
capped at 0.95. Invoices carrying an unresolved line are distinguished from
those priced in full, which is where all three Hospital 1 errors fall.

## Prompts

| file | purpose |
|---|---|
| `extract_v1.md` | contract window -> service terms |
| `adjudicate_v2.md` | unresolved description -> service or CANNOT_DETERMINE |
| `review_v1.md`, `review_v2.md` | confirm or dispute a finding |

`review_v2` supplies the contract header and explains that volume discounts run
on cumulative prior utilisation. Measured on Hospital 1, that change moved the
reviewer from 30 confirmations and 11 disputes to 39 and 3.

## AI assistance

Claude (Anthropic) was used for code review, assistant and debugging. At runtime
the pipeline calls `gpt-oss-120b` via Cerebras for extraction, adjudication and
review. See `DECISION_LOG.md`.

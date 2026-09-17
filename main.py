"""
Audit runner.

    python main.py --hospital hospital_1 --evaluate
    python main.py --hospital hospital_3 --adjudicate --review

Order is cheapest and most certain first: structural checks (no contract
needed), then mapping with abstention, then pricing.
"""

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import config
import structural_checks as sc
from llm_mapper import SemanticMapper
from pricing_engine import PricingEngine, Unpriceable, parse_date

# fitted to the observed H1 calibration table. capped at 0.95, not 1.0 -- these
# carry to hospitals with no labels to check them against.
CONF = {
    "structural": 0.95,
    "unknown_service": 0.92,
    "pricing": 0.93,
    "pricing_partial": 0.85,
    "llm_resolved_pricing": 0.80,
    "clean_fully_priced": 0.95,
    "clean_with_abstention": 0.90,
}


def load_records(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def run(hospital_id, adjudicate=False, review=False):
    print(f"--- AUDIT PIPELINE: {hospital_id} ---")

    ruleset_file = config.ruleset_path(hospital_id)
    if not ruleset_file.exists():
        raise FileNotFoundError(
            f"{ruleset_file} not found. Run:\n"
            f"    python src/llm_extract.py --hospital {hospital_id}")
    ruleset = json.loads(ruleset_file.read_text(encoding="utf-8"))
    records = load_records(config.invoices_path(hospital_id))
    print(f"loaded {len(records)} invoice records, "
          f"{len(ruleset['rates'])} contracted services")

    descriptions = [li["description"] for r in records for li in r["line_items"]]

    alias_file = config.alias_cache_path(hospital_id)
    if alias_file.exists():
        aliases = json.loads(alias_file.read_text(encoding="utf-8"))
        print(f"alias table: {len(aliases)} entries from cache")
    else:
        print("learning alias table from data...")
        aliases = SemanticMapper.bootstrap_aliases(ruleset["rates"].keys(), descriptions)
        alias_file.parent.mkdir(parents=True, exist_ok=True)
        alias_file.write_text(json.dumps(aliases, indent=2), encoding="utf-8")
        print(f"  {len(aliases)} aliases learned -> {alias_file}")

    mapper = SemanticMapper(ruleset["rates"].keys(), hospital_id, aliases=aliases)
    engine = PricingEngine(ruleset)

    if adjudicate:
        mapper.adjudicate_unresolved(descriptions)

    service_of, ledger_of, reason_of = {}, {}, {}
    for r in records:
        for li in r["line_items"]:
            m = mapper.match(li["description"])
            service_of[li["line_id"]] = m.service
            ledger_of[li["line_id"]] = m.best
            reason_of[li["line_id"]] = m.reason

    matched = sum(1 for v in reason_of.values() if v in ("matched", "llm_resolved"))
    print(f"mapped {matched}/{len(reason_of)} line items ({matched / len(reason_of):.1%})")

    all_lines = [{
        "patient_id": r["patient_id"],
        "date": parse_date(li["service_date"]),
        "service": service_of[li["line_id"]],
        "ledger_service": ledger_of[li["line_id"]],
        "quantity": li["quantity"],
        "line_id": li["line_id"],
    } for r in records for li in r["line_items"]]
    engine.build_context(all_lines)

    dup_ids = sc.check_duplicate_invoice_ids(records)
    cross_dup_invoices = sc.build_cross_invoice_index(records, service_of)

    # service-date order: volume discounts depend on prior utilisation
    all_lines.sort(key=lambda x: (x["date"] or date.max, x["line_id"]))
    expected_line, notes_of = {}, {}
    for ln in all_lines:
        try:
            expected_line[ln["line_id"]], notes_of[ln["line_id"]] = engine.price_line(ln)
        except Unpriceable:
            pass

    contract_no = ruleset["header"]["contract_number"]
    term_start = date.fromisoformat(ruleset["header"]["term_start"])
    term_end = date.fromisoformat(ruleset["header"]["term_end"])

    rows = []
    for r in records:
        findings, conf_pool = [], []
        billed = r["invoice_total_cents"]

        if r["invoice_id"] in dup_ids:
            findings.append("duplicate_invoice_id"); conf_pool.append(CONF["structural"])
        if contract_no and sc.check_contract_number(r, contract_no):
            findings.append("contract_number_mismatch"); conf_pool.append(CONF["structural"])
        if sc.check_line_arithmetic(r):
            findings.append("line_total_arithmetic"); conf_pool.append(CONF["structural"])
        if sc.check_invoice_total(r):
            findings.append("invoice_total_mismatch"); conf_pool.append(CONF["structural"])
        for kind, _lid in sc.check_service_dates(r, term_start, term_end):
            if kind not in findings:
                findings.append(kind); conf_pool.append(CONF["structural"])
        if sc.check_intra_invoice_duplicates(r, service_of):
            findings.append("duplicate_service_same_day"); conf_pool.append(CONF["structural"])
        if r["invoice_id"] in cross_dup_invoices:
            findings.append("cross_invoice_duplicate"); conf_pool.append(CONF["structural"])

        for li in r["line_items"]:
            svc = service_of[li["line_id"]]
            if svc and li["unit_basis_as_billed"] != ruleset["rates"][svc]["unit_basis"]:
                findings.append("wrong_unit_basis"); conf_pool.append(CONF["structural"])
                break

        reasons = {reason_of[li["line_id"]] for li in r["line_items"]}
        if "no_candidate" in reasons:
            findings.append("unknown_service"); conf_pool.append(CONF["unknown_service"])
        has_abstention = bool(reasons & {"ambiguous", "weak"})
        used_llm = "llm_resolved" in reasons

        line_ids = [li["line_id"] for li in r["line_items"]]
        priceable = all(lid in expected_line for lid in line_ids)
        expected_total = sum(expected_line[lid] for lid in line_ids) if priceable else None
        partial_expected = None

        if priceable and expected_total != billed:
            findings.append("pricing_mismatch")
            conf_pool.append(CONF["llm_resolved_pricing"] if used_llm else CONF["pricing"])
        elif not priceable:
            # one ambiguous line used to kill the verdict for the whole invoice.
            # price the subset we can, but only on patient-days with no
            # unresolved lines -- bundles, premiums and caps are day aggregates
            # and would be wrong otherwise. +3 categories, +0.07 F1.
            bad_days = {parse_date(li["service_date"]) for li in r["line_items"]
                        if li["line_id"] not in expected_line}
            safe = [li["line_id"] for li in r["line_items"]
                    if li["line_id"] in expected_line
                    and parse_date(li["service_date"]) not in bad_days]
            billed_map = {li["line_id"]: li["line_total_cents"] for li in r["line_items"]}
            sub_expected = sum(expected_line[lid] for lid in safe)
            sub_billed = sum(billed_map[lid] for lid in safe)
            if safe and sub_expected != sub_billed:
                findings.append("pricing_mismatch")
                conf_pool.append(CONF["pricing_partial"])
                # correct the billed total by the difference on the lines we
                # could price. reporting `billed` here would say the invoice is
                # wrong and should have totalled exactly what it did total.
                partial_expected = billed - sub_billed + sub_expected

        flagged = 1 if findings else 0
        if flagged:
            confidence = max(conf_pool)
        elif has_abstention or not priceable:
            confidence = CONF["clean_with_abstention"]
        else:
            confidence = CONF["clean_fully_priced"]

        rows.append({
            "invoice_id": r["invoice_id"],
            "flagged": flagged,
            "error_category": "|".join(findings),
            "expected_total_cents": (expected_total if expected_total is not None
                                     else partial_expected if partial_expected is not None
                                     else billed),
            "billed_total_cents": billed,
            "confidence": round(confidence, 2),
        })

    if review:
        from reviewer import review_findings, adjust_confidence
        reviews = review_findings(rows, records, ruleset, service_of,
                                  expected_line, notes_of, hospital_id)
        changed = 0
        for row in rows:
            new = adjust_confidence(row, reviews.get(row["invoice_id"]))
            changed += new != row["confidence"]
            row["confidence"] = new
        verdicts = {}
        for rv in reviews.values():
            verdicts[rv["verdict"]] = verdicts.get(rv["verdict"], 0) + 1
        print(f"  reviewer: {verdicts}, {changed} confidences adjusted")
    # reused invoice ids -> one row each, keep the flagged verdict
    merged = {}
    for row in rows:
        prev = merged.get(row["invoice_id"])
        if prev is None or row["flagged"] > prev["flagged"]:
            merged[row["invoice_id"]] = row

    out = config.submission_path(hospital_id)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["invoice_id", "flagged", "error_category",
                                           "expected_total_cents",
                                           "billed_total_cents", "confidence"])
        w.writeheader()
        w.writerows(merged.values())

    flagged_n = sum(r["flagged"] for r in merged.values())
    print(f"flagged {flagged_n}/{len(merged)} invoices")
    print(f"written to {out}")
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--hospital", default="hospital_1")
    p.add_argument("--adjudicate", action="store_true",
                   help="resolve ambiguous descriptions with the model")
    p.add_argument("--review", action="store_true",
                   help="model second opinion on each finding, adjusts confidence only")
    p.add_argument("--evaluate", action="store_true")
    a = p.parse_args()

    submission = run(a.hospital, adjudicate=a.adjudicate, review=a.review)

    if a.evaluate:
        labels = config.labels_path(a.hospital)
        if not labels.exists():
            print(f"\nno labels at {labels}; skipping evaluation")
        else:
            from evaluate import score
            print()
            score(str(submission), str(labels))
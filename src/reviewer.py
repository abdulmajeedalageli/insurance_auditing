"""
reviewer.py -- second opinion on each finding the engine flagged.

Only flagged invoices are sent. The model sees the contract rules for that
invoice's services, the billed figures, the computed expectation and the rules
that fired, and returns CONFIRM, DISPUTE or UNSURE. The verdict adjusts
confidence only; it never changes `flagged`.

    python main.py --hospital hospital_3 --review
    python src/reviewer.py --hospital hospital_1     # agreement against labels
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from llm_client import call_llm

SYSTEM = ("You review billing audit findings against contract terms. You "
          "confirm a finding only if the contract supports it. Output only "
          "valid JSON.")


def _cache_path(hospital_id):
    return config.CACHE_DIR / f"{hospital_id}_reviews.json"


def rules_for(ruleset, services):
    """Subset of the ruleset touching `services`, plus the header. Sending the
    whole ruleset buries the relevant rules."""
    out = {"rates": {}, "threshold_premiums": {}, "nonbusiness_uplifts": {},
           "volume_discounts": {}, "daily_caps": {}, "bundles": [],
           "exclusions": [], "amendments": []}
    for s in services:
        if s in ruleset["rates"]:
            out["rates"][s] = ruleset["rates"][s]
        for key in ("threshold_premiums", "nonbusiness_uplifts",
                    "volume_discounts", "daily_caps"):
            if s in ruleset[key]:
                out[key][s] = ruleset[key][s]
    for b in ruleset.get("bundles", []):
        if b["service_a"] in services or b["service_b"] in services:
            out["bundles"].append(b)
    for e in ruleset.get("exclusions", []):
        if e["excluded_service"] in services:
            out["exclusions"].append(e)
    for a in ruleset.get("amendments", []):
        if a["service"] in services:
            out["amendments"].append(a)
    # term dates, needed to judge date-window and duplicate-id findings
    out["contract_header"] = ruleset["header"]
    return out


def review_findings(rows, records, ruleset, service_of, expected_line,
                    notes_of, hospital_id, limit=None):
    """-> {invoice_id: {"verdict", "reason"}}. Cached to disk per invoice."""
    cache_path = _cache_path(hospital_id)
    cache = json.loads(cache_path.read_text(encoding="utf-8")) \
        if cache_path.exists() else {}

    by_id = {r["invoice_id"]: r for r in records}
    flagged = [r for r in rows if r["flagged"] == 1
               and r["invoice_id"] not in cache]
    if limit:
        flagged = flagged[:limit]

    if not flagged:
        print(f"  reviewer: {len(cache)} findings already reviewed")
        return cache

    print(f"  reviewing {len(flagged)} findings")
    template = (config.PROMPTS_DIR / "review_v2.md").read_text(encoding="utf-8")

    for n, row in enumerate(flagged, 1):
        if n % 10 == 1 or n == len(flagged):
            print(f"    {n}/{len(flagged)}")
        rec = by_id.get(row["invoice_id"])
        if rec is None:
            continue

        services, lines = set(), []
        for li in rec["line_items"]:
            svc = service_of.get(li["line_id"])
            if svc:
                services.add(svc)
            lines.append({
                "line_id": li["line_id"],
                "mapped_service": svc,
                "service_date": li["service_date"],
                "quantity": li["quantity"],
                "unit_basis_as_billed": li["unit_basis_as_billed"],
                "billed_unit_price_cents": li["unit_price_cents"],
                "billed_line_total_cents": li["line_total_cents"],
                "our_expected_line_total_cents": expected_line.get(li["line_id"]),
                # notes carry the figures behind each rule, not just its name
                "rules_we_applied": notes_of.get(li["line_id"], []),
            })

        payload = {
            "invoice_id": rec["invoice_id"],
            "our_finding": row["error_category"],
            "billed_total_cents": row["billed_total_cents"],
            "our_expected_total_cents": row["expected_total_cents"],
            "line_items": lines,
        }
        prompt = (template
                  .replace("{{RULES}}", json.dumps(rules_for(ruleset, services), indent=1))
                  .replace("{{FINDING}}", json.dumps(payload, indent=1)))

        try:
            data = call_llm(SYSTEM, prompt)
        except Exception as exc:                       # noqa: BLE001
            # one unreviewable invoice keeps its base confidence
            print(f"    review failed for {rec['invoice_id']}: {exc}")
            continue

        verdict = str(data.get("verdict", "")).strip().upper()
        if verdict not in ("CONFIRM", "DISPUTE", "UNSURE"):
            verdict = "UNSURE"
        cache[rec["invoice_id"]] = {"verdict": verdict,
                                    "reason": str(data.get("reason", ""))[:300]}
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    return cache


def adjust_confidence(row, review):
    """CONFIRM raises confidence; DISPUTE and UNSURE leave it unchanged.

    On H1 every dispute was against a correct finding, so disputes carry no
    signal and acting on them would only degrade calibration.
    """
    if review and review["verdict"] == "CONFIRM":
        return min(0.97, round(row["confidence"] + 0.02, 2))
    return row["confidence"]


def measure(hospital_id):
    """Print how often each verdict landed on a genuinely erroneous invoice.
    Needs labels, so H1 only."""
    reviews = json.loads(_cache_path(hospital_id).read_text(encoding="utf-8"))
    labels = {r["invoice_id"]: r for r in
              csv.DictReader(config.labels_path(hospital_id).open(encoding="utf-8"))}

    counts = {"CONFIRM": [0, 0], "DISPUTE": [0, 0], "UNSURE": [0, 0]}
    for inv_id, rev in reviews.items():
        lab = labels.get(inv_id)
        if lab is None:
            continue
        counts[rev["verdict"]][0] += int(lab["is_erroneous"] == "1")
        counts[rev["verdict"]][1] += 1

    print("reviewer verdicts on flagged invoices (all were flagged by us)")
    print("-" * 62)
    for verdict, (correct, total) in counts.items():
        if total:
            print(f"  {verdict:8s} n={total:3d}   of these, truly erroneous: "
                  f"{correct}/{total} ({correct / total:.0%})")

    disputed = [(i, r["reason"]) for i, r in reviews.items()
                if r["verdict"] == "DISPUTE"]
    if disputed:
        print(f"\ndisputed ({len(disputed)}):")
        for inv_id, reason in disputed[:10]:
            truth = labels.get(inv_id, {}).get("is_erroneous", "?")
            mark = "our flag was RIGHT" if truth == "1" else "our flag was WRONG"
            print(f"  {inv_id}  [{mark}]  {reason[:110]}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--hospital", default="hospital_1")
    a = p.parse_args()
    measure(a.hospital)
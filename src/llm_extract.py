"""
llm_extract.py -- contract documents to ruleset_llm.json.

    python src/llm_extract.py --hospital hospital_1 --window 30
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from config import UNIT_BASIS_MAP
from llm_client import (call_llm, coerce_cents, coerce_int, coerce_obj,
                        coerce_pct, find_list, normalise_keys)

SYSTEM = ("You read healthcare reimbursement contracts and report their terms "
          "as JSON. You copy values exactly as written. You never infer a term "
          "that is not stated. Output only valid JSON.")

# output field names aren't stable between calls
FIELD_ALIASES = {
    "rate": "rate_gbp", "rate_amount": "rate_gbp", "base_rate": "rate_gbp",
    "price": "rate_gbp", "amount": "rate_gbp",
    "service": "service_name", "name": "service_name",
    "unit": "unit_basis", "basis": "unit_basis",
    "cap": "daily_cap", "daily_quantity_cap": "daily_cap",
    "non_business_day_uplift": "nonbusiness_uplift_percent",
    "nonbusiness_uplift": "nonbusiness_uplift_percent",
    "weekend_uplift": "nonbusiness_uplift_percent",
    "volume_discount": "volume_discounts",
    "premium": "threshold_premium",
}


def windows(text, size, overlap=None):
    """Overlapping line windows. Default overlap is half of `size`, putting
    every line in exactly two windows."""
    overlap = size // 2 if overlap is None else overlap
    lines = text.splitlines()
    step = max(size - overlap, 1)
    for i in range(0, len(lines), step):
        chunk = "\n".join(lines[i:i + size]).strip()
        if chunk:
            yield chunk


def empty_ruleset(hospital_id):
    cfg = config.hospital_config(hospital_id)
    return {
        "hospital_id": hospital_id,
        "header": {"contract_number": cfg["contract_number"],
                   "term_start": cfg["term"][0], "term_end": cfg["term"][1]},
        "rates": {}, "threshold_premiums": {}, "nonbusiness_uplifts": {},
        "volume_discounts": {}, "daily_caps": {}, "bundles": [],
        "exclusions": [], "amendments": [],
    }


def merge(rs, svc, source_text, rejected):
    """Fold one entry into `rs`. An entry may carry a rate, rules, or both --
    rule tables name a service without restating its rate."""
    svc = normalise_keys(svc, FIELD_ALIASES)
    name = str(svc.get("service_name", "")).strip()
    if not name:
        return

    cents = coerce_cents(svc.get("rate_gbp"))
    if cents is not None:
        whole, frac = divmod(cents, 100)
        # a rate absent from the source wasn't read from the contract
        if f"{whole}.{frac:02d}" not in source_text and \
                f"{whole:,}.{frac:02d}" not in source_text:
            rejected.append(f"{name}: rate {cents}c not in contract text")
        else:
            basis = str(svc.get("unit_basis", "")).strip().lower()
            code = UNIT_BASIS_MAP.get(basis)
            if code is None:
                rejected.append(f"{name}: unknown unit basis {basis!r}")
            else:
                eff = svc.get("effective_from")
                entry = {"rate_cents": cents, "unit_basis": code}
                if eff:
                    entry["effective_from"] = str(eff)[:10]
                    if not any(a["service"] == name and
                               a["effective_from"] == entry["effective_from"]
                               for a in rs["amendments"]):
                        rs["amendments"].append({"service": name, **entry})
                # base rate wins; config reads base -> appendix -> amendment
                rs["rates"].setdefault(name, entry)

    cap = coerce_int(svc.get("daily_cap"))
    if cap:
        # real table rows land in two windows; one sighting means the cap came
        # from a stray number, and a false cap flags a correct invoice
        votes = rs.setdefault("_cap_votes", {})
        k = f"{name}|{cap}"
        votes[k] = votes.get(k, 0) + 1
        if votes[k] >= 2:
            rs["daily_caps"][name] = cap

    tp = coerce_obj(svc.get("threshold_premium"),
                    "daily_qty_threshold", "uplift_percent")
    if tp:
        n, pct = coerce_int(tp["daily_qty_threshold"]), coerce_pct(tp["uplift_percent"])
        if n and pct:
            rs["threshold_premiums"][name] = {"daily_qty_threshold": n, "uplift": pct}

    up = coerce_pct(svc.get("nonbusiness_uplift_percent"))
    if up:
        rs["nonbusiness_uplifts"][name] = up

    vds = svc.get("volume_discounts") or []
    if isinstance(vds, dict):
        vds = [vds]
    for vd in vds:
        vd = coerce_obj(vd, "threshold", "discount_percent")
        if not vd:
            continue
        n, pct = coerce_int(vd["threshold"]), coerce_pct(vd["discount_percent"])
        # half a tier is dropped, not defaulted
        if n and pct and [n, pct] not in rs["volume_discounts"].get(name, []):
            rs["volume_discounts"].setdefault(name, []).append([n, pct])

    b = coerce_obj(svc.get("bundle"), "partner_service",
                   "this_bundled_rate_gbp", "partner_bundled_rate_gbp")
    if b:
        ra = coerce_cents(b["this_bundled_rate_gbp"])
        rb = coerce_cents(b["partner_bundled_rate_gbp"])
        partner = str(b["partner_service"]).strip()
        if ra and rb and partner:
            pair = {partner, name}      # unordered: both services report it
            if not any({x["service_a"], x["service_b"]} == pair for x in rs["bundles"]):
                rs["bundles"].append({"service_a": name, "service_b": partner,
                                      "rate_a_cents": ra, "rate_b_cents": rb})

    ex = coerce_obj(svc.get("exclusion"), "trigger_service", "window_days")
    if ex:
        days = coerce_int(ex["window_days"])
        trigger = str(ex["trigger_service"]).strip()
        if days and trigger and not any(
                x["excluded_service"] == name and x["trigger_service"] == trigger
                for x in rs["exclusions"]):
            rs["exclusions"].append({"excluded_service": name,
                                     "window_days": days,
                                     "trigger_service": trigger})


def validate(rs, source_text):
    """-> list of failures; empty means usable."""
    errors = []

    # named in a rule but no rate: that clause wasn't extracted
    known = set(rs["rates"])
    named = (set(rs["threshold_premiums"]) | set(rs["nonbusiness_uplifts"])
             | set(rs["volume_discounts"]) | set(rs["daily_caps"]))
    for b in rs["bundles"]:
        named |= {b["service_a"], b["service_b"]}
    for e in rs["exclusions"]:
        named |= {e["excluded_service"], e["trigger_service"]}
    for orphan in sorted(named - known):
        errors.append(f"in a rule but not in the rate table: {orphan!r}")

    if len(rs["rates"]) < 20:
        errors.append(f"only {len(rs['rates'])} rates from a contract with "
                      f"{source_text.count('GBP')} GBP mentions")

    # a full rate table can arrive with whole rule tables missing, so count
    # each type. every contract here states all six.
    for label, count in (("threshold premiums", len(rs["threshold_premiums"])),
                         ("nonbusiness uplifts", len(rs["nonbusiness_uplifts"])),
                         ("volume discounts", len(rs["volume_discounts"])),
                         ("daily caps", len(rs["daily_caps"])),
                         ("bundles", len(rs["bundles"])),
                         ("exclusions", len(rs["exclusions"]))):
        if count == 0:
            errors.append(f"zero {label} -- implausible, check window size")

    return errors


def build(hospital_id, window_size=30, strict=True):
    """Extract, validate, write. Raises on a gate failure unless strict is off.

    window_size: larger loses rules near window edges, smaller cuts clauses.
    """
    print(f"extracting {hospital_id} by LLM")
    rs = empty_ruleset(hospital_id)
    rejected = []
    template = (config.PROMPTS_DIR / "extract_v1.md").read_text(encoding="utf-8")

    for path in config.contract_paths(hospital_id):
        text = path.read_text(encoding="utf-8")
        chunks = list(windows(text, window_size))
        print(f"  {path.name}: {len(chunks)} windows")
        for i, chunk in enumerate(chunks, 1):
            print(f"    window {i}/{len(chunks)}")
            data = call_llm(SYSTEM, template.replace("{{TEXT}}", chunk))
            # top-level key varies, so find the list by shape
            for svc in find_list(data, "service_name"):
                if isinstance(svc, dict):
                    merge(rs, svc, text, rejected)

    for s in rs["volume_discounts"]:
        rs["volume_discounts"][s].sort(key=lambda t: -t[0])   # deepest first

    print(f"\n  services {len(rs['rates'])}  premiums {len(rs['threshold_premiums'])}"
          f"  uplifts {len(rs['nonbusiness_uplifts'])}"
          f"  discounts {sum(len(v) for v in rs['volume_discounts'].values())}"
          f"  caps {len(rs['daily_caps'])}  bundles {len(rs['bundles'])}"
          f"  exclusions {len(rs['exclusions'])}  amendments {len(rs['amendments'])}")

    if rejected:
        print(f"\n  {len(rejected)} rejected:")
        for r in rejected[:15]:
            print(f"    {r}")

    all_text = "\n".join(p.read_text(encoding="utf-8")
                         for p in config.contract_paths(hospital_id))
    errors = validate(rs, all_text)
    if errors:
        print(f"\n  {len(errors)} GATE FAILURE(S):")
        for e in errors[:15]:
            print(f"    {e}")
        if strict:
            raise AssertionError("extraction failed validation, fix before pricing")

    rs.pop("_cap_votes", None)
    out = config.ruleset_path(hospital_id)
    out.write_text(json.dumps(rs, indent=2), encoding="utf-8")
    print(f"\n  written to {out}")
    return rs


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--hospital", required=True)
    p.add_argument("--window", type=int, default=30)
    p.add_argument("--no-strict", action="store_true")
    a = p.parse_args()
    build(a.hospital, a.window, strict=not a.no_strict)
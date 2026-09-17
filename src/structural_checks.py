"""
structural_checks.py -- validations needing only invoice data and the
contract header. No rate table, no mapping, no pricing.
"""

from collections import Counter, defaultdict

from pricing_engine import parse_date


def check_duplicate_invoice_ids(records):
    """Invoice ids used by more than one record."""
    counts = Counter(r["invoice_id"] for r in records)
    return {inv_id for inv_id, n in counts.items() if n > 1}


def check_contract_number(record, expected):
    return record.get("contract_number") != expected


def check_line_arithmetic(record):
    return any(li["quantity"] * li["unit_price_cents"] != li["line_total_cents"]
               for li in record["line_items"])


def check_invoice_total(record):
    return record["invoice_total_cents"] != sum(
        li["line_total_cents"] for li in record["line_items"])


def check_service_dates(record, term_start, term_end):
    """-> [(category, line_id), ...] for every date problem on the invoice."""
    findings = []
    inv_date = parse_date(record.get("invoice_date"))
    for li in record["line_items"]:
        d = parse_date(li.get("service_date"))
        if d is None:
            findings.append(("malformed_service_date", li["line_id"]))
            continue
        if d < term_start or d > term_end:
            findings.append(("service_date_out_of_window", li["line_id"]))
        if inv_date and d > inv_date:
            findings.append(("service_date_after_invoice_date", li["line_id"]))
    return findings


def check_intra_invoice_duplicates(record, service_of):
    """Same service, patient and date twice on one invoice."""
    seen = defaultdict(list)
    for li in record["line_items"]:
        svc = service_of.get(li["line_id"])
        if svc is None:
            continue
        seen[(svc, li["service_date"])].append(li["line_id"])
    return any(len(ids) > 1 for ids in seen.values())


def build_cross_invoice_index(records, service_of):
    """Invoice ids that re-bill a service already billed for that patient and
    date. Only the later invoice of each group: the first billing is the
    legitimate one. Ordered by invoice date, id as tiebreak."""
    index = defaultdict(list)
    for r in records:
        for li in r["line_items"]:
            svc = service_of.get(li["line_id"])
            if svc is None:
                continue
            entry = (r.get("invoice_date") or "", r["invoice_id"])
            key = (r["patient_id"], svc, li["service_date"])
            if entry not in index[key]:
                index[key].append(entry)

    offenders = set()
    for entries in index.values():
        if len(entries) < 2:
            continue
        entries.sort()
        offenders.update(inv_id for _d, inv_id in entries[1:])
    return offenders
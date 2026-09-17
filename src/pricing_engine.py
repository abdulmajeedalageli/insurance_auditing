"""
pricing_engine.py -- applies contract rules to one line item.

No service names or rates are held here; everything comes from the ruleset
JSON, so one class serves all hospitals. Adjustment order is fixed by contract:
bundle, facility, plan tier, premium/uplift, volume discount, rounding to a
whole cent after each step.
"""

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP


class Unpriceable(Exception):
    """A line that cannot be priced. Signals abstention, not a finding."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def round_half_up(value):
    """Nearest cent, exact halves away from zero."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_date(text):
    """None for anything that is not a real calendar date."""
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


class PricingEngine:
    """Stateful: cumulative utilisation accrues as lines are priced, so lines
    must be supplied in service-date order."""

    def __init__(self, ruleset):
        rs = ruleset
        self.rates = rs["rates"]                  # svc -> {rate_cents, unit_basis}
        self.premiums = rs["threshold_premiums"]  # svc -> {daily_qty_threshold, uplift}
        self.uplifts = rs["nonbusiness_uplifts"]  # svc -> fraction, e.g. "0.20"
        self.discounts = rs["volume_discounts"]   # svc -> [[threshold, pct], ...]
        self.caps = rs["daily_caps"]              # svc -> max units per patient-day
        self.exclusions = rs["exclusions"]
        self.term_start = date.fromisoformat(rs["header"]["term_start"])
        self.term_end = date.fromisoformat(rs["header"]["term_end"])

        # keyed on the unordered pair so lookup works from either service
        self.bundle_index = {}
        for b in rs["bundles"]:
            self.bundle_index[frozenset((b["service_a"], b["service_b"]))] = {
                b["service_a"]: b["rate_a_cents"],
                b["service_b"]: b["rate_b_cents"],
            }

        # sorted latest effective date first; empty for contracts with no
        # amendments
        self.amended = defaultdict(list)
        for a in rs.get("amendments", []):
            if a.get("effective_from"):
                self.amended[a["service"]].append(
                    (date.fromisoformat(a["effective_from"]), a["rate_cents"]))
        for svc in self.amended:
            self.amended[svc].sort(key=lambda t: t[0], reverse=True)

        self.cumulative = defaultdict(int)   # svc -> units billed so far

        # populated by build_context
        self.daily_qty = {}
        self.daily_services = {}
        self.service_dates = {}
        self._context_built = False

    def base_rate(self, service, svc_date):
        """Rate in force on the service date. Amendments apply by service date,
        not invoice date."""
        for eff_from, rate in self.amended.get(service, []):
            if svc_date >= eff_from:
                return rate
        return self.rates[service]["rate_cents"]

    def build_context(self, lines):
        """Build the patient-day aggregates. Requires every line in the
        dataset: bundles and exclusion windows span invoices."""
        self.daily_qty = defaultdict(int)       # (patient, date, svc) -> units
        self.daily_services = defaultdict(set)  # (patient, date) -> {svc}
        self.service_dates = defaultdict(set)   # (patient, svc) -> {date}

        for ln in lines:
            svc, d = ln.get("service"), ln.get("date")
            if svc is None or d is None:
                continue
            self.daily_qty[(ln["patient_id"], d, svc)] += ln["quantity"]
            self.daily_services[(ln["patient_id"], d)].add(svc)
            self.service_dates[(ln["patient_id"], svc)].add(d)

        self._context_built = True

    def _bundled_rate(self, patient_id, svc_date, service):
        """Bundled rate where the partner service shares the patient-day."""
        same_day = self.daily_services.get((patient_id, svc_date), set())
        for pair, rates in self.bundle_index.items():
            if service in pair and pair <= same_day:
                return rates[service]
        return None

    def exclusion_violation(self, patient_id, svc_date, service):
        """Trigger service name if the line falls inside an exclusion window.
        The window extends both directions from the trigger date."""
        for ex in self.exclusions:
            if ex["excluded_service"] != service:
                continue
            for other in self.service_dates.get((patient_id, ex["trigger_service"]), set()):
                if abs((svc_date - other).days) <= ex["window_days"]:
                    return ex["trigger_service"]
        return None

    def cap_excess(self, patient_id, svc_date, service):
        """Units above the daily cap, assessed on the patient-day aggregate
        rather than the quantity of a single line."""
        cap = self.caps.get(service)
        if cap is None:
            return 0
        return max(0, self.daily_qty.get((patient_id, svc_date, service), 0) - cap)

    def price_line(self, line, apply_caps=True):
        """Price one line.

        line: dict with service, ledger_service, date, patient_id, quantity.
              service is None where the matcher abstained; ledger_service holds
              the best candidate and is used only for counting.

        Returns (expected_line_total_cents, notes), where notes records each
        rule applied together with the figures that triggered it.
        """
        if not self._context_built:
            raise RuntimeError("build_context() must be called with the full dataset")

        service = line.get("service")
        svc_date = line.get("date")
        qty = line["quantity"]
        notes = []

        # unpriceable lines still advance the ledger: the units were billed and
        # count toward later volume-discount tiers
        if service is None:
            ledger_svc = line.get("ledger_service")
            if ledger_svc in self.discounts:
                self.cumulative[ledger_svc] += qty
            raise Unpriceable("unmapped_service")
        if service not in self.rates:
            raise Unpriceable("uncontracted_service")
        if svc_date is None:
            if service in self.discounts:
                self.cumulative[service] += qty
            raise Unpriceable("malformed_service_date")

        bundled = self._bundled_rate(line["patient_id"], svc_date, service)
        base = self.base_rate(service, svc_date)
        if bundled is not None:
            rate = Decimal(bundled)
            notes.append(f"bundled_rate {bundled}c substituted for base {base}c")
        else:
            rate = Decimal(base)

        # facility and plan-tier multipliers are 1.0 under these contracts

        # premiums and caps are assessed on the patient-day aggregate
        daily_total = self.daily_qty.get((line["patient_id"], svc_date, service), 0)

        prem = self.premiums.get(service)
        if prem and daily_total > prem["daily_qty_threshold"]:
            rate = Decimal(round_half_up(rate * (Decimal(1) + Decimal(prem["uplift"]))))
            notes.append(f"threshold_premium: day total {daily_total} > "
                         f"threshold {prem['daily_qty_threshold']}, uplift {prem['uplift']}")

        uplift = self.uplifts.get(service)
        if uplift and svc_date.weekday() >= 5:
            rate = Decimal(round_half_up(rate * (Decimal(1) + Decimal(uplift))))
            notes.append(f"nonbusiness_uplift: {svc_date} is a "
                         f"{'Saturday' if svc_date.weekday() == 5 else 'Sunday'}, "
                         f"uplift {uplift}")

        # discount tiers run on utilisation accrued before this line
        tiers = self.discounts.get(service)
        if tiers:
            prior = self.cumulative[service]
            for threshold, pct in tiers:            # deepest tier first
                if prior > threshold:
                    rate = Decimal(round_half_up(rate * (Decimal(1) - Decimal(pct))))
                    notes.append(f"volume_discount: prior utilisation {prior} > "
                                 f"threshold {threshold}, discount {pct}")
                    break

        self.cumulative[service] += qty

        billable_qty = qty
        if apply_caps:
            excess = self.cap_excess(line["patient_id"], svc_date, service)
            if excess:
                # units above the cap are treated as not billable; the
                # remainder prices normally
                billable_qty = qty - min(excess, qty)
                notes.append(f"daily_cap_exceeded: day total {daily_total} > "
                             f"cap {self.caps[service]}, {min(excess, qty)} unbillable")

        trigger = self.exclusion_violation(line["patient_id"], svc_date, service)
        if trigger:
            notes.append(f"exclusion_window_violation: within window of {trigger}")
            billable_qty = 0

        return round_half_up(rate * Decimal(billable_qty)), notes
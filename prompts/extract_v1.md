# extract v1

You are reading one window of a healthcare reimbursement contract. Report every
contracted service you find in this window, with all terms attached to it.

The window may contain a rate table, prose clauses, a premium or discount
schedule, a bundle table, an exclusion table, or nothing relevant at all. If the
window contains no service terms, return {"services": []}.

## Rules

1. Copy the rate EXACTLY as written: "1,301.25". Do not convert, round or
   reformat. If you change it, it will be rejected.
2. Copy the service name exactly as written. Do not normalise capitalisation,
   expand abbreviations, or correct apparent typos.
3. unit_basis must be copied verbatim, and will be one of:
   "per hour", "per day of service", "per night of occupancy", "per visit",
   "per test", "per procedure", "per item supplied", "per unit dispensed",
   "per hour, per item".
4. Report a term ONLY if this window states it. If nothing is said about a
   daily cap, return null. Never infer a term from another service or from what
   is typical.
5. Numbers written as words are followed by digits in parentheses
   ("twenty-four (24) days"). Use the digits.
6. If the window is a table listing only one kind of rule (for example a table
   of premiums, or a table of caps), report one entry per row with the
   service_name and that rule filled in, and rate_gbp set to null.
7. Distinguish carefully:
   - daily_cap: maximum units billable per patient per service day
   - threshold_premium: rate increases when the day's aggregate quantity
     exceeds a threshold
   - nonbusiness_uplift_percent: rate increases when the service date is not a
     business day
   - volume_discounts: rate decreases once cumulative utilisation passes a
     threshold
   - bundle: two named services delivered to the same patient on the same day
     are both repriced
   - exclusion: this service is not billable within N days of another service
8. If an amendment states a new rate effective from a date, set
   effective_from to that date in YYYY-MM-DD form.

## Output

Return a single JSON object with exactly one top-level key, "services". Use
these key names exactly; do not rename them.

{"services": [
  {"service_name": "Advanced Infectious Isolation Room Occupancy",
   "rate_gbp": "1,648.25",
   "unit_basis": "per day of service",
   "effective_from": null,
   "daily_cap": 24,
   "threshold_premium": {"daily_qty_threshold": 6, "uplift_percent": 30},
   "nonbusiness_uplift_percent": null,
   "volume_discounts": [{"threshold": 100, "discount_percent": 10}],
   "bundle": {"partner_service": "Bedside Renal Wound Care",
              "this_bundled_rate_gbp": "1,500.00",
              "partner_bundled_rate_gbp": "120.25"},
   "exclusion": {"trigger_service": "Advanced Geriatric Nutritional Support",
                 "window_days": 7}}
]}

## Contract window

{{TEXT}}

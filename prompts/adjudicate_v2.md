# adjudicate v2

Used only for descriptions the deterministic matcher abstained on. Bounded
work: on H1 this is ~20 descriptions out of 488.

## Task

For each item you are given a billing description and a shortlist of candidate
contracted services. Choose the correct one, or decline.

## Rules

1. Answer with a service name copied EXACTLY from the candidate list, or the
   literal string "CANNOT_DETERMINE".
2. Clinical adjectives are discriminating and must not be substituted.
   "Intensive" is not "Intermittent"; "Ambulatory" is not "Assisted".
3. If the description omits the word that separates two candidates, answer
   "CANNOT_DETERMINE". Do not use price, quantity or plausibility to break the
   tie -- the audit is checking the price, so using it to pick the service
   makes the check circular.
4. A description naming a service absent from the candidate list is
   "CANNOT_DETERMINE". That is a real finding (the hospital billed something
   uncontracted), not a failure to match.
5. Ignore alphanumeric reference tags such as '/NG-3022'.

## Output

JSON only:

{"decisions": [{"description": "...", "service": "...", "reasoning": "..."}]}

## Items

{{ITEMS}}

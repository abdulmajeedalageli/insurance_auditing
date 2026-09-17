"""
llm_mapper.py -- billing description to contracted service name.

Service names are {Modifier} {Speciality} {ServiceType}; descriptions are
abbreviated permutations of those tokens, so matching is token-based. The
abbreviation table is learned per hospital by bootstrap_aliases.

Unresolvable descriptions are left unassigned rather than guessed. 'Visit Amb
Hm' scores identically against Ambulatory Cardiac and Ambulatory Infectious
Home Visit -- the distinguishing word is absent from the text. --adjudicate
sends that residue to the model.
"""

import json
import re
import time
from dataclasses import dataclass

import config
from llm_client import call_llm

STOPWORDS = {"of", "the", "a", "per", "and"}
TAG_RE = re.compile(r"/[A-Z]{2}-\d+", re.IGNORECASE)   # e.g. /NG-3022
PUNCT_RE = re.compile(r"[^a-z0-9]+")

# Two expansions where the abbreviation is genuinely ambiguous: 'Obs Metab
# Nursing' is observation, 'Obs Diag Img' is obstetric. Both are kept and the
# scorer picks per candidate.
SEED_ALIASES = {
    "amb": ["ambulatory"], "adv": ["advanced"], "asst": ["assisted"],
    "comp": ["comprehensive"], "cont": ["continuous"], "emerg": ["emergency"],
    "ext": ["extended"], "inpt": ["inpatient"], "outpt": ["outpatient"],
    "postop": ["postoperative"], "preop": ["preoperative"], "std": ["standard"],
    "spclst": ["specialist"], "supv": ["supervised"], "intens": ["intensive"],
    "obs": ["obstetric", "observation"], "cr": ["care", "critical"],
    "int": ["intensive", "intermittent"],
}


def tokenise(text, aliases):
    """-> [(token, *expansions), ...]"""
    text = PUNCT_RE.sub(" ", TAG_RE.sub(" ", text).lower())
    out = []
    for tok in text.split():
        if tok in STOPWORDS:
            continue
        exp = aliases.get(tok, [])
        out.append((tok, *([exp] if isinstance(exp, str) else exp)))
    return out


def _token_matches(desc_alts, svc_tok):
    return any(alt == svc_tok or (len(alt) >= 3 and svc_tok.startswith(alt))
               for alt in desc_alts)


@dataclass
class Match:
    service: str | None     # None where the matcher abstained
    best: str | None        # top candidate regardless of thresholds, for the ledger
    score: float
    margin: float
    runner_up: str | None
    reason: str             # matched | ambiguous | weak | no_candidate | llm_resolved


class SemanticMapper:
    """Thresholds are fitted on H1 and carried to hospitals without labels.

    unknown_below  below this, the description names no contracted service --
                   a positive claim, reported as a finding
    min_score      above unknown_below but below this, too weak to assert;
                   not reported, since failing to match is not evidence of error
    min_margin     two services fit within this gap, so the description lacks
                   the word that separates them
    """

    def __init__(self, valid_services, hospital_id, aliases=None,
                 min_score=0.70, min_margin=0.06, unknown_below=0.50):
        self.valid_services = list(valid_services)
        self.hospital_id = hospital_id
        self.aliases = {k: list(v) for k, v in SEED_ALIASES.items()}
        for k, v in (aliases or {}).items():
            self.aliases.setdefault(k, []).extend([v] if isinstance(v, str) else v)
            self.aliases[k] = sorted(set(self.aliases[k]))
        self.min_score = min_score
        self.min_margin = min_margin
        self.unknown_below = unknown_below
        self.service_tokens = {s: tokenise(s, self.aliases) for s in self.valid_services}
        self._cache = {}
        self._adjudications = self._load(config.adjudication_cache_path(hospital_id))

    @classmethod
    def bootstrap_aliases(cls, valid_services, descriptions, rounds=4,
                          min_support=2, verbose=True):
        """Derive the abbreviation table from the descriptions.

        Where a confidently-matched pair leaves exactly one unmatched token on
        each side, those two are proposed as an alias ('ent' / 'otolaryngologic').
        min_support independent descriptions must produce the same alignment
        before it is accepted. Rounds compound: new aliases raise scores, which
        exposes further alignments.
        """
        services = list(valid_services)
        table = {k: list(v) for k, v in SEED_ALIASES.items()}
        uniq = sorted(set(descriptions))

        for rnd in range(1, rounds + 1):
            svc_tokens = {s: tokenise(s, table) for s in services}
            proposals = {}

            for desc in uniq:
                dts = tokenise(desc, table)
                if not dts:
                    continue
                scored = []
                for svc in services:
                    flat = [t[0] for t in svc_tokens[svc]]
                    hs = sum(1 for st in flat if any(_token_matches(dt, st) for dt in dts))
                    hd = sum(1 for dt in dts if any(_token_matches(dt, st) for st in flat))
                    r, pr = hs / len(flat), hd / len(dts)
                    scored.append(((2 * r * pr / (r + pr)) if r and pr else 0.0, svc))
                scored.sort(reverse=True)
                best = scored[0]
                second = scored[1] if len(scored) > 1 else (0.0, None)

                # learn only from pairs that match well and clearly beat the
                # runner-up; a wrong alias here propagates into every round after
                if best[0] < 0.55 or best[0] - second[0] < 0.10:
                    continue

                flat = [t[0] for t in svc_tokens[best[1]]]
                un_d = [dt for dt in dts if not any(_token_matches(dt, st) for st in flat)]
                un_s = [st for st in flat if not any(_token_matches(dt, st) for dt in dts)]
                if len(un_d) == 1 and len(un_s) == 1:
                    abbr, full = un_d[0][0], un_s[0]
                    if abbr != full and len(abbr) >= 2 and abbr not in table.get(full, []):
                        proposals[(abbr, full)] = proposals.get((abbr, full), 0) + 1

            accepted = 0
            for (abbr, full), support in proposals.items():
                if support >= min_support and full not in table.get(abbr, []):
                    table.setdefault(abbr, []).append(full)
                    accepted += 1

            if verbose:
                print(f"  bootstrap round {rnd}: {accepted} new aliases ({len(table)} total)")
            if accepted == 0:
                break

        return {k: sorted(set(v)) for k, v in table.items()}

    def _score(self, desc_tokens, svc_tokens):
        """F1 over token coverage in both directions."""
        if not desc_tokens or not svc_tokens:
            return 0.0
        flat = [st[0] for st in svc_tokens]
        svc_hits = sum(1 for st in flat if any(_token_matches(dt, st) for dt in desc_tokens))
        desc_hits = sum(1 for dt in desc_tokens if any(_token_matches(dt, st) for st in flat))
        recall, precision = svc_hits / len(flat), desc_hits / len(desc_tokens)
        if not recall or not precision:
            return 0.0
        return 2 * recall * precision / (recall + precision)

    def match(self, description):
        if description in self._cache:
            return self._cache[description]

        resolved = self._adjudications.get(description)
        if resolved and resolved in self.valid_services:
            result = Match(resolved, resolved, 1.0, 1.0, None, "llm_resolved")
            self._cache[description] = result
            return result

        dt = tokenise(description, self.aliases)
        scored = sorted(((self._score(dt, self.service_tokens[s]), s)
                         for s in self.valid_services), reverse=True)
        best_score, best = scored[0]
        second_score, second = scored[1] if len(scored) > 1 else (0.0, None)
        margin = best_score - second_score

        if best_score < self.unknown_below:
            # best is None: an uncontracted description must not accrue units
            # toward any service's volume ledger
            result = Match(None, None, best_score, margin, best, "no_candidate")
        elif best_score < self.min_score:
            result = Match(None, best, best_score, margin, best, "weak")
        elif margin < self.min_margin:
            result = Match(None, best, best_score, margin, second, "ambiguous")
        else:
            result = Match(best, best, best_score, margin, second, "matched")

        self._cache[description] = result
        return result

    def map_all(self, descriptions):
        return {d: self.match(d) for d in set(descriptions)}

    def adjudicate_unresolved(self, descriptions, batch_size=None):
        """Send unresolved descriptions to the model with their five best
        candidates. Answers are validated against the service list;
        CANNOT_DETERMINE is accepted, so the model is never forced to choose.
        Cached to disk.
        """
        batch_size = batch_size or config.BATCH_SIZE
        pending = []
        for d in sorted(set(descriptions)):
            if d in self._adjudications:
                continue
            if self.match(d).reason in ("ambiguous", "no_candidate", "weak"):
                shortlist = sorted(
                    ((self._score(tokenise(d, self.aliases), self.service_tokens[s]), s)
                     for s in self.valid_services), reverse=True)[:5]
                pending.append({"description": d,
                                "candidates": [s for _sc, s in shortlist]})

        if not pending:
            print("  nothing to adjudicate")
            return self._adjudications

        print(f"  adjudicating {len(pending)} unresolved descriptions")
        prompt = (config.PROMPTS_DIR / "adjudicate_v2.md").read_text(encoding="utf-8")

        for i in range(0, len(pending), batch_size):
            batch = pending[i:i + batch_size]
            print(f"    batch {i // batch_size + 1}/"
                  f"{(len(pending) - 1) // batch_size + 1}")
            data = call_llm(
                "You are a medical billing analyst. Output only valid JSON.",
                prompt.replace("{{ITEMS}}", json.dumps(batch, indent=1)))
            for dec in data.get("decisions", []):
                svc = str(dec.get("service", "")).strip()
                if svc in self.valid_services:
                    self._adjudications[dec["description"]] = svc
                else:
                    print(f"      declined: {dec.get('description')!r} -> {svc!r}")
            self._save(config.adjudication_cache_path(self.hospital_id),
                       self._adjudications)
            time.sleep(2)

        self._cache.clear()     # adjudications change what match() returns
        return self._adjudications

    @staticmethod
    def _load(path):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _save(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
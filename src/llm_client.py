"""
llm_client.py -- HTTP transport and output coercion for every model call.

Responses are cached on a hash of the prompt, so re-runs and interrupted runs
cost nothing. Model output varies in shape between identical calls, so the
payload is located by structure rather than by key name and values are coerced
rather than trusted.
"""

import hashlib
import json
import re
import time
from decimal import Decimal



import config


def _cache_path(system_prompt, user_prompt):
    h = hashlib.sha256()
    for part in (config.MODEL, system_prompt, user_prompt):
        h.update(part.encode())
    return config.LLM_CACHE_DIR / f"{h.hexdigest()[:32]}.json"


def call_llm(system_prompt, user_prompt, use_cache=True):
    """Post to the chat completions endpoint and return parsed JSON.

    Retries with exponential backoff on 429 only. Other 4xx responses are
    raised immediately with the body attached.
    """
    path = _cache_path(system_prompt, user_prompt)
    if use_cache and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass        # corrupt entry, refetch
    import requests
    headers = {"Authorization": f"Bearer {config.require_api_key()}",
               "Content-Type": "application/json"}
    payload = {
        "model": config.MODEL,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
        "temperature": config.TEMPERATURE,
        # json_schema mode requires strict schemas: every property listed in
        # required, additionalProperties false. The extraction schema has
        # optional fields by design, so output is validated in code instead.
        "response_format": {"type": "json_object"},
    }

    delay = config.INITIAL_BACKOFF
    last_error = None

    for attempt in range(config.MAX_RETRIES):
        try:
            resp = requests.post(config.API_URL, headers=headers,
                                 json=payload, timeout=300)

            if resp.status_code == 429:
                print(f"      rate limited, waiting {delay}s "
                      f"({attempt + 1}/{config.MAX_RETRIES})")
                time.sleep(delay)
                delay *= 2
                continue

            # a malformed request will fail the same way on every retry
            if 400 <= resp.status_code < 500:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:600]}")

            resp.raise_for_status()

            choice = (resp.json().get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content")
            if not text:
                # 200 with an empty body, usually finish_reason=length
                raise RuntimeError(
                    f"empty response (finish_reason={choice.get('finish_reason')}), "
                    f"batch probably too big")

            text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
            data = json.loads(re.sub(r"\s*```$", "", text))

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
            return data

        except RuntimeError:
            raise
        except Exception as exc:    # noqa: BLE001
            last_error = exc
            print(f"      error attempt {attempt + 1}: {exc}")
            if attempt < config.MAX_RETRIES - 1:
                time.sleep(delay)
                delay *= 2

    raise RuntimeError(f"failed after {config.MAX_RETRIES} attempts: {last_error}")


def find_list(data, *required_keys):
    """First top-level list of dicts carrying all required_keys, else [].

    The envelope key varies between identical calls -- 'services', 'terms',
    'items' -- so the payload is identified by shape. There is deliberately no
    fallback to any list of dicts: that admits unrelated output into the
    ruleset.
    """
    candidates = [data] if isinstance(data, list) else \
                 [v for v in data.values() if isinstance(v, list)]
    for value in candidates:
        if value and isinstance(value[0], dict) and \
                all(k in value[0] for k in required_keys):
            return value
    return []


def coerce_int(value):
    """'sixty (60) visits' -> 60.

    Contracts write numbers as words with the digits in parentheses. The prompt
    asks for digits; the whole phrase comes back often enough to parse for.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value)
    m = re.search(r"\((\d+)\)", s) or re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None


def coerce_pct(value):
    """'twenty percent (20%)' / '20%' / 20 / 0.20 -> '0.20'"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = Decimal(str(value))
    else:
        s = str(value)
        m = (re.search(r"\((\d+(?:\.\d+)?)\s*%?\)", s)
             or re.search(r"(\d+(?:\.\d+)?)", s))
        if not m:
            return None
        num = Decimal(m.group(1))
    if num <= 0:
        return None
    if num >= 1:            # 20 means 20%, 0.20 is already a fraction
        num = num / Decimal(100)
    return str(num)


def coerce_cents(value):
    """'GBP 1,648.25' -> 164825. Input is pounds, as stated in contract text."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int((Decimal(str(value)) * 100).to_integral_value())
    s = str(value).replace("GBP", "").replace("£", "").replace(",", "").strip()
    m = re.search(r"(\d+(?:\.\d{1,2})?)", s)
    return int((Decimal(m.group(1)) * 100).to_integral_value()) if m else None


def as_cents(value):
    """For fields already denominated in cents. Never scales."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    m = re.search(r"-?\d+", str(value).replace(",", ""))
    return int(m.group(0)) if m else None


def coerce_obj(value, *required_keys):
    """The dict if it carries all required_keys, else None.

    A malformed nested term is dropped rather than repaired: a missing rule
    costs recall on one category, an invented one costs precision on every
    invoice touching that service.
    """
    if isinstance(value, dict) and all(k in value for k in required_keys):
        return value
    return None


def normalise_keys(obj, aliases):
    return {aliases.get(k, k): v for k, v in obj.items()}
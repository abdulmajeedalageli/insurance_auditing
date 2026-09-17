"""src/config.py -- paths, model settings, hospital registry."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INVOICES_DIR = BASE_DIR / "invoices"
CONTRACTS_DIR = BASE_DIR / "contracts"
LABELS_DIR = BASE_DIR / "labels"
PROMPTS_DIR = BASE_DIR / "prompts"
CACHE_DIR = BASE_DIR / "cache"
LLM_CACHE_DIR = CACHE_DIR / "llm"
OUTPUT_DIR = BASE_DIR / "output"

for _d in (CACHE_DIR, LLM_CACHE_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

API_URL = os.environ.get("LLM_API_URL", "https://api.cerebras.ai/v1/chat/completions")
API_KEY = os.environ.get("CEREBRAS_API_KEY")
MODEL = os.environ.get("LLM_MODEL", "qwen-3.8-27b")
TEMPERATURE = 0.0
BATCH_SIZE = 30
MAX_RETRIES = 6
INITIAL_BACKOFF = 30      # 20 wasn't enough, kept exhausting retries


def require_api_key():
    if not API_KEY:
        raise RuntimeError("API_KEY is not set.")
    return API_KEY


HOSPITALS = {
    "hospital_1": {
        "contract_number": "INS-H1-2024-0417",
        "term": ("2024-01-01", "2025-12-31"),
        "documents": ["provider_services_agreement.txt"],
    },
    "hospital_2": {
        "contract_number": "INS-H2-2024-1183",
        "term": ("2024-01-01", "2025-12-31"),
        "documents": ["master_services_agreement.txt"],
    },
    "hospital_3": {
        "contract_number": "INS-H3-2024-0562",
        "term": ("2024-01-01", "2025-12-31"),
        "documents": ["base_agreement.txt", "appendix_b_rate_schedule.txt",
                      "amendment_no_1.txt"],
    },
}


def hospital_config(h):
    if h not in HOSPITALS:
        raise KeyError(f"Unknown hospital {h!r}. Known: {list(HOSPITALS)}")
    return HOSPITALS[h]


def contract_paths(h):
    return [CONTRACTS_DIR / h / n for n in hospital_config(h)["documents"]]


def ruleset_path(h): return CONTRACTS_DIR / h / "ruleset_llm.json"
def invoices_path(h): return INVOICES_DIR / f"{h}_invoices.jsonl"
def labels_path(h): return LABELS_DIR / f"{h}_labels.csv"
def submission_path(h): return OUTPUT_DIR / f"submission_{h}.csv"
def alias_cache_path(h): return CACHE_DIR / f"{h}_aliases.json"
def adjudication_cache_path(h): return CACHE_DIR / f"{h}_adjudications.json"



UNIT_BASIS_MAP = {
    "per hour": "per_hour",
    "per day of service": "per_day",
    "per night of occupancy": "per_night",
    "per visit": "per_visit",
    "per test": "per_test",
    "per procedure": "per_procedure",
    "per item supplied": "per_item",
    "per unit dispensed": "per_unit_dispensed",
    "per hour, per item": "per_hour_per_item",
}

VALID_UNIT_CODES = set(UNIT_BASIS_MAP.values())
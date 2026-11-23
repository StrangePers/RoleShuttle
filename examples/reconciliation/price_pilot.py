"""Reprice the recorded pilot stages at dated Standard short-context USD rates."""
from decimal import Decimal
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# USD per million tokens: uncached input, cached input, output.
# OpenAI Standard text token rates checked 2026-11-23.
RATES = {
    "gpt-6.2-astra": ("8.00", "0.40", "40.00"),
    "gpt-6.2-sol": ("2.00", "0.10", "10.00"),
    "gpt-6-luna": ("0.10", "0.01", "0.50"),
}


def cost(stage):
    usage = stage["usage"]
    if usage["cache_write_input_tokens"] != 0:
        raise ValueError("This pilot calculation assumes zero billed cache writes")
    fresh = usage["input_tokens"] - usage["cached_input_tokens"]
    quantities = (fresh, usage["cached_input_tokens"], usage["output_tokens"])
    return sum(q * Decimal(rate) for q, rate in zip(quantities, RATES[stage["model"]])) / 1_000_000


if __name__ == "__main__":
    data = json.loads((ROOT / "usage.json").read_text())
    stages = {s["stage"]: cost(s) for s in [*data["stages"], *data["follow_up"]["stages"]]}
    routes = {
        "Astra alone": ["solo"],
        "Sol + Astra": ["worker", "reviewer"],
        "Luna implementation stage": ["luna"],
        "Luna + Astra": ["luna", "luna-reviewer"],
        "CSV / Luna alone": ["csv-luna"],
        "CSV / Astra alone": ["csv-astra"],
    }
    for name, ids in routes.items():
        total = sum(stages[key] for key in ids)
        print(f"{name:28} ${total:.7f}")

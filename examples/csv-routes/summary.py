"""Recompute route totals and means from the sanitized subscription receipts."""
from decimal import Decimal
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Standard short-context USD per million uncached input, cached input and output tokens.
RATES = {"gpt-6-luna": (".10", ".01", ".50"), "gpt-6.2-astra": ("8", ".40", "40")}


def price(call):
    u = call["usage"]
    if u["cacheWriteInputTokens"] != 0:
        raise ValueError("This trial's price calculation requires zero cache writes")
    fresh = u["inputTokens"] - u["cachedInputTokens"]
    if min(fresh, u["cachedInputTokens"], u["outputTokens"]) < 0:
        raise ValueError("Invalid token counters")
    qty = (fresh, u["cachedInputTokens"], u["outputTokens"])
    return sum(n * Decimal(rate) for n, rate in zip(qty, RATES[call["model"]])) / 1_000_000


def summarize(records, route):
    trials = [row for row in records if row["route"] == route]
    accepted = sum(row["accepted"] for row in trials)
    calls = [call for row in trials for call in row["calls"]]
    if not accepted:
        raise ValueError("No accepted result for " + route)
    totals = {key: sum(call["usage"][key] for call in calls)
              for key in ("inputTokens", "cachedInputTokens", "outputTokens")}
    durations = [Decimal(str(row["wall_s"])) for row in trials]
    return {"attempts": len(trials), "accepted": accepted, "calls": len(calls),
            "tokens": totals, "seconds": sum(durations), "range_s": [min(durations), max(durations)],
            "usd": sum(map(price, calls)), "premium_input": sum(call["usage"]["inputTokens"] for call in calls
                                                             if call["model"] == "gpt-6.2-astra")}


if __name__ == "__main__":
    rows = json.loads((ROOT / "receipts.json").read_text(encoding="utf-8"))
    for route in ("luna", "astra", "reviewed"):
        summary = summarize(rows, route)
        n = summary["accepted"]
        print(route, f"{n}/{summary['attempts']} accepted", summary["tokens"],
              f"{summary['seconds'] / n:.2f} s and USD {summary['usd'] / n:.6f} per accepted result")

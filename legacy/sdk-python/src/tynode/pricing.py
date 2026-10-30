"""Estimated USD price per 1M tokens (input, output). Estimates: adjust as providers change prices."""
from typing import Optional

PRICES = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "o3-mini": (1.10, 4.40),
    "claude-haiku": (1.00, 5.00),
    "claude-sonnet": (3.00, 15.00),
    "claude-opus": (15.00, 75.00),
}


def estimate_cost(model: Optional[str], tokens_in: int, tokens_out: int) -> float:
    if not model:
        return 0.0
    m = model.lower()
    for key in sorted(PRICES, key=len, reverse=True):
        if key in m:
            pin, pout = PRICES[key]
            return (tokens_in * pin + tokens_out * pout) / 1_000_000
    return 0.0
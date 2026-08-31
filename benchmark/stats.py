"""
Percentile and basic statistics helpers — no numpy dependency.
"""
from __future__ import annotations
from typing import List, Optional


def percentile(data: List[float], p: float) -> float:
    """
    Nearest-rank percentile. Returns 0.0 for empty lists.
    p is 0-100.
    """
    if not data:
        return 0.0
    sorted_data = sorted(data)
    n = len(sorted_data)
    # Nearest-rank formula: ceil(p/100 * n), 1-indexed
    rank = max(1, int(round(p / 100.0 * n + 0.5)))
    rank = min(rank, n)
    return sorted_data[rank - 1]


def p50(data: List[float]) -> float:
    return percentile(data, 50)


def p95(data: List[float]) -> float:
    return percentile(data, 95)


def p99(data: List[float]) -> float:
    return percentile(data, 99)


def mean(data: List[float]) -> float:
    if not data:
        return 0.0
    return sum(data) / len(data)


def minimum(data: List[float]) -> Optional[float]:
    return min(data) if data else None


def maximum(data: List[float]) -> Optional[float]:
    return max(data) if data else None


def summary_stats(data: List[float]) -> dict:
    return {
        "count": len(data),
        "min": minimum(data),
        "max": maximum(data),
        "mean": round(mean(data), 2),
        "p50": round(p50(data), 2),
        "p95": round(p95(data), 2),
        "p99": round(p99(data), 2),
    }

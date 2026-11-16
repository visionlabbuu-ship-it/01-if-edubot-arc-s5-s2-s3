"""Adaptive difficulty — rule-based (no ML / no LLM)."""

from __future__ import annotations


def next_level(
    level: int,
    *,
    passed: bool,
    consecutive_pass: int,
    consecutive_fail: int,
    min_level: int = 1,
    max_level: int = 3,
) -> int:
    """Return difficulty level after a graded attempt."""
    level = max(min_level, min(max_level, int(level)))
    if passed:
        if consecutive_pass >= 2:
            return min(max_level, level + 1)
        return level
    if consecutive_fail >= 3:
        return max(min_level, level - 1)
    return level


def should_auto_demo(*, consecutive_fail: int) -> bool:
    """After 2 fails in a row, teacher should offer / run demo."""
    return int(consecutive_fail) >= 2

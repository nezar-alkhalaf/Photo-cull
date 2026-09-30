"""Burst grouping by capture-time gap (plan section 6.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence, TypeVar

from .split import distribute

T = TypeVar("T")


def group_by_gap(items: Sequence[T], times: Sequence[datetime], gap_seconds: float,
                 max_group_size: int) -> list[list[T]]:
    """Split time-sorted items into bursts.

    A new group starts when the gap to the PREVIOUS frame exceeds gap_seconds.
    Groups longer than max_group_size are cut into consecutive chunks of
    near-equal size (13 frames with max 12 -> 7 + 6, not 12 + 1).
    """
    if len(items) != len(times):
        raise ValueError("items and times must have the same length")
    if max_group_size < 1:
        raise ValueError("max_group_size must be at least 1")
    bursts: list[list[T]] = []
    prev: datetime | None = None
    for item, t in zip(items, times):
        if prev is None or (t - prev).total_seconds() > gap_seconds:
            bursts.append([])
        bursts[-1].append(item)
        prev = t

    groups: list[list[T]] = []
    for burst in bursts:
        i = 0
        for size in distribute(len(burst), max_group_size):
            groups.append(burst[i:i + size])
            i += size
    return groups

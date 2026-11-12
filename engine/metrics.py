"""FlowAugur engine snapshot: selected definitions from ``metrics.py``.

Copied verbatim from MonteJiraSim, the FlowAugur app's repository, at commit
ae17e093878c68057ee8f5f3e7e54d8fc325305a.

Do not edit. ``engine/snapshot.py`` regenerates this file from a MonteJiraSim
checkout and checks it against that commit; ``engine/SNAPSHOT.json`` pins a
hash of every definition below.

Definitions: GRANULARITIES, DEFAULT_SPRINT_DAYS, MIN_CADENCE_SPAN, MIN_CADENCE_ITEMS, _FLOW_ACTIVE_RATIO, _BURST_GAP_DAYS, bucket_daily_series, CadenceReport, detect_cadence, remove_outliers_iqr.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


# Bucket granularities for throughput aggregation (FORE-82). "daily" is the
# historical default; "weekly"/"sprint" sum daily counts into fixed-width bins.
GRANULARITIES = ("daily", "weekly", "sprint")


DEFAULT_SPRINT_DAYS = 14


# Cadence auto-detection (FORE-78). Conservative: only suggest a coarser default
# bucket when there's a clear, regular batch rhythm; otherwise stay on daily.
MIN_CADENCE_SPAN = 14  # need at least this many days of history to judge


MIN_CADENCE_ITEMS = 8  # ...and at least this many completed items


_FLOW_ACTIVE_RATIO = 0.4  # completing on >= this fraction of days reads as flow


_BURST_GAP_DAYS = 2  # more than this many idle days separates one batch from the next


def bucket_daily_series(
    daily: pd.Series,
    granularity: str = "daily",
    *,
    sprint_days: int = DEFAULT_SPRINT_DAYS,
) -> pd.Series:
    """Sum an existing zero-filled daily throughput series into wider buckets.

    Splits out the binning step of :func:`bucketed_throughput` so callers that
    already hold a daily series (e.g. the Forecast tab) can re-bucket it without
    re-fetching. Daily granularity returns the series unchanged.
    """
    if granularity not in GRANULARITIES:
        raise ValueError(
            f"granularity must be one of {GRANULARITIES}, got {granularity!r}"
        )
    if granularity == "daily" or daily.empty:
        return daily

    width = 7 if granularity == "weekly" else sprint_days
    # Anchor bins at the window start: bucket = floor(days_since_start / width).
    # Done explicitly rather than via resample(origin=...), which silently
    # ignores the anchor for day-frequencies.
    idx = pd.DatetimeIndex(daily.index)
    bin_starts = idx[0] + pd.to_timedelta((idx - idx[0]).days // width * width, unit="D")
    out = daily.groupby(bin_starts).sum()
    out.name = "throughput"
    return out


@dataclass(frozen=True)
class CadenceReport:
    """Suggested default cadence for the throughput pickers (FORE-78).

    ``cadence`` is one of GRANULARITIES; ``hint`` is a short Markdown note shown by
    the UI when a batch rhythm was detected, or None when the data looks like
    continuous flow / there isn't enough signal (defaulting quietly to daily)."""

    cadence: str
    hint: str | None = None


def detect_cadence(daily: pd.Series) -> CadenceReport:
    """Infer whether a team delivers continuously (flow) or in periodic batches
    from the daily completion pattern, to default the cadence picker sensibly.

    The signal is the gap pattern: a flow team completes on most days; a
    batch/sprint team completes in clusters separated by regular idle stretches.
    Conservative by design — ambiguous or thin data stays on daily, since the
    suggestion only sets a *default* the user can override.

    Expects a zero-filled, contiguous daily series (as from daily_throughput with
    weekdays_only=False) so index positions are consecutive calendar days.
    """
    counts = daily.to_numpy(dtype=float) if len(daily) else np.empty(0)
    span = counts.size
    if span < MIN_CADENCE_SPAN or counts.sum() < MIN_CADENCE_ITEMS:
        return CadenceReport("daily")

    active = counts > 0
    if active.sum() / span >= _FLOW_ACTIVE_RATIO:
        return CadenceReport("daily")  # completes most days → continuous flow

    # Batchy: locate burst starts (an active day preceded by a real idle gap) and
    # measure the typical period between bursts.
    active_idx = np.flatnonzero(active)
    burst_starts = [int(active_idx[0])]
    for prev, cur in zip(active_idx, active_idx[1:]):
        if cur - prev > _BURST_GAP_DAYS:
            burst_starts.append(int(cur))
    if len(burst_starts) < 2:
        return CadenceReport("daily")  # one lump, no rhythm to call

    period = float(np.median(np.diff(burst_starts)))
    if period >= 11:
        return CadenceReport(
            "sprint",
            f"Looks like a **sprint team** — completions arrive in batches about "
            f"every {round(period)} days, so daily is too fine. Defaulting to "
            "sprint buckets (override above if needed).",
        )
    if period >= 4:
        return CadenceReport(
            "weekly",
            "Looks like a **weekly-batch team** — completions cluster about weekly. "
            "Defaulting to weekly buckets (override above if needed).",
        )
    return CadenceReport("daily")  # frequent small bursts read as flow


def remove_outliers_iqr(s: pd.Series, k: float = 1.5) -> pd.Series:
    if len(s) < 4:
        return s
    q1, q3 = s.quantile([0.25, 0.75])
    iqr = q3 - q1
    if iqr == 0:
        return s
    lo, hi = q1 - k * iqr, q3 + k * iqr
    return s[(s >= lo) & (s <= hi)]

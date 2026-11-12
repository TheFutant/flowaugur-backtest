"""FlowAugur engine snapshot: selected definitions from ``simulate.py``.

Copied verbatim from MonteJiraSim, the FlowAugur app's repository, at commit
ae17e093878c68057ee8f5f3e7e54d8fc325305a.

Do not edit. ``engine/snapshot.py`` regenerates this file from a MonteJiraSim
checkout and checks it against that commit; ``engine/SNAPSHOT.json`` pins a
hash of every definition below.

Definitions: DEFAULT_LEVELS, forecast_items_by_date, confidence_amounts.
"""
from __future__ import annotations

import numpy as np


DEFAULT_LEVELS = (0.5, 0.7, 0.85, 0.95)


def forecast_items_by_date(
    throughput: np.ndarray,
    days_remaining: int,
    n_trials: int = 10000,
    rng: np.random.Generator | None = None,
    *,
    bucket_days: int = 1,
) -> np.ndarray:
    """Sample throughput with replacement across the horizon; return totals per trial.

    `throughput` holds counts per bucket and `bucket_days` is the bucket width in
    days, so the horizon spans ``days_remaining / bucket_days`` buckets. A
    trailing partial bucket is scaled by its fraction of a full bucket. With the
    default ``bucket_days=1`` (daily) this draws one sample per remaining day,
    matching the original behaviour exactly.
    """
    rng = rng or np.random.default_rng()
    n_full, rem = divmod(int(days_remaining), bucket_days)
    width = n_full + (1 if rem else 0)
    if width <= 0:
        return np.zeros(n_trials)
    samples = rng.choice(throughput, size=(n_trials, width), replace=True)
    if rem:
        samples = samples.astype(float)
        samples[:, -1] *= rem / bucket_days
    return samples.sum(axis=1)


def confidence_amounts(
    arr: np.ndarray,
    levels: tuple[float, ...] = DEFAULT_LEVELS,
    *,
    lower_is_safer: bool = False,
) -> dict[float, float]:
    """Map confidence level → amount.

    lower_is_safer=False (durations): N% confident done by D days → D = Nth percentile
    lower_is_safer=True  (counts):    N% confident at least M items → M = (100-N)th percentile
    """
    out: dict[float, float] = {}
    for lvl in levels:
        pct = (1 - lvl) * 100 if lower_is_safer else lvl * 100
        out[lvl] = float(np.percentile(arr, pct))
    return out

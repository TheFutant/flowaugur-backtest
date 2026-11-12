"""Replay FlowAugur forecasts on one project's history and score each one.

For every cutoff date the forecast is rebuilt from the data before that date
only, with FlowAugur's own engine and nothing re-implemented::

    daily history -> metrics.remove_outliers_iqr (optional)
                  -> metrics.bucket_daily_series
                  -> simulate.forecast_items_by_date
                  -> simulate.confidence_amounts(lower_is_safer=True)

and then compared with what the project actually completed. The engine is the
verbatim snapshot under ``engine/`` (see ``engine/snapshot.py``), and numpy,
pandas and pyarrow are pinned to the versions the app itself locks.

Conventions (the plan's "Decisions locked" table):

* A cutoff ``T`` is a UTC day. History is the ``W`` days ``T-W .. T-1``,
  zero-filled; the horizon is the ``H`` days ``T .. T+H-1``. Nothing on or
  after ``T`` reaches the forecast.
* Calendar days for every bucket size.
* Cutoffs run every 2 weeks inside the project's qualifying 2-year window:
  the 104 consecutive Monday-start weeks with the highest share of weeks at
  10 or more done items (at least 90% to qualify; ties go to the most recent
  window). Both the longest history (26 weeks) and the longest horizon
  (12 weeks) must fit inside the window, so every variant is scored on the
  same 34 cutoffs.
* A band holds when the actual count is at least its value, which is the
  app's own scoring rule (``reconcile.reconcile_bydate`` in MonteJiraSim).

Usage::

    uv run replay.py --instance Apache --project SPARK
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from engine.metrics import bucket_daily_series, remove_outliers_iqr
from engine.simulate import DEFAULT_LEVELS, confidence_amounts, forecast_items_by_date

HERE = Path(__file__).resolve().parent

# Plan name -> (metrics granularity, bucket width in days).
BUCKETS = {"day": ("daily", 1), "week": ("weekly", 7), "2 weeks": ("sprint", 14)}
HISTORY_DAYS = (84, 182)  # 12 and 26 weeks
HORIZON_DAYS = (14, 28, 56, 84)  # 2, 4, 8 and 12 weeks
N_TRIALS = 10_000
SEED = 20261112

WINDOW_WEEKS = 104
MIN_ITEMS = 10  # done items that make a week "steady"
MIN_SHARE = 0.9  # share of steady weeks a window needs
CUTOFF_STEP_DAYS = 14

DAY = pd.Timedelta(days=1)


def load_daily(instance: str, project: str, path: Path | None = None) -> pd.Series:
    """Done items per UTC day for one project, zero-filled first to last done day."""
    table = pd.read_parquet(path or HERE / "data" / "daily_throughput.parquet")
    rows = table[(table["instance"] == instance) & (table["project"] == project)]
    if rows.empty:
        raise ValueError(f"no done items for {instance} {project}")
    daily = rows.groupby(pd.to_datetime(rows["day"]))["done"].sum()
    daily = daily.reindex(pd.date_range(daily.index.min(), daily.index.max(), freq="D"), fill_value=0)
    return daily.rename("throughput")


def weekly(daily: pd.Series) -> pd.Series:
    """Monday-start weekly totals over the complete weeks the project was observed.

    The last observed week is dropped: the download stopped part-way through it.
    """
    idx = pd.DatetimeIndex(daily.index)
    week_start = idx - pd.to_timedelta(idx.dayofweek, unit="D")
    return daily.groupby(week_start).sum().iloc[:-1]


def qualifying_window(daily: pd.Series) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First and last day of the project's qualifying 2-year window."""
    weeks = weekly(daily)
    share = (weeks >= MIN_ITEMS).astype(float).rolling(WINDOW_WEEKS).mean()
    best = share.max()
    if not best >= MIN_SHARE:  # also catches NaN: fewer than 104 weeks
        raise ValueError(f"no {WINDOW_WEEKS}-week window with {MIN_SHARE:.0%} of weeks at {MIN_ITEMS}+ done")
    last_week = pd.Timestamp(share[share == best].index[-1])  # most recent of the best
    return last_week - pd.Timedelta(weeks=WINDOW_WEEKS - 1), last_week + 6 * DAY


def cutoffs(window: tuple[pd.Timestamp, pd.Timestamp]) -> list[pd.Timestamp]:
    """Cutoff days: every 2 weeks, with room for 26 weeks of history before the
    first and a 12-week horizon after the last, all inside the window."""
    start, end = window
    first = start + max(HISTORY_DAYS) * DAY
    last = end + DAY - max(HORIZON_DAYS) * DAY
    return list(pd.date_range(first, last, freq=f"{CUTOFF_STEP_DAYS}D"))


def history(daily: pd.Series, cutoff: pd.Timestamp, history_days: int) -> pd.Series:
    """The ``history_days`` days before the cutoff, zero-filled. Excludes the cutoff day."""
    days = pd.date_range(cutoff - history_days * DAY, cutoff - DAY, freq="D")
    return daily.reindex(days, fill_value=0).rename("throughput")


def actual(daily: pd.Series, cutoff: pd.Timestamp, horizon_days: int) -> int:
    """Items really done in the horizon: the cutoff day and the days after it."""
    days = pd.date_range(cutoff, cutoff + (horizon_days - 1) * DAY, freq="D")
    return int(daily.reindex(days, fill_value=0).sum())


def prepare(hist: pd.Series, bucket: str, drop_outliers: bool) -> tuple[np.ndarray, int]:
    """The app's throughput prep (app.py + tabs/forecast.py): optional IQR drop on
    the *daily* series, then bucketing. Returns the samples and the days dropped."""
    granularity, _ = BUCKETS[bucket]
    clean = remove_outliers_iqr(hist) if drop_outliers else hist
    samples = clean if granularity == "daily" else bucket_daily_series(clean, granularity)
    return samples.to_numpy(dtype=float), len(hist) - len(clean)


def rng_for(cutoff: pd.Timestamp, bucket_days: int, history_days: int, horizon_days: int, drop: bool) -> np.random.Generator:
    """A reproducible, independent stream per forecast."""
    return np.random.default_rng([SEED, cutoff.toordinal(), bucket_days, history_days, horizon_days, int(drop)])


def crps(samples: np.ndarray, observed: float) -> float:
    """Continuous ranked probability score of a sample forecast (lower is better):
    ``E|X - y| - 0.5 E|X - X'|``. For a point forecast it is the absolute error."""
    x = np.sort(np.asarray(samples, dtype=float))
    n = x.size
    spread = 2.0 * np.sum((2 * np.arange(1, n + 1) - n - 1) * x) / (n * n)
    return float(np.mean(np.abs(x - observed)) - 0.5 * spread)


def score(totals: np.ndarray, done: int) -> dict:
    """Score one forecast. Every method in this repository is scored here: the
    engine's "at least" amount per confidence level, whether each held, where
    the actual fell among the simulated totals (PIT), and CRPS."""
    amounts = confidence_amounts(totals, lower_is_safer=True)
    out = {
        "actual": done,
        "mean": float(totals.mean()),
        "pit": float((totals <= done).mean()),
        "crps": crps(totals, done),
    }
    for level in DEFAULT_LEVELS:
        name = f"p{round(level * 100)}"
        out[name] = amounts[level]
        out[f"hit_{name}"] = bool(done >= amounts[level])
    return out


SCORE_COLUMNS = ["pit", "crps", "p50", "hit_p50", "p70", "hit_p70", "p85", "hit_p85", "p95", "hit_p95"]
COLUMNS = [
    "instance", "project", "cutoff", "bucket", "history_days", "horizon_days", "outlier_drop",
    "n_samples", "days_dropped", "actual", "mean", "naive", *SCORE_COLUMNS,
]  # fmt: skip


def replay(daily: pd.Series, instance: str = "", project: str = "") -> pd.DataFrame:
    """One scored forecast per cutoff x bucket x history x horizon x outlier setting."""
    rows = []
    for cutoff in cutoffs(qualifying_window(daily)):
        for history_days in HISTORY_DAYS:
            hist = history(daily, cutoff, history_days)
            naive_rate = float(hist.mean())  # raw average per day, whatever the outlier setting
            for bucket, (_, bucket_days) in BUCKETS.items():
                for drop in (True, False):
                    samples, days_dropped = prepare(hist, bucket, drop)
                    for horizon_days in HORIZON_DAYS:
                        totals = forecast_items_by_date(
                            samples,
                            horizon_days,
                            n_trials=N_TRIALS,
                            rng=rng_for(cutoff, bucket_days, history_days, horizon_days, drop),
                            bucket_days=bucket_days,
                        )
                        rows.append(
                            {
                                "instance": instance,
                                "project": project,
                                "cutoff": cutoff.date(),
                                "bucket": bucket,
                                "history_days": history_days,
                                "horizon_days": horizon_days,
                                "outlier_drop": drop,
                                "n_samples": len(samples),
                                "days_dropped": days_dropped,
                                "naive": naive_rate * horizon_days,
                                **score(totals, actual(daily, cutoff, horizon_days)),
                            }
                        )
    return pd.DataFrame(rows)[COLUMNS]


def coverage(results: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Share of forecasts whose band held, per confidence level."""
    hits = [f"hit_p{round(level * 100)}" for level in DEFAULT_LEVELS]
    out = results.groupby(by)[hits].mean()
    out.columns = [h.removeprefix("hit_").upper() for h in hits]
    out.insert(0, "forecasts", results.groupby(by).size())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--instance", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args(argv)

    daily = load_daily(args.instance, args.project)
    start, end = qualifying_window(daily)
    results = replay(daily, args.instance, args.project)
    out = HERE / "data" / f"replay_{args.instance}_{args.project}.parquet".lower()
    results.to_parquet(out, index=False, compression="zstd")

    n_cutoffs = results["cutoff"].nunique()
    print(f"{args.instance} {args.project}: window {start.date()} to {end.date()}, {n_cutoffs} cutoffs "
          f"({results['cutoff'].min()} to {results['cutoff'].max()}), {len(results):,} scored forecasts -> {out.name}")
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", "{:.2f}".format)
    print("\nShare of forecasts whose band held (a calibrated P85 holds 85% of the time):")
    print(coverage(results, ["outlier_drop"]).to_string())
    print()
    print(coverage(results, ["outlier_drop", "bucket"]).to_string())
    print()
    print(coverage(results, ["outlier_drop", "horizon_days"]).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())

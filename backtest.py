"""Run the full backtest: every frozen project x cutoff x variant in one table.

Nothing is simulated until each project's qualifying window and first and last
cutoff have been recomputed and compared with ``data/frozen_windows.csv``, the
table fixed in the plan before any results existed. Any difference stops the
run.

Writes:

``data/results.parquet``
    One scored forecast per project x cutoff x bucket x history x horizon x
    outlier setting (columns as in ``replay.py``).

``data/project_summary.csv``
    Per project: its window, the cadence FlowAugur's own detection assigns to
    its history at each cutoff, and how often each band held at the app's
    default settings (day buckets, 26 weeks of history), as shipped and raw.

Usage::

    uv run backtest.py
"""
from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

import pandas as pd

import replay
from engine.metrics import detect_cadence
from engine.simulate import DEFAULT_LEVELS

HERE = Path(__file__).resolve().parent
FROZEN = HERE / "data" / "frozen_windows.csv"

# The app's defaults for these projects: its cadence detection picks daily
# buckets for every one of them, and its default lookback is 180 days.
APP_BUCKET = "day"
APP_HISTORY_DAYS = 182
HIT_COLUMNS = [f"hit_p{round(level * 100)}" for level in DEFAULT_LEVELS]


def frozen_windows(path: Path = FROZEN) -> pd.DataFrame:
    dates = ["window_start", "window_end", "first_cutoff", "last_cutoff"]
    return pd.read_csv(path, parse_dates=dates)


def check_windows(
    frozen: pd.DataFrame, load: Callable[[str, str], pd.Series] = replay.load_daily
) -> list[str]:
    """Differences between the frozen table and what the code computes now."""
    problems = []
    for row in frozen.itertuples():
        name = f"{row.instance} {row.project}"
        try:
            window = replay.qualifying_window(load(row.instance, row.project))
        except ValueError as error:
            problems.append(f"{name}: {error}")
            continue
        cuts = replay.cutoffs(window)
        computed = {
            "window_start": window[0],
            "window_end": window[1],
            "first_cutoff": cuts[0],
            "last_cutoff": cuts[-1],
        }
        for field, value in computed.items():
            if value != getattr(row, field):
                problems.append(f"{name}: {field} is {value.date()}, frozen as {getattr(row, field).date()}")
        if len(cuts) != 34:
            problems.append(f"{name}: {len(cuts)} cutoffs, expected 34")
    return problems


def app_default(results: pd.DataFrame) -> pd.DataFrame:
    """The forecasts the app would make by default: day buckets, 26 weeks of history."""
    return results[(results["bucket"] == APP_BUCKET) & (results["history_days"] == APP_HISTORY_DAYS)]


def outer_tenths(pit: pd.Series) -> tuple[float, float]:
    """Share of forecasts whose actual fell in the bottom and the top tenth of
    the forecast distribution: the two end bars of the PIT histogram."""
    return float((pit < 0.1).mean()), float((pit >= 0.9).mean())


def hit_rates(results: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Share of forecasts whose band held, per project and ``by``, one column per level."""
    rates = results.groupby(["instance", "project", *by])[HIT_COLUMNS].mean()
    return rates.rename(columns=lambda c: c.removeprefix("hit_"))


def project_summary(frozen: pd.DataFrame, results: pd.DataFrame, cadences: dict) -> pd.DataFrame:
    rates = hit_rates(app_default(results), ["outlier_drop"]).unstack("outlier_drop")
    rates.columns = [f"{level}_{'shipped' if drop else 'raw'}" for level, drop in rates.columns]
    out = frozen.set_index(["instance", "project"])[["window_start", "window_end"]].copy()
    out["cutoffs"] = results.groupby(["instance", "project"])["cutoff"].nunique()
    out["cadence"] = pd.Series(cadences)
    return out.join(rates.round(4)).reset_index()


def main() -> int:
    frozen = frozen_windows()
    problems = check_windows(frozen)
    if problems:
        print(f"STOPPED: {len(problems)} difference(s) from {FROZEN.name}; nothing was run.")
        for problem in problems:
            print("  " + problem)
        return 1
    print(f"windows and cutoffs match {FROZEN.name} for all {len(frozen)} projects")

    started = time.time()
    frames, cadences = [], {}
    for row in frozen.itertuples():
        daily = replay.load_daily(row.instance, row.project)
        frames.append(replay.replay(daily, row.instance, row.project))
        cuts = replay.cutoffs(replay.qualifying_window(daily))
        seen = pd.Series([detect_cadence(replay.history(daily, c, APP_HISTORY_DAYS)).cadence for c in cuts])
        counts = seen.value_counts()
        cadences[(row.instance, row.project)] = (
            counts.index[0] if len(counts) == 1 else "; ".join(f"{k} {v}" for k, v in counts.items())
        )
        print(f"  {row.instance:<12} {row.project:<13} {len(frames[-1]):>5} forecasts  ({time.time() - started:4.0f}s)", flush=True)
    results = pd.concat(frames, ignore_index=True)
    results.to_parquet(HERE / "data" / "results.parquet", index=False, compression="zstd")
    summary = project_summary(frozen, results, cadences)
    summary.to_csv(HERE / "data" / "project_summary.csv", index=False)

    print(f"{len(results):,} scored forecasts, {results.groupby(['instance', 'project']).ngroups} projects -> data/results.parquet")
    print(f"cadence by FlowAugur's own detection: {summary['cadence'].value_counts().to_dict()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

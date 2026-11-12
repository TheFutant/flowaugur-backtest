"""EXPERIMENT 3b: block resampling. This is not FlowAugur's engine.

FlowAugur resamples single days independently. The main replay found its
forecast range too narrow. This experiment asks whether resampling multi-week runs of
real days instead fixes that. The sampler below was written for this backtest;
it is not in ``engine/`` (which stays a verbatim snapshot of what the app
ships) and it is not in the app.

The variants, settings and pass test were declared before any 3b results
existed (see METHODS.md):

* **Variants.** A moving-block bootstrap on the daily history, with 14-day and
  28-day blocks: start points drawn uniformly from all full blocks in the
  history window, blocks joined end to end, the last one cut to the horizon.
* **Fixed settings.** Raw history (no outlier drop), calendar days, 26 weeks of
  history, horizons of 2, 4, 8 and 12 weeks, 10,000 trials, the frozen windows
  and 34 cutoffs per project.
* **Baselines.** The main replay at the app's defaults, read from
  ``data/results.parquet``: day buckets raw, and day buckets as shipped.
* **Pass test, all three:** P85 holds 80-90% of the time; the share of actuals
  in the outer tenths of the forecast is at most 30%; mean CRPS is at most 64.

**Pace-shift diagnostic** (descriptive only; it is not part of the pass test).
For every forecast, the *pace ratio* is the horizon's daily rate divided by the
history's. A *shift* is a ratio below 0.8 or above 1.25.

* *Observed shift share*: the share of forecasts whose actual ratio is a shift.
* *Expected shift share*: the same share across each forecast's own 10,000
  simulated totals (total / horizon / history rate), averaged over forecasts.
  It is how often such a swing would happen if the forecast's model of
  variation were right.
* *Excess shift*: observed minus expected, by horizon, for raw day buckets and
  both block variants.

This is the revised diagnostic. Its first version (the share of
outer-tenth misses that came with a shift) was dropped as circular: a miss is,
nearly by definition, a result far from the history rate.

Scoring is ``replay.score``, the same function the main replay uses.

Writes ``data/experiment_3b.parquet`` (one row per project x cutoff x horizon x
method, baselines included), ``data/experiment_3b_summary.csv`` (each method
against the pass test), ``data/experiment_3b_pace.csv`` (the diagnostic),
``data/experiment_3b_pace_distribution.csv`` (the pace ratio's quantiles) and
``data/experiment_3b_pace_by_project.csv`` (the diagnostic per project, for
raw day buckets, beside how often each project's 85% forecast held as shipped;
this per-project view was added after the results were known, for the "where
it breaks" chart, and is descriptive only).

Usage::

    uv run experiment_3b.py
"""
from __future__ import annotations

import sys
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

import backtest
import replay
from engine.simulate import forecast_items_by_date

HERE = Path(__file__).resolve().parent

BLOCK_DAYS = (14, 28)
HISTORY_DAYS = 182
# method key -> label. The first two are the main replay's forecasts; the last two are the experiment.
METHODS = {
    "shipped": "Day buckets, as shipped (main replay)",
    "raw": "Day buckets, raw (main replay)",
    "block14": "14-day blocks, raw (experiment)",
    "block28": "28-day blocks, raw (experiment)",
}
EXPERIMENTAL = ("block14", "block28")
# The diagnostic compares methods that resample the same raw history.
DIAGNOSTIC_METHODS = ("raw", "block14", "block28")
KEYS = ["instance", "project", "cutoff", "horizon_days"]

# The pass test, as declared.
P85_RANGE = (0.80, 0.90)
OUTER_TENTHS_MAX = 0.30
CRPS_MAX = 64.0
# The pace-shift diagnostic's thresholds, as declared.
PACE_LOW, PACE_HIGH = 0.8, 1.25


def block_totals(
    history: np.ndarray, horizon_days: int, block_days: int, n_trials: int, rng: np.random.Generator
) -> np.ndarray:
    """Moving-block bootstrap totals: one simulated horizon total per trial.

    Each trial draws block start days uniformly from every position where a
    full ``block_days`` block fits inside ``history``, lays the blocks end to
    end, cuts the last one so the path is exactly ``horizon_days`` long, and
    sums it. Days inside a block keep their real order and neighbours.
    """
    history = np.asarray(history, dtype=float)
    n_starts = history.size - block_days + 1
    if n_starts < 1:
        raise ValueError(f"history of {history.size} days is shorter than one {block_days}-day block")
    n_blocks = -(-horizon_days // block_days)
    lengths = np.full(n_blocks, block_days)
    lengths[-1] = horizon_days - block_days * (n_blocks - 1)
    starts = rng.integers(0, n_starts, size=(n_trials, n_blocks))
    running = np.concatenate([[0.0], np.cumsum(history)])
    return (running[starts + lengths] - running[starts]).sum(axis=1)


def rng_for(instance: str, project: str, cutoff: pd.Timestamp, block_days: int, horizon_days: int) -> np.random.Generator:
    """A reproducible, independent stream per forecast."""
    name = zlib.crc32(f"{instance}/{project}".encode())
    return np.random.default_rng([replay.SEED, 3, name, cutoff.toordinal(), block_days, horizon_days])


def is_shift(ratio):
    """A pace ratio outside the declared band."""
    return (ratio < PACE_LOW) | (ratio > PACE_HIGH)


def pace_ratio(hist: pd.Series, done: float, horizon_days: int) -> float:
    """The horizon's daily rate divided by the history's daily rate."""
    history_rate = float(hist.sum()) / len(hist)
    return (done / horizon_days) / history_rate if history_rate > 0 else float("nan")


def expected_shift(hist: pd.Series, totals: np.ndarray, horizon_days: int) -> float:
    """Share of a forecast's own simulated totals whose pace ratio is a shift."""
    history_rate = float(hist.sum()) / len(hist)
    if history_rate <= 0:
        return float("nan")
    return float(is_shift(totals / horizon_days / history_rate).mean())


def run(frozen: pd.DataFrame) -> pd.DataFrame:
    """Every forecast that resamples raw history, scored, with its pace ratio and
    its expected shift share: the two block variants, and the main replay's raw
    day-bucket forecast regenerated from the same engine call and random stream."""
    rows = []
    for row in frozen.itertuples():
        daily = replay.load_daily(row.instance, row.project)
        for cutoff in replay.cutoffs(replay.qualifying_window(daily)):
            hist = replay.history(daily, cutoff, HISTORY_DAYS)
            samples, _ = replay.prepare(hist, backtest.APP_BUCKET, drop_outliers=False)
            for horizon_days in replay.HORIZON_DAYS:
                done = replay.actual(daily, cutoff, horizon_days)
                simulated = {
                    "raw": forecast_items_by_date(
                        samples,
                        horizon_days,
                        n_trials=replay.N_TRIALS,
                        rng=replay.rng_for(cutoff, 1, HISTORY_DAYS, horizon_days, False),
                        bucket_days=1,
                    )
                }
                for block_days in BLOCK_DAYS:
                    simulated[f"block{block_days}"] = block_totals(
                        hist.to_numpy(),
                        horizon_days,
                        block_days,
                        replay.N_TRIALS,
                        rng_for(row.instance, row.project, cutoff, block_days, horizon_days),
                    )
                for method, totals in simulated.items():
                    rows.append(
                        {
                            "instance": row.instance,
                            "project": row.project,
                            "cutoff": cutoff.date(),
                            "horizon_days": horizon_days,
                            "method": method,
                            "pace_ratio": pace_ratio(hist, done, horizon_days),
                            "expected_shift": expected_shift(hist, totals, horizon_days),
                            **replay.score(totals, done),
                        }
                    )
    return pd.DataFrame(rows)


def session_3(results: pd.DataFrame) -> pd.DataFrame:
    """The main replay's forecasts at the app's defaults, in the experiment's layout."""
    subset = backtest.app_default(results).copy()
    subset["method"] = np.where(subset["outlier_drop"], "shipped", "raw")
    return subset[[*KEYS, "method", "actual", "mean", *replay.SCORE_COLUMNS]]


def assemble(simulated: pd.DataFrame, results: pd.DataFrame) -> pd.DataFrame:
    """One table for all four methods. The raw rows regenerated here must be
    the main replay's rows exactly; the as-shipped rows are taken from it."""
    published = session_3(results)
    scored = ["actual", "mean", *replay.SCORE_COLUMNS]
    ours = simulated[simulated["method"] == "raw"].set_index(KEYS)[scored]
    theirs = published[published["method"] == "raw"].set_index(KEYS)[scored]
    if not ours.sort_index().equals(theirs.sort_index()):
        raise RuntimeError("the regenerated raw day-bucket forecasts differ from data/results.parquet")
    pace = simulated.drop_duplicates(KEYS)[[*KEYS, "pace_ratio"]]
    shipped = published[published["method"] == "shipped"].merge(pace, on=KEYS, validate="one_to_one")
    table = pd.concat([shipped, simulated], ignore_index=True)
    table["experimental"] = table["method"].isin(EXPERIMENTAL)
    order = {method: i for i, method in enumerate(METHODS)}
    return table.sort_values(by="method", key=lambda m: m.map(order), kind="stable").reset_index(drop=True)


def pass_test(table: pd.DataFrame) -> pd.DataFrame:
    """Each method against the declared pass test."""
    rows = []
    for method, label in METHODS.items():
        t = table[table["method"] == method]
        bottom, top = backtest.outer_tenths(t["pit"])
        p85, crps = float(t["hit_p85"].mean()), float(t["crps"].mean())
        checks = {
            "p85_in_range": P85_RANGE[0] <= p85 <= P85_RANGE[1],
            "outer_tenths_ok": bottom + top <= OUTER_TENTHS_MAX,
            "crps_ok": crps <= CRPS_MAX,
        }
        rows.append(
            {
                "method": method,
                "label": label,
                "forecasts": len(t),
                "p50": float(t["hit_p50"].mean()),
                "p70": float(t["hit_p70"].mean()),
                "p85": p85,
                "p95": float(t["hit_p95"].mean()),
                "bottom_tenth": bottom,
                "top_tenth": top,
                "outer_tenths": bottom + top,
                "mean_crps": crps,
                **checks,
                "passes": all(checks.values()),
            }
        )
    return pd.DataFrame(rows)


def pace_shift(table: pd.DataFrame) -> pd.DataFrame:
    """The diagnostic: observed, expected and excess shift share by horizon and method."""
    rows = []
    for horizon_days, at_horizon in table.groupby("horizon_days"):
        for method in DIAGNOSTIC_METHODS:
            t = at_horizon[at_horizon["method"] == method]
            observed = float(is_shift(t["pace_ratio"]).mean())
            expected = float(t["expected_shift"].mean())
            rows.append(
                {
                    "horizon_days": horizon_days,
                    "method": method,
                    "label": METHODS[method],
                    "forecasts": len(t),
                    "observed_shift": observed,
                    "expected_shift": expected,
                    "excess_shift": observed - expected,
                }
            )
    return pd.DataFrame(rows)


def pace_by_project(table: pd.DataFrame) -> pd.DataFrame:
    """Per project and horizon: observed, expected and excess shift share for raw
    day buckets, with how often the project's 85% forecast held as shipped."""
    raw = table[table["method"] == "raw"]
    raw = raw.assign(shift=is_shift(raw["pace_ratio"]))
    out = (
        raw.groupby(["instance", "project", "horizon_days"])
        .agg(forecasts=("shift", "size"), observed_shift=("shift", "mean"), expected_shift=("expected_shift", "mean"))
        .reset_index()
    )
    out["excess_shift"] = out["observed_shift"] - out["expected_shift"]
    held = table[table["method"] == "shipped"].groupby(["instance", "project"])["hit_p85"].mean().rename("p85_shipped")
    return out.merge(held.reset_index(), on=["instance", "project"], validate="many_to_one")


def pace_distribution(table: pd.DataFrame) -> pd.DataFrame:
    """The pace ratio's quantiles over forecasts, for all horizons and for each."""
    per_forecast = table.drop_duplicates(KEYS)
    groups = [("all", per_forecast["pace_ratio"])]
    groups += [(str(h), g["pace_ratio"]) for h, g in per_forecast.groupby("horizon_days")]
    rows = []
    for name, ratio in groups:
        rows.append(
            {
                "horizon_days": name,
                "forecasts": len(ratio),
                **{f"p{q:02d}": float(ratio.quantile(q / 100)) for q in (5, 15, 25, 50, 75, 85, 95)},
                f"below_{PACE_LOW}": float((ratio < PACE_LOW).mean()),
                f"above_{PACE_HIGH}": float((ratio > PACE_HIGH).mean()),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    frozen = backtest.frozen_windows()
    problems = backtest.check_windows(frozen)
    if problems:
        print(f"STOPPED: {len(problems)} difference(s) from {backtest.FROZEN.name}; nothing was run.")
        for problem in problems:
            print("  " + problem)
        return 1
    print(f"windows and cutoffs match {backtest.FROZEN.name} for all {len(frozen)} projects")

    table = assemble(run(frozen), pd.read_parquet(HERE / "data" / "results.parquet"))
    table.to_parquet(HERE / "data" / "experiment_3b.parquet", index=False, compression="zstd")
    verdict = pass_test(table)
    verdict.round(4).to_csv(HERE / "data" / "experiment_3b_summary.csv", index=False)
    diagnostic = pace_shift(table)
    diagnostic.round(4).to_csv(HERE / "data" / "experiment_3b_pace.csv", index=False)
    pace_distribution(table).round(4).to_csv(HERE / "data" / "experiment_3b_pace_distribution.csv", index=False)
    projects = pace_by_project(table)
    projects.round(4).to_csv(HERE / "data" / "experiment_3b_pace_by_project.csv", index=False)

    print(f"{len(table):,} rows, {len(table) // len(METHODS):,} forecasts per method -> data/experiment_3b.parquet")
    print("the raw day-bucket forecasts regenerated here are identical to the main replay's\n")
    print(f"Pass test: P85 in {P85_RANGE[0]:.0%}-{P85_RANGE[1]:.0%}; outer tenths <= {OUTER_TENTHS_MAX:.0%}; mean CRPS <= {CRPS_MAX:g}")
    for r in verdict.itertuples():
        marks = "  ".join(
            f"{name} {'pass' if ok else 'FAIL'}"
            for name, ok in (("P85", r.p85_in_range), ("tails", r.outer_tenths_ok), ("CRPS", r.crps_ok))
        )
        print(
            f"  {r.label:<37} P50 {r.p50:.1%}  P70 {r.p70:.1%}  P85 {r.p85:.1%}  P95 {r.p95:.1%} | "
            f"outer tenths {r.outer_tenths:.1%} ({r.bottom_tenth:.1%} + {r.top_tenth:.1%}) | CRPS {r.mean_crps:.1f} | "
            f"{marks} -> {'PASSES' if r.passes else 'does not pass'}"
        )
    print(f"\nPace shift: share of forecasts whose horizon ran below {PACE_LOW} or above {PACE_HIGH} times the history's daily rate")
    print("  observed, against what each method's own simulated outcomes expected")
    for r in diagnostic.itertuples():
        print(
            f"  {r.horizon_days // 7:>2} weeks  {r.label:<34} observed {r.observed_shift:.1%}  "
            f"expected {r.expected_shift:.1%}  excess {r.excess_shift:+.1%}"
        )
    at12 = projects[projects["horizon_days"] == 84]
    print(
        f"\nAcross the {len(at12)} projects at 12 weeks, the correlation between excess shift and how often "
        f"the 85% forecast held (as shipped) is {at12['excess_shift'].corr(at12['p85_shipped']):+.2f}; "
        f"observed exceeds expected in {int((at12['excess_shift'] > 0).sum())} of them"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

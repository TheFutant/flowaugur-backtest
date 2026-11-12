"""EXPERIMENT 3b: block resampling. This is not FlowAugur's engine.

FlowAugur resamples single days independently. Session 3 found its forecast
range too narrow. This experiment asks whether resampling multi-week runs of
real days instead fixes that. The sampler below was written for this backtest;
it is not in ``engine/`` (which stays a verbatim snapshot of what the app
ships) and it is not in the app.

The spec was declared in the plan before any 3b results existed:

* **Variants.** A moving-block bootstrap on the daily history, with 14-day and
  28-day blocks: start points drawn uniformly from all full blocks in the
  history window, blocks joined end to end, the last one cut to the horizon.
* **Fixed settings.** Raw history (no outlier drop), calendar days, 26 weeks of
  history, horizons of 2, 4, 8 and 12 weeks, 10,000 trials, the frozen windows
  and 34 cutoffs per project.
* **Baselines.** Session 3 at the app's defaults, read from
  ``data/results.parquet``: day buckets raw, and day buckets as shipped.
* **Pass test, all three:** P85 holds 80-90% of the time; the share of actuals
  in the outer tenths of the forecast is at most 30%; mean CRPS is at most 64.
* **Pace-shift diagnostic, descriptive only.** For every forecast, the
  horizon's daily rate divided by the history's; and the share of outer-tenth
  misses where that ratio is below 0.8 or above 1.25.

Scoring is ``replay.score``, the same function Session 3 used.

Writes ``data/experiment_3b.parquet`` (one row per project x cutoff x horizon x
method, baselines included), ``data/experiment_3b_summary.csv`` (the pass test
and the diagnostic per method) and ``data/experiment_3b_pace.csv`` (the pace
ratio's distribution, overall and by horizon).

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

HERE = Path(__file__).resolve().parent

BLOCK_DAYS = (14, 28)
HISTORY_DAYS = 182
# method key -> label. The first two are Session 3's forecasts; the last two are the experiment.
METHODS = {
    "shipped": "Day buckets, as shipped (Session 3)",
    "raw": "Day buckets, raw (Session 3)",
    "block14": "14-day blocks, raw (experiment)",
    "block28": "28-day blocks, raw (experiment)",
}
EXPERIMENTAL = ("block14", "block28")

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


def pace_ratio(hist: pd.Series, done: int, horizon_days: int) -> float:
    """The horizon's daily rate divided by the history's daily rate."""
    history_rate = float(hist.sum()) / len(hist)
    return (done / horizon_days) / history_rate if history_rate > 0 else float("nan")


def run_blocks(frozen: pd.DataFrame) -> pd.DataFrame:
    """The block-bootstrap forecasts, scored, with the pace ratio of each forecast."""
    rows = []
    for row in frozen.itertuples():
        daily = replay.load_daily(row.instance, row.project)
        for cutoff in replay.cutoffs(replay.qualifying_window(daily)):
            hist = replay.history(daily, cutoff, HISTORY_DAYS)
            for horizon_days in replay.HORIZON_DAYS:
                done = replay.actual(daily, cutoff, horizon_days)
                ratio = pace_ratio(hist, done, horizon_days)
                for block_days in BLOCK_DAYS:
                    totals = block_totals(
                        hist.to_numpy(),
                        horizon_days,
                        block_days,
                        replay.N_TRIALS,
                        rng_for(row.instance, row.project, cutoff, block_days, horizon_days),
                    )
                    rows.append(
                        {
                            "instance": row.instance,
                            "project": row.project,
                            "cutoff": cutoff.date(),
                            "horizon_days": horizon_days,
                            "method": f"block{block_days}",
                            "pace_ratio": ratio,
                            **replay.score(totals, done),
                        }
                    )
    return pd.DataFrame(rows)


def baselines(results: pd.DataFrame, pace: pd.DataFrame) -> pd.DataFrame:
    """Session 3's forecasts at the app's defaults, in the experiment's layout."""
    keys = ["instance", "project", "cutoff", "horizon_days"]
    subset = backtest.app_default(results).copy()
    subset["method"] = np.where(subset["outlier_drop"], "shipped", "raw")
    columns = [*keys, "method", "actual", "mean", *replay.SCORE_COLUMNS]
    return subset[columns].merge(pace, on=keys, how="left", validate="many_to_one")


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


def pace_shift(table: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """The ratio's distribution over forecasts, and per method how many
    outer-tenth misses (and how many of the rest) had a pace shift."""
    per_forecast = table.drop_duplicates(["instance", "project", "cutoff", "horizon_days"])["pace_ratio"]
    shifted = (per_forecast < PACE_LOW) | (per_forecast > PACE_HIGH)
    distribution = pd.Series(
        {
            "forecasts": len(per_forecast),
            **{f"p{q:02d}": float(per_forecast.quantile(q / 100)) for q in (5, 25, 50, 75, 95)},
            f"below_{PACE_LOW}": float((per_forecast < PACE_LOW).mean()),
            f"above_{PACE_HIGH}": float((per_forecast > PACE_HIGH).mean()),
            "shifted": float(shifted.mean()),
        }
    )
    rows = []
    for method, label in METHODS.items():
        t = table[table["method"] == method]
        miss = (t["pit"] < 0.1) | (t["pit"] >= 0.9)
        moved = (t["pace_ratio"] < PACE_LOW) | (t["pace_ratio"] > PACE_HIGH)
        rows.append(
            {
                "method": method,
                "label": label,
                "outer_tenth_misses": int(miss.sum()),
                "misses_with_pace_shift": float(moved[miss].mean()),
                "others_with_pace_shift": float(moved[~miss].mean()),
                "pace_shifts_that_missed": float(miss[moved].mean()),
            }
        )
    return distribution, pd.DataFrame(rows)


def pace_by_horizon(table: pd.DataFrame) -> pd.DataFrame:
    """The pace ratio's distribution over forecasts, for all horizons and for each."""
    per_forecast = table.drop_duplicates(["instance", "project", "cutoff", "horizon_days"])
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

    blocks = run_blocks(frozen)
    keys = ["instance", "project", "cutoff", "horizon_days"]
    pace = blocks.drop_duplicates(keys)[[*keys, "pace_ratio"]]
    results = pd.read_parquet(HERE / "data" / "results.parquet")
    table = pd.concat([baselines(results, pace), blocks], ignore_index=True)
    table["experimental"] = table["method"].isin(EXPERIMENTAL)
    table.to_parquet(HERE / "data" / "experiment_3b.parquet", index=False, compression="zstd")

    verdict = pass_test(table)
    distribution, by_method = pace_shift(table)
    verdict.merge(by_method.drop(columns="label"), on="method").round(4).to_csv(
        HERE / "data" / "experiment_3b_summary.csv", index=False
    )
    horizons = pace_by_horizon(table)
    horizons.round(4).to_csv(HERE / "data" / "experiment_3b_pace.csv", index=False)

    print(f"{len(table):,} rows ({len(blocks):,} block forecasts + {len(table) - len(blocks):,} baseline) -> data/experiment_3b.parquet\n")
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
    print("\nPace shift (horizon daily rate / history daily rate), one value per forecast:")
    print(f"  {distribution['shifted']:.1%} of forecasts are below {PACE_LOW} or above {PACE_HIGH}")
    print(horizons.round(3).to_string(index=False))
    for r in by_method.itertuples():
        print(
            f"  {r.label:<37} {r.outer_tenth_misses:>5} outer-tenth misses; {r.misses_with_pace_shift:.1%} of them had a "
            f"pace shift, against {r.others_with_pace_shift:.1%} of the other forecasts"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

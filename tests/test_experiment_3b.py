"""Experiment 3b's block sampler and its reporting (experiment_3b.py).

The sampler is experiment code, not FlowAugur's engine, so it is tested here
rather than in the app's repository.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import experiment_3b as x
import replay


def _rng(seed: int = 0) -> np.random.Generator:
    return np.random.default_rng(seed)


def _window_sums(history: np.ndarray, length: int, last_start: int) -> set[float]:
    """Sums of every run of ``length`` days starting at day 0 .. ``last_start``."""
    return {float(history[s : s + length].sum()) for s in range(last_start + 1)}


# ---- the sampler ------------------------------------------------------------


def test_constant_history_gives_exact_totals() -> None:
    for block_days in x.BLOCK_DAYS:
        for horizon_days in (14, 28, 56, 84):
            totals = x.block_totals(np.full(182, 3), horizon_days, block_days, 500, _rng())
            assert totals.shape == (500,)
            assert (totals == 3 * horizon_days).all()


def test_one_block_is_a_real_run_of_consecutive_days() -> None:
    history = _rng(1).poisson(6, 182).astype(float)
    totals = x.block_totals(history, 14, 14, 4000, _rng(2))
    # Every total is the sum of 14 consecutive real days, and nothing else.
    assert set(totals) <= _window_sums(history, 14, last_start=182 - 14)


def test_start_points_are_uniform_over_every_full_block() -> None:
    # Powers of two make every block sum unique, so a total identifies its start day.
    history = 2.0 ** np.arange(30)
    block_days = 14
    n_starts = len(history) - block_days + 1  # 17 full blocks
    start_of = {float(history[s : s + block_days].sum()): s for s in range(n_starts)}
    totals = x.block_totals(history, block_days, block_days, 34_000, _rng(3))
    counts = pd.Series([start_of[t] for t in totals]).value_counts()
    assert sorted(counts.index) == list(range(n_starts))  # first and last full block both drawn
    expected = len(totals) / n_starts
    assert ((counts - expected).abs() < 5 * np.sqrt(expected)).all()


def test_last_block_is_cut_to_the_horizon() -> None:
    history = 2.0 ** np.arange(40)  # every run of days has a unique sum
    # A 14-day horizon from 28-day blocks: the first 14 days of a full 28-day block,
    # so the start can be no later than the last place a whole 28-day block fits.
    totals = x.block_totals(history, 14, 28, 4000, _rng(5))
    assert set(totals) == _window_sums(history, 14, last_start=40 - 28)
    # A 35-day horizon from 14-day blocks: two full blocks and a 7-day stub.
    full = _window_sums(history, 14, last_start=40 - 14)
    stub = _window_sums(history, 7, last_start=40 - 14)
    reachable = {a + b + c for a in full for b in full for c in stub}
    assert set(x.block_totals(history, 35, 14, 500, _rng(6))) <= reachable


def test_blocks_are_joined_end_to_end_to_cover_the_horizon() -> None:
    history = 2.0 ** np.arange(40)
    totals = x.block_totals(history, 28, 14, 2000, _rng(7))
    blocks = _window_sums(history, 14, last_start=40 - 14)
    assert set(totals) <= {a + b for a in blocks for b in blocks}
    assert len(set(totals)) > 100  # the two blocks are drawn independently


def test_block_resampling_keeps_streaks_that_day_resampling_loses() -> None:
    # Thirteen quiet weeks then thirteen busy ones: real runs are all-quiet or all-busy.
    history = np.concatenate([np.zeros(91), np.full(91, 10.0)])
    blocks = x.block_totals(history, 14, 14, 20_000, _rng(8))
    days = _rng(9).choice(history, size=(20_000, 14)).sum(axis=1)
    assert blocks.mean() == pytest.approx(days.mean(), rel=0.05)  # same level
    assert blocks.std() > 3 * days.std()  # much wider range


def test_same_seed_same_totals() -> None:
    history = _rng(10).poisson(5, 182)
    first = x.block_totals(history, 56, 14, 1000, _rng(11))
    second = x.block_totals(history, 56, 14, 1000, _rng(11))
    np.testing.assert_array_equal(first, second)
    assert not np.array_equal(first, x.block_totals(history, 56, 14, 1000, _rng(12)))


def test_history_shorter_than_a_block_is_rejected() -> None:
    with pytest.raises(ValueError, match="shorter than one 28-day block"):
        x.block_totals(np.ones(20), 14, 28, 10, _rng())


def test_streams_differ_by_project_cutoff_block_and_horizon() -> None:
    cutoff = pd.Timestamp("2020-07-06")
    base = x.rng_for("Apache", "SPARK", cutoff, 14, 28).integers(0, 10**9, 5)
    same = x.rng_for("Apache", "SPARK", cutoff, 14, 28).integers(0, 10**9, 5)
    np.testing.assert_array_equal(base, same)
    for other in (
        x.rng_for("Apache", "FLINK", cutoff, 14, 28),
        x.rng_for("Apache", "SPARK", cutoff + pd.Timedelta(days=14), 14, 28),
        x.rng_for("Apache", "SPARK", cutoff, 28, 28),
        x.rng_for("Apache", "SPARK", cutoff, 14, 56),
    ):
        assert not np.array_equal(base, other.integers(0, 10**9, 5))


# ---- the pass test ----------------------------------------------------------


def _table(method: str, pit: list[float], hit_p85: list[bool], crps: float, ratio: list[float], expected: float = 0.1) -> pd.DataFrame:
    n = len(pit)
    return pd.DataFrame(
        {
            "instance": "Demo",
            "project": "A",
            "cutoff": pd.date_range("2020-01-06", periods=n, freq="14D").date,
            "horizon_days": 14,
            "method": method,
            "pace_ratio": ratio,
            "expected_shift": expected,
            "pit": pit,
            "crps": crps,
            "hit_p50": True,
            "hit_p70": True,
            "hit_p85": hit_p85,
            "hit_p95": False,
        }
    )


def test_pass_test_needs_all_three_and_uses_the_declared_thresholds() -> None:
    ratio = [1.0] * 10
    good = _table("block14", [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95], [True] * 8 + [False] * 2, 64.0, ratio)
    tails = _table("block28", [0.05] * 2 + [0.95] * 2 + [0.5] * 6, [True] * 8 + [False] * 2, 60.0, ratio)
    high = _table("raw", [0.5] * 10, [True] * 10, 60.0, ratio)
    costly = _table("shipped", [0.5] * 10, [True] * 9 + [False], 64.1, ratio)
    verdict = x.pass_test(pd.concat([good, tails, high, costly])).set_index("method")

    assert verdict.loc["block14", ["p85_in_range", "outer_tenths_ok", "crps_ok", "passes"]].tolist() == [True, True, True, True]
    assert verdict.loc["block14", "outer_tenths"] == pytest.approx(0.2)
    assert verdict.loc["block28", "outer_tenths"] == pytest.approx(0.4)  # 40% in the tails: over the 30% limit
    assert verdict.loc["block28", ["outer_tenths_ok", "passes"]].tolist() == [False, False]
    assert verdict.loc["raw", ["p85_in_range", "passes"]].tolist() == [False, False]  # 100% is above 90%
    assert verdict.loc["shipped", ["crps_ok", "passes"]].tolist() == [False, False]  # 64.1 is over 64


# ---- the pace-shift diagnostic ----------------------------------------------


def test_pace_ratio_is_horizon_rate_over_history_rate() -> None:
    hist = pd.Series(np.full(182, 2.0))
    assert x.pace_ratio(hist, 28, 14) == pytest.approx(1.0)
    assert x.pace_ratio(hist, 56, 14) == pytest.approx(2.0)
    assert x.pace_ratio(hist, 84, 84) == pytest.approx(0.5)
    assert np.isnan(x.pace_ratio(pd.Series(np.zeros(182)), 5, 14))


def test_a_shift_is_strictly_outside_the_declared_band() -> None:
    ratios = np.array([0.79, 0.8, 1.0, 1.25, 1.26])
    assert x.is_shift(ratios).tolist() == [True, False, False, False, True]


def test_expected_shift_is_the_share_of_a_forecasts_own_trials_that_shift() -> None:
    hist = pd.Series(np.full(182, 2.0))  # 2 a day, so 28 over a 14-day horizon is a ratio of 1
    #                  1.0   0.75  1.29  0.82  1.21
    totals = np.array([28.0, 21.0, 36.0, 23.0, 34.0])
    assert x.expected_shift(hist, totals, 14) == pytest.approx(2 / 5)
    assert x.expected_shift(hist, np.full(100, 28.0), 14) == 0.0
    assert np.isnan(x.expected_shift(pd.Series(np.zeros(182)), totals, 14))


def test_expected_shift_matches_the_sampler_it_is_given() -> None:
    # Quiet then busy: block totals swing far more than day totals, so far more of them shift.
    hist = pd.Series(np.concatenate([np.zeros(91), np.full(91, 10.0)]))
    blocks = x.block_totals(hist.to_numpy(), 14, 14, 20_000, _rng(1))
    days = _rng(2).choice(hist.to_numpy(), size=(20_000, 14)).sum(axis=1)
    assert x.expected_shift(hist, blocks, 14) > 0.9
    assert x.expected_shift(hist, days, 14) < x.expected_shift(hist, blocks, 14) - 0.2


def test_pace_shift_is_observed_minus_expected_by_horizon_and_method() -> None:
    ratio = [0.7, 1.0, 1.0, 1.3]  # two of four forecasts shifted
    frames = [_table(m, [0.5] * 4, [True] * 4, 60.0, ratio, expected=e) for m, e in (("raw", 0.1), ("block14", 0.2), ("block28", 0.3), ("shipped", 0.9))]
    table = pd.concat(frames)
    later = table.assign(horizon_days=84, pace_ratio=1.0)  # nobody shifted at 12 weeks
    out = x.pace_shift(pd.concat([table, later])).set_index(["horizon_days", "method"])

    assert list(out.index) == [(h, m) for h in (14, 84) for m in ("raw", "block14", "block28")]  # as shipped is not in it
    assert out["forecasts"].tolist() == [4] * 6
    assert out.loc[14, "observed_shift"].tolist() == [0.5, 0.5, 0.5]  # the same actuals for every method
    assert out.loc[14, "expected_shift"].tolist() == pytest.approx([0.1, 0.2, 0.3])
    assert out.loc[14, "excess_shift"].tolist() == pytest.approx([0.4, 0.3, 0.2])
    assert out.loc[84, "excess_shift"].tolist() == pytest.approx([-0.1, -0.2, -0.3])


def test_pace_distribution_reports_all_horizons_then_each() -> None:
    table = pd.concat([_table(m, [0.5] * 4, [True] * 4, 60.0, [0.5, 1.0, 1.0, 1.5]) for m in x.METHODS])
    table["horizon_days"] = [14, 14, 28, 28] * len(x.METHODS)
    out = x.pace_distribution(table).set_index("horizon_days")
    assert list(out.index) == ["all", "14", "28"]
    assert out["forecasts"].tolist() == [4, 2, 2]
    assert out.loc["all", "p50"] == pytest.approx(1.0)
    assert out.loc["14", "below_0.8"] == pytest.approx(0.5)
    assert out.loc["28", "above_1.25"] == pytest.approx(0.5)


# ---- assembling the table ---------------------------------------------------


def _scored(method: str, crps: float) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "instance": "Demo",
            "project": "A",
            "cutoff": pd.date_range("2020-01-06", periods=2, freq="14D").date,
            "horizon_days": 14,
            "method": method,
            "actual": 10,
            "mean": 9.5,
        }
    )
    for column in replay.SCORE_COLUMNS:
        frame[column] = column.startswith("hit_") or crps
    return frame


def _session_3_results(raw_crps: float) -> pd.DataFrame:
    frames = []
    for method, drop, crps in (("raw", False, raw_crps), ("shipped", True, 7.0)):
        frames.append(_scored(method, crps).drop(columns="method").assign(bucket="day", history_days=182, outlier_drop=drop))
    return pd.concat(frames, ignore_index=True)


def _simulated() -> pd.DataFrame:
    frames = [_scored(m, c).assign(pace_ratio=1.1, expected_shift=0.05) for m, c in (("raw", 5.0), ("block14", 4.0), ("block28", 4.5))]
    return pd.concat(frames, ignore_index=True)


def test_assemble_joins_the_four_methods_and_flags_the_experiment() -> None:
    table = x.assemble(_simulated(), _session_3_results(raw_crps=5.0))
    assert list(dict.fromkeys(table["method"])) == list(x.METHODS)
    assert table.groupby("method").size().tolist() == [2, 2, 2, 2]
    assert table.loc[table["experimental"], "method"].unique().tolist() == ["block14", "block28"]
    shipped = table[table["method"] == "shipped"]
    assert (shipped["pace_ratio"] == 1.1).all()  # the actuals' ratio, whatever the method
    assert shipped["expected_shift"].isna().all()  # as shipped is not part of the diagnostic


def test_assemble_refuses_raw_forecasts_that_are_not_session_3s() -> None:
    with pytest.raises(RuntimeError, match="differ from data/results.parquet"):
        x.assemble(_simulated(), _session_3_results(raw_crps=5.5))

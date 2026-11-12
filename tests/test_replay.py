"""Replay wiring (replay.py): the qualifying window, cutoffs, the no-leakage
history/horizon split, the app-faithful throughput prep, and the scoring. The
forecast itself is FlowAugur's engine, vendored under engine/ and tested in the
app's own repository.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import replay
from engine.metrics import bucket_daily_series, remove_outliers_iqr

MONDAY = pd.Timestamp("2019-01-07")
DAY = pd.Timedelta(days=1)
WEEK = pd.Timedelta(weeks=1)


def _daily(values, start: pd.Timestamp = MONDAY) -> pd.Series:
    index = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(np.asarray(values), index=index, name="throughput")


# ---- qualifying window ------------------------------------------------------


def test_weekly_sums_monday_weeks_and_drops_the_last_observed_week() -> None:
    # Two full weeks and one extra day: the trailing, partly-observed week goes.
    weeks = replay.weekly(_daily([1] * 15))
    assert weeks.tolist() == [7, 7]
    assert list(weeks.index) == [MONDAY, MONDAY + WEEK]


def test_qualifying_window_takes_the_most_recent_of_equally_steady_windows() -> None:
    # 300 weeks at 14 done/week: every 104-week window is 100% steady.
    start, end = replay.qualifying_window(_daily([2] * 7 * 300))
    # The 300th week is dropped as partly observed, so the window ends at week 299.
    assert end == MONDAY + 299 * WEEK - DAY
    assert start == end - 104 * WEEK + DAY


def test_qualifying_window_follows_the_steady_stretch() -> None:
    # Steady for 150 weeks, then 7 done/week (below the 10-item bar).
    start, end = replay.qualifying_window(_daily([2] * 7 * 150 + [1] * 7 * 150))
    assert end == MONDAY + 150 * WEEK - DAY
    assert start == MONDAY + 46 * WEEK


@pytest.mark.parametrize(
    "values",
    [
        [1] * 7 * 200,  # long enough, never 10 a week
        [2] * 7 * 100,  # steady, but under 104 complete weeks
    ],
)
def test_qualifying_window_rejects_projects_that_do_not_qualify(values: list[int]) -> None:
    with pytest.raises(ValueError, match="no 104-week window"):
        replay.qualifying_window(_daily(values))


def test_cutoffs_leave_room_for_longest_history_and_horizon_inside_the_window() -> None:
    window = (MONDAY, MONDAY + 104 * WEEK - DAY)
    cuts = replay.cutoffs(window)
    assert len(cuts) == 34
    assert cuts[0] - 182 * DAY == window[0]  # 26 weeks of history start at the window start
    assert cuts[-1] + 83 * DAY == window[1]  # the 12-week horizon ends at the window end
    assert {b - a for a, b in zip(cuts, cuts[1:])} == {14 * DAY}


# ---- history / horizon split (no leakage) -----------------------------------


def test_history_is_the_days_before_the_cutoff_and_zero_fills() -> None:
    daily = _daily(range(1, 101))
    cutoff = MONDAY + 90 * DAY
    hist = replay.history(daily, cutoff, 84)
    assert len(hist) == 84
    assert hist.index[0] == cutoff - 84 * DAY
    assert hist.index[-1] == cutoff - DAY  # the cutoff day itself is not history
    assert hist.tolist() == list(range(7, 91))
    # A window reaching back before the first observed day is padded with zeros.
    early = replay.history(daily, MONDAY + 2 * DAY, 84)
    assert early.tolist() == [0] * 82 + [1, 2]


def test_actual_counts_the_cutoff_day_through_the_last_horizon_day() -> None:
    values = [0] * 200
    values[100] = 5  # the cutoff day
    values[113] = 7  # last day of a 14-day horizon
    values[114] = 100  # first day after it
    values[99] = 1000  # the day before the cutoff
    daily = _daily(values)
    assert replay.actual(daily, MONDAY + 100 * DAY, 14) == 12


def test_nothing_on_or_after_the_cutoff_reaches_the_forecast_inputs() -> None:
    rng = np.random.default_rng(1)
    daily = _daily(rng.poisson(5, 400))
    cutoff = MONDAY + 300 * DAY
    poisoned = daily.copy()
    poisoned[poisoned.index >= cutoff] = 10**6
    for history_days in replay.HISTORY_DAYS:
        for bucket in replay.BUCKETS:
            for drop in (True, False):
                clean, _ = replay.prepare(replay.history(daily, cutoff, history_days), bucket, drop)
                dirty, _ = replay.prepare(replay.history(poisoned, cutoff, history_days), bucket, drop)
                np.testing.assert_array_equal(clean, dirty)


# ---- throughput prep mirrors the app ----------------------------------------


def test_prepare_drops_outlier_days_before_bucketing_like_the_app() -> None:
    values = [3, 4, 5, 4, 3, 4, 5] * 12
    values[10] = 500  # one bulk day
    hist = replay.history(_daily(values), MONDAY + 84 * DAY, 84)

    samples, dropped = replay.prepare(hist, "week", drop_outliers=True)
    expected = bucket_daily_series(remove_outliers_iqr(hist), "weekly")
    np.testing.assert_array_equal(samples, expected.to_numpy(dtype=float))
    assert dropped == 1
    assert samples.max() < 100  # the bulk day is gone from its week

    raw, dropped = replay.prepare(hist, "week", drop_outliers=False)
    assert dropped == 0
    assert raw.max() > 500


def test_prepare_bucket_sizes() -> None:
    hist = replay.history(_daily([1] * 200), MONDAY + 190 * DAY, 84)
    assert replay.prepare(hist, "day", False)[0].tolist() == [1.0] * 84
    assert replay.prepare(hist, "week", False)[0].tolist() == [7.0] * 12
    assert replay.prepare(hist, "2 weeks", False)[0].tolist() == [14.0] * 6


# ---- scoring ----------------------------------------------------------------


def test_crps_matches_the_pairwise_definition() -> None:
    rng = np.random.default_rng(0)
    x = rng.poisson(40, 300).astype(float)
    brute = np.mean(np.abs(x - 37)) - 0.5 * np.mean(np.abs(x[:, None] - x[None, :]))
    assert replay.crps(x, 37) == pytest.approx(brute)


def test_crps_of_a_point_forecast_is_the_absolute_error() -> None:
    assert replay.crps(np.full(50, 12.0), 15) == pytest.approx(3.0)
    assert replay.crps(np.full(50, 12.0), 12) == pytest.approx(0.0)


def test_coverage_is_the_share_of_bands_that_held() -> None:
    results = pd.DataFrame(
        {
            "outlier_drop": [True, True, False, False],
            "hit_p50": [True, False, True, True],
            "hit_p70": [True, True, True, True],
            "hit_p85": [True, True, False, False],
            "hit_p95": [False, False, False, True],
        }
    )
    table = replay.coverage(results, ["outlier_drop"])
    rows = {drop: row.tolist() for drop, row in zip(table.index, table.to_numpy())}
    assert rows == {True: [2, 0.5, 1.0, 1.0, 0.0], False: [2, 1.0, 1.0, 0.0, 0.5]}
    assert list(table.columns) == ["forecasts", "P50", "P70", "P85", "P95"]


# ---- end to end -------------------------------------------------------------


def test_replay_of_constant_throughput_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(replay, "N_TRIALS", 200)
    results = replay.replay(_daily([3] * 7 * 160), "Demo", "FLAT")

    # 34 cutoffs x 2 histories x 3 buckets x 2 outlier settings x 4 horizons.
    assert len(results) == 34 * 2 * 3 * 2 * 4
    assert results["cutoff"].nunique() == 34
    expected = 3 * results["horizon_days"]
    for column in ("actual", "mean", "naive", "p50", "p70", "p85", "p95"):
        assert np.allclose(results[column], expected), column
    assert results[["hit_p50", "hit_p70", "hit_p85", "hit_p95"]].all().all()
    assert np.allclose(results["crps"], 0.0)
    assert (results["pit"] == 1.0).all()
    assert (results["days_dropped"] == 0).all()  # IQR is zero on a flat series
    samples = results.groupby(["bucket", "history_days"])["n_samples"].first().to_dict()
    assert samples == {
        ("day", 84): 84, ("day", 182): 182,
        ("week", 84): 12, ("week", 182): 26,
        ("2 weeks", 84): 6, ("2 weeks", 182): 13,
    }  # fmt: skip


def test_replay_is_reproducible_and_keeps_bands_ordered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(replay, "N_TRIALS", 200)
    daily = _daily(np.random.default_rng(7).poisson(6, 7 * 150))
    first = replay.replay(daily)
    second = replay.replay(daily)
    pd.testing.assert_frame_equal(first, second)
    assert (first["p50"] >= first["p70"]).all()
    assert (first["p70"] >= first["p85"]).all()
    assert (first["p85"] >= first["p95"]).all()
    for level in (50, 70, 85, 95):
        assert ((first["actual"] >= first[f"p{level}"]) == first[f"hit_p{level}"]).all()
    assert first["pit"].between(0, 1).all()

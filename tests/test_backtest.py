"""The full-run wiring (backtest.py): the frozen-window gate and the summaries."""
from __future__ import annotations

import numpy as np
import pandas as pd

import backtest
import replay

MONDAY = pd.Timestamp("2019-01-07")
DAY = pd.Timedelta(days=1)
WEEK = pd.Timedelta(weeks=1)


def _steady(instance: str, project: str) -> pd.Series:
    """300 weeks at 14 done a week; the qualifying window is the last complete 104."""
    index = pd.date_range(MONDAY, periods=7 * 300, freq="D")
    return pd.Series(2, index=index, name="throughput")


def _frozen_row(**changes) -> pd.DataFrame:
    end = MONDAY + 299 * WEEK - DAY
    start = end - 104 * WEEK + DAY
    row = {
        "instance": "Demo",
        "project": "FLAT",
        "window_start": start,
        "window_end": end,
        "first_cutoff": start + 182 * DAY,
        "last_cutoff": end + DAY - 84 * DAY,
    }
    return pd.DataFrame([row | changes])


def test_gate_passes_when_the_frozen_table_matches() -> None:
    assert backtest.check_windows(_frozen_row(), load=_steady) == []


def test_gate_reports_each_field_that_differs() -> None:
    wrong = _frozen_row(first_cutoff=MONDAY, window_end=MONDAY + 500 * WEEK)
    problems = backtest.check_windows(wrong, load=_steady)
    assert len(problems) == 2
    assert any("window_end" in p and "frozen as" in p for p in problems)
    assert any("first_cutoff" in p for p in problems)
    assert all(p.startswith("Demo FLAT:") for p in problems)


def test_gate_reports_a_project_with_no_qualifying_window() -> None:
    def too_quiet(instance: str, project: str) -> pd.Series:
        return _steady(instance, project) // 2  # 7 a week, under the 10-item bar

    problems = backtest.check_windows(_frozen_row(), load=too_quiet)
    assert len(problems) == 1
    assert "no 104-week window" in problems[0]


def test_committed_frozen_table_is_the_plans_24_projects() -> None:
    frozen = backtest.frozen_windows()
    assert len(frozen) == 24
    assert frozen["instance"].nunique() == 10
    assert "Mojang" not in set(frozen["instance"])
    assert not frozen.duplicated(["instance", "project"]).any()
    # Internally consistent: 104 Monday-start weeks, room for 26 weeks before and 12 after.
    assert (frozen["window_start"].dt.dayofweek == 0).all()
    assert ((frozen["window_end"] - frozen["window_start"]) == 104 * WEEK - DAY).all()
    assert ((frozen["first_cutoff"] - frozen["window_start"]) == 182 * DAY).all()
    assert ((frozen["window_end"] - frozen["last_cutoff"]) == 83 * DAY).all()


def test_committed_daily_export_still_produces_the_frozen_windows() -> None:
    assert backtest.check_windows(backtest.frozen_windows()) == []


def test_app_default_and_hit_rates() -> None:
    results = pd.DataFrame(
        {
            "instance": "Demo",
            "project": ["A", "A", "A", "A", "B", "B"],
            "bucket": ["day", "day", "week", "day", "day", "day"],
            "history_days": [182, 182, 182, 84, 182, 182],
            "outlier_drop": True,
            "hit_p50": [True, False, True, True, True, True],
            "hit_p70": True,
            "hit_p85": [True, False, False, False, False, False],
            "hit_p95": False,
        }
    )
    subset = backtest.app_default(results)
    assert len(subset) == 4  # the week-bucket and 84-day rows are not the app's defaults
    rates = backtest.hit_rates(subset, ["outlier_drop"])
    assert list(rates.columns) == ["p50", "p70", "p85", "p95"]
    assert rates.loc[("Demo", "A", True)].to_numpy().tolist() == [0.5, 1.0, 0.5, 0.0]
    assert rates.loc[("Demo", "B", True)].to_numpy().tolist() == [1.0, 1.0, 0.0, 0.0]


def test_project_summary_has_one_row_per_project_with_both_settings(monkeypatch) -> None:
    monkeypatch.setattr(replay, "N_TRIALS", 100)
    daily = _steady("Demo", "FLAT")
    results = replay.replay(daily, "Demo", "FLAT")
    summary = backtest.project_summary(_frozen_row(), results, {("Demo", "FLAT"): "daily"})
    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["cutoffs"] == 34
    assert row["cadence"] == "daily"
    # Constant throughput: every band holds, as shipped and raw.
    assert np.allclose([row[f"p{level}_{kind}"] for level in (50, 70, 85, 95) for kind in ("shipped", "raw")], 1.0)

"""Daily throughput per project, steadiness metrics, and a sparkline sheet.

Reads ``data/done_dates.parquet`` (from ``extract_done_dates.py``) and applies
the backtest's counting rule — Story, Task, Bug and Improvement only, no
sub-tasks — then writes:

``data/daily_throughput.parquet``
    instance, project, issue_type, day (UTC), done. Only days with at least one
    done item; zero-fill when building a series. Small enough to commit, and all
    later sessions need.

``data/project_overview.csv``
    Per project: active span, done count, median week, the share of weeks with
    at least 10 done items over the whole span, and the same share in its best
    2-year (104-week) window. These are the inputs to the project filter.

``figures/weekly_throughput.png``
    One weekly-throughput sparkline per project whose best 2-year window has at
    least 10 done items in more than half its weeks — the loosest reading of the
    project filter, so every plausible candidate is on the sheet.

Usage::

    uv run throughput_overview.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
COUNTED_TYPES = ["Story", "Task", "Bug", "Improvement"]
MIN_ITEMS = 10  # "at least 10 done items" in a week
WINDOW = 104  # weeks: "at least 2 years"

SURFACE, SERIES, INK, MUTED, RULE = "#fcfcfb", "#2a78d6", "#0b0b0b", "#52514e", "#b9b8b2"


def daily_throughput(done: pd.DataFrame) -> pd.DataFrame:
    counted = (
        done["done_date"].notna()
        & done["issue_type"].isin(COUNTED_TYPES)
        & ~done["is_subtask"].fillna(False).astype(bool)
    )
    d = done.loc[counted, ["instance", "project", "issue_type"]].copy()
    d["day"] = done.loc[counted, "done_date"].dt.tz_convert(None).dt.normalize()
    out = d.groupby(["instance", "project", "issue_type", "day"]).size().rename("done").reset_index()
    out["done"] = out["done"].astype("int32")
    return out


def weekly_series(daily: pd.DataFrame) -> dict[tuple[str, str], pd.Series]:
    """Zero-filled Monday-start weekly counts per project, first to last done week."""
    week = daily["day"].dt.to_period("W-SUN").dt.start_time
    wk = daily.groupby(["instance", "project", week.rename("week")])["done"].sum()
    series = {}
    for (instance, project), s in wk.groupby(level=[0, 1]):
        s = s.droplevel([0, 1])
        series[(instance, project)] = s.reindex(
            pd.date_range(s.index.min(), s.index.max(), freq="7D"), fill_value=0
        )
    return series


def overview(series: dict[tuple[str, str], pd.Series]) -> pd.DataFrame:
    rows = []
    for (instance, project), s in series.items():
        steady = pd.Series((s >= MIN_ITEMS).to_numpy(), dtype=float)
        best = steady.rolling(WINDOW).mean().max() if len(s) >= WINDOW else np.nan
        rows.append(
            {
                "instance": instance,
                "project": project,
                "first_week": s.index[0].date(),
                "last_week": s.index[-1].date(),
                "span_weeks": len(s),
                "done": int(s.sum()),
                "median_week": float(s.median()),
                "max_week": int(s.max()),
                "share_weeks_ge10": round(float(steady.mean()), 4),
                "best_2y_share_weeks_ge10": round(float(best), 4),
            }
        )
    return pd.DataFrame(rows)


def sparkline_sheet(series: dict, table: pd.DataFrame, path: Path, ncols: int = 7) -> None:
    show = table[table["best_2y_share_weeks_ge10"] > 0.5].sort_values(
        ["instance", "best_2y_share_weeks_ge10", "done"], ascending=[True, False, False]
    )
    nrows = -(-len(show) // ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(2.75 * ncols, 1.02 * nrows + 1.1), facecolor=SURFACE)
    x0 = min(series[k].index[0] for k in zip(show["instance"], show["project"]))
    x1 = max(series[k].index[-1] for k in zip(show["instance"], show["project"]))
    for ax, row in zip(axes.flat, show.itertuples()):
        s = series[(row.instance, row.project)]
        # Bulk closes dwarf the weekly rhythm: clip the axis and flag the clipped weeks.
        cap = max(1.2 * MIN_ITEMS, 2.0 * float(s.quantile(0.98)))
        y = np.minimum(s.to_numpy(), cap)
        ax.fill_between(s.index, y, color=SERIES, alpha=0.22, linewidth=0)
        ax.plot(s.index, y, color=SERIES, linewidth=0.7)
        clipped = s[s > cap]
        ax.plot(clipped.index, np.full(len(clipped), cap), "^", color=MUTED, markersize=2.5, clip_on=False)
        ax.axhline(MIN_ITEMS, color=RULE, linewidth=0.6, linestyle=(0, (4, 3)))
        ax.set_xlim(x0, x1)
        ax.set_ylim(0, cap * 1.08)
        ax.set_title(f"{row.instance} · {row.project}", fontsize=8, color=INK, loc="left", pad=11, fontweight="bold")
        ax.text(
            0, 1.03,
            f"median {row.median_week:.0f}/wk · max {row.max_week:,} · best 2y {row.best_2y_share_weeks_ge10:.0%}",
            transform=ax.transAxes, fontsize=6.3, color=MUTED, va="bottom",
        )  # fmt: skip
    years = pd.to_datetime([f"{y}-01-01" for y in range(2005, 2023, 5)])
    for i, ax in enumerate(axes.flat):
        ax.set_facecolor(SURFACE)
        ax.set_yticks([])
        ax.set_xticks(years)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(RULE)
        ax.tick_params(axis="x", length=2, color=RULE, labelsize=6, labelcolor=MUTED)
        last_in_column = i + ncols >= len(show)
        ax.set_xticklabels([str(y.year) for y in years] if last_in_column else [])
        if i >= len(show):
            ax.set_visible(False)
    fig.suptitle(
        f"Weekly done items per project — {len(show)} projects whose best 2-year window has "
        f"≥{MIN_ITEMS} done in more than half its weeks",
        x=0.012, y=0.997, ha="left", fontsize=13, color=INK, fontweight="bold",
    )  # fmt: skip
    fig.text(
        0.012, 1 - 0.62 / fig.get_figheight(),
        f"{', '.join(COUNTED_TYPES)}; no sub-tasks. Dashed line = {MIN_ITEMS} per week. "
        "Each panel has its own y-axis, clipped at 2× its 98th-percentile week; ▲ marks a clipped (bulk) week. "
        "“best 2y” = share of weeks with ≥10 done in the project's best 104-week window. "
        "Source: The Public Jira Dataset (Montgomery, Lüders & Maalej), CC BY 4.0.",
        ha="left", va="top", fontsize=8, color=MUTED,
    )  # fmt: skip
    fig.subplots_adjust(left=0.012, right=0.988, bottom=0.018, top=1 - 1.25 / fig.get_figheight(), wspace=0.1, hspace=0.95)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120, facecolor=SURFACE)
    plt.close(fig)


def main() -> int:
    data = HERE / "data"
    done = pd.read_parquet(data / "done_dates.parquet")
    daily = daily_throughput(done)
    daily.assign(day=daily["day"].dt.date).to_parquet(
        data / "daily_throughput.parquet", index=False, compression="zstd", compression_level=19
    )
    series = weekly_series(daily)
    table = overview(series)
    table.to_csv(data / "project_overview.csv", index=False)
    sparkline_sheet(series, table, HERE / "figures" / "weekly_throughput.png")

    best, whole, long_enough = table["best_2y_share_weeks_ge10"], table["share_weeks_ge10"], table["span_weeks"] >= WINDOW
    print(f"{int(daily['done'].sum()):,} counted done items in {len(table):,} projects")
    print(f"projects active for at least {WINDOW} weeks: {int(long_enough.sum())}")
    print(f"  >=10 done in >50% of all their weeks:            {int((long_enough & (whole > 0.5)).sum())}")
    for share in (0.5, 0.75, 0.9):
        op = ">" if share == 0.5 else ">="
        hit = best > share if share == 0.5 else best >= share
        print(f"  best 2-year window with >=10 done in {op}{share:.0%} of weeks: {int(hit.sum()):>4}")
    by_instance = table[best >= 0.9].groupby("instance").size()
    print(f"  the >=90% set by instance: {by_instance.to_dict()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

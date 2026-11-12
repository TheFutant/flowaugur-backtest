"""Charts for the full backtest, drawn from ``data/results.parquet``.

Every definition here was fixed before the full results existed:

* **Per-project rates.** A project's hit rate is the share of its forecasts
  whose band held. Each point is the mean of the 24 project rates (every project
  has the same number of forecasts, so this is also the pooled rate); the bar
  spans the interquartile range across projects; each faint dot is one project.
* **Calibration plot and PIT histogram** use the forecasts the app would make
  by default for these projects: day buckets and 26 weeks of history, all four
  horizons together, as shipped (IQR outlier drop on) and raw.
* **Bucket-size comparison** shows P85 coverage for day, week and 2-week
  buckets, for each history length, all horizons together. All 24 projects are
  steady by FlowAugur's own ``detect_cadence``, so there is no batchy group.
* **PIT** is the share of simulated totals at or below the actual. Ten equal
  bins; a calibrated forecast puts 10% of forecasts in each.

**Experiment 3b** (block resampling, not FlowAugur's engine) gets its own two
figures, ``calibration_3b.png`` and ``pit_histogram_3b.png``, drawn from
``data/experiment_3b.parquet`` with the same definitions. They show four
methods at the same settings: the two main-replay series above, and 14-day and
28-day block resampling on raw history.

**Where it breaks** (``where_it_breaks.png``) is drawn from the same table. For
each project and for the 8- and 12-week horizons it sets the share of forecasts
in which the pace shifted (the horizon's daily rate below 0.8 or above 1.25
times the history's) against the share the forecast's own simulated outcomes
expected, for single-day resampling on raw history. Projects are ordered by how
often their 85% forecast held as shipped.

Usage::

    uv run charts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

import backtest  # noqa: E402
import experiment_3b  # noqa: E402

HERE = Path(__file__).resolve().parent
FIGURES = HERE / "figures"

SURFACE, INK, MUTED, RULE = "#fcfcfb", "#0b0b0b", "#52514e", "#b9b8b2"
# outlier_drop -> (label, colour, marker). Validated pair; the marker shape repeats the identity.
SERIES = {True: ("As shipped (IQR outlier drop on)", "#2a78d6", "o"), False: ("Raw (no outlier drop)", "#eb6834", "s")}
LEVELS = [50, 70, 85, 95]
BUCKET_NAME = {"day": "day", "week": "week", "2 weeks": "2-week"}
# Experiment 3b: method -> (label, colour, marker). Colour follows the method, so the two
# main-replay series keep theirs. Validated in this order; marker shape repeats the identity.
METHOD_STYLE = {
    "shipped": ("Day buckets, as shipped", "#2a78d6", "o"),
    "raw": ("Day buckets, raw", "#eb6834", "s"),
    "block14": ("14-day blocks, raw (experiment)", "#1baf7a", "D"),
    "block28": ("28-day blocks, raw (experiment)", "#eda100", "^"),
}
PASS_MARK = (0.80, 0.90)  # the pass mark for P85, set before any results
SOURCE = "Source: The Public Jira Dataset (Montgomery, Lüders & Maalej), CC BY 4.0."


def style(ax: plt.Axes) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=RULE, linewidth=0.5, alpha=0.5)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.tick_params(colors=MUTED, labelsize=9.5, length=0)


def marks(ax: plt.Axes, x: float, rates: np.ndarray, color: str, marker: str) -> float:
    """One series at one x: project dots, interquartile bar and mean point."""
    mean = float(np.mean(rates))
    low, high = np.percentile(rates, [25, 75])
    ax.scatter(np.full(len(rates), x), rates, s=13, color=color, alpha=0.28, linewidths=0, marker=marker, zorder=2)
    ax.plot([x, x], [low, high], color=color, linewidth=2.2, solid_capstyle="round", zorder=3)
    ax.scatter([x], [mean], s=78, color=color, marker=marker, edgecolors=SURFACE, linewidths=1.6, zorder=4)
    return mean


def spread(ax: plt.Axes, x: float, rates: np.ndarray, drop: bool, label_side: int) -> float:
    """``marks`` for a main-replay series, with the mean labelled beside its point."""
    _, color, marker = SERIES[drop]
    mean = marks(ax, x, rates, color, marker)
    ax.annotate(
        f"{mean:.0%}", xy=(x, mean), xytext=(9 * label_side, 0), textcoords="offset points",
        ha="left" if label_side > 0 else "right", va="center", fontsize=10.5, color=INK, fontweight="bold",
    )  # fmt: skip
    return mean


def legend(fig: plt.Figure, y: float) -> None:
    handles = [
        Line2D([], [], color=color, marker=marker, markersize=8, markeredgecolor=SURFACE, linewidth=2.2, label=label)
        for label, color, marker in (SERIES[True], SERIES[False])
    ]
    handles.append(Line2D([], [], color=MUTED, marker="o", markersize=4, alpha=0.4, linewidth=0, label="one project"))
    handles.append(Patch(facecolor=RULE, alpha=0.55, label="pass mark for P85: 80–90%"))
    out = fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.036, y), ncols=4, frameon=False, fontsize=9.5, columnspacing=2.0)
    for text in out.get_texts():
        text.set_color(MUTED)


def titles(fig: plt.Figure, title: str, subtitle: str, footnote: str, title_y: float = 0.972, subtitle_y: float = 0.915) -> None:
    fig.suptitle(title, x=0.045, y=title_y, ha="left", fontsize=14, color=INK, fontweight="bold")
    fig.text(0.045, subtitle_y, subtitle, ha="left", va="top", fontsize=10, color=MUTED, linespacing=1.4)
    fig.text(0.045, 0.022, footnote + "\n" + SOURCE, ha="left", va="bottom", fontsize=8.5, color=MUTED, linespacing=1.4)


def save(fig: plt.Figure, name: str) -> None:
    FIGURES.mkdir(exist_ok=True)
    fig.savefig(FIGURES / name, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def percent_axis(ax: plt.Axes, lowest: float) -> None:
    """A 10%-step y-axis from just under the lowest project up to 100%."""
    bottom = np.floor(lowest * 10) / 10
    ax.set_ylim(bottom - 0.02, 1.03)
    ticks = np.arange(bottom, 1.001, 0.1)
    ax.set_yticks(ticks)
    ax.set_yticklabels([f"{v:.0%}" for v in ticks])


def position(rate: float) -> str:
    if rate < PASS_MARK[0]:
        return "below"
    return "above" if rate > PASS_MARK[1] else "inside"


def calibration(results: pd.DataFrame) -> dict:
    rates = backtest.hit_rates(backtest.app_default(results), ["outlier_drop"])
    n_projects = rates.index.droplevel("outlier_drop").nunique()
    fig, ax = plt.subplots(figsize=(12, 6.3), facecolor=SURFACE)
    ax.plot([40, 100], [0.40, 1.00], color=RULE, linewidth=1.1, linestyle=(0, (4, 3)), zorder=1)
    ax.annotate("perfectly calibrated", xy=(60, 0.60), xytext=(0, 7), textcoords="offset points", ha="center", fontsize=9, color=MUTED)
    ax.axhspan(*PASS_MARK, color=RULE, alpha=0.22, linewidth=0, zorder=0)  # spans the chart
    means: dict = {}
    for drop, offset in ((True, -1.5), (False, 1.5)):
        for level in LEVELS:
            values = rates.xs(drop, level="outlier_drop")[f"p{level}"].to_numpy()
            means[(drop, level)] = spread(ax, level + offset, values, drop, label_side=-1 if drop else 1)
    ax.set_xlim(42, 101)
    percent_axis(ax, float(rates.min().min()))
    ax.set_xticks(LEVELS)
    ax.set_xticklabels([f"P{level}" for level in LEVELS])
    ax.set_xlabel("Stated Confidence", fontsize=9.5, color=MUTED)
    ax.set_ylabel("Forecasts That Held", fontsize=9.5, color=MUTED)
    style(ax)
    shipped = means[(True, 85)]
    titles(
        fig,
        f"FlowAugur's 85% forecasts held {shipped:.0%} of the time across {n_projects} projects, "
        f"{position(shipped)} the 80–90% pass mark",
        f"Share of replayed forecasts in which the project finished at least the forecast amount. "
        f"{len(backtest.app_default(results)):,} forecasts at the app's defaults:\nday buckets, 26 weeks of history, horizons of 2, 4, 8 and 12 weeks.",
        "Point = mean of the project rates; bar = middle half of projects. Each project has 34 cutoff dates two weeks apart, "
        "so its forecasts overlap and are not independent.",
    )
    legend(fig, 0.845)
    fig.subplots_adjust(left=0.075, right=0.965, top=0.765, bottom=0.165)
    save(fig, "calibration.png")
    return means


def bucket_comparison(results: pd.DataFrame) -> dict:
    rates = backtest.hit_rates(results, ["history_days", "bucket", "outlier_drop"])["p85"]
    buckets = list(dict.fromkeys(results["bucket"]))  # day, week, 2 weeks
    panels = [(182, "26 weeks of history (the app's default)"), (84, "12 weeks of history")]
    fig, axes = plt.subplots(1, 2, figsize=(12, 6.3), sharey=True, facecolor=SURFACE)
    means: dict = {}
    for ax, (history_days, label) in zip(axes, panels):
        ax.axhspan(*PASS_MARK, color=RULE, alpha=0.22, linewidth=0, zorder=0)
        ax.axhline(0.85, color=RULE, linewidth=1.0, linestyle=(0, (4, 3)), zorder=1)
        for drop, offset in ((True, -0.13), (False, 0.13)):
            for i, bucket in enumerate(buckets):
                values = rates.xs((history_days, bucket, drop), level=("history_days", "bucket", "outlier_drop")).to_numpy()
                means[(history_days, bucket, drop)] = spread(ax, i + offset, values, drop, label_side=-1 if drop else 1)
        ax.set_xlim(-0.6, len(buckets) - 0.4)
        ax.set_xticks(range(len(buckets)))
        ax.set_xticklabels([f"{BUCKET_NAME[b]} buckets" for b in buckets])
        ax.set_title(label, fontsize=11.5, color=INK, loc="left", fontweight="bold", pad=10)
        style(ax)
    percent_axis(axes[0], float(rates.min()))
    axes[0].set_ylabel("P85 Forecasts That Held", fontsize=9.5, color=MUTED)
    shipped = [means[(182, b, True)] for b in buckets]
    if max(shipped) - min(shipped) < 0.03:
        lead = "Bucket size made little difference"
    elif shipped == sorted(shipped):
        lead = "Wider buckets held more often"
    else:
        lead = f"{BUCKET_NAME[buckets[int(np.argmax(shipped))]].capitalize()} buckets held most often"
    titles(
        fig,
        f"{lead}: P85 held {shipped[0]:.0%} with day, {shipped[1]:.0%} with week and {shipped[2]:.0%} with 2-week buckets",
        "Share of 85% forecasts that held, by the bucket size history is resampled in. All four horizons together.\n"
        "All 24 projects are steady by FlowAugur's own cadence detection, so this cannot show whether wider buckets help teams that close work in batches.",
        "Title figures: as shipped, 26 weeks of history. Point = mean of the project rates; bar = middle half of projects. Dashed line = 85%.\n"
        "12 weeks of history in 2-week buckets is 6 samples, below the 8 at which the app warns of thin data.",
    )
    legend(fig, 0.845)
    fig.subplots_adjust(left=0.075, right=0.965, top=0.735, bottom=0.185, wspace=0.06)
    save(fig, "bucket_comparison.png")
    return means


def pit_histogram(results: pd.DataFrame) -> dict:
    subset = backtest.app_default(results)
    edges = np.linspace(0, 1, 11)
    fig, axes = plt.subplots(1, 2, figsize=(12, 6.3), sharey=True, facecolor=SURFACE)
    shares: dict = {}
    for ax, drop in zip(axes, (True, False)):
        label, color, _ = SERIES[drop]
        pit = subset.loc[subset["outlier_drop"] == drop, "pit"].to_numpy()
        counts, _ = np.histogram(pit, bins=edges)
        share = counts / counts.sum()
        shares[drop] = share
        ax.bar(edges[:-1] + 0.05, share, width=0.086, color=color, linewidth=0)
        ax.axhline(0.10, color=INK, linewidth=1.0, linestyle=(0, (4, 3)), zorder=3)
        for i in (0, 9):
            ax.annotate(f"{share[i]:.0%}", xy=(edges[i] + 0.05, share[i]), xytext=(0, 4), textcoords="offset points", ha="center", fontsize=10.5, color=INK, fontweight="bold")
        ax.set_title(label, fontsize=11.5, color=INK, loc="left", fontweight="bold", pad=10)
        ax.set_xlim(0, 1)
        ax.set_xticks(np.linspace(0, 1, 6))
        labels = ax.set_xticklabels([f"{v:.0%}" for v in np.linspace(0, 1, 6)])
        labels[0].set_horizontalalignment("left")
        labels[-1].set_horizontalalignment("right")
        ax.set_xlabel("Share of Simulated Outcomes at or Below the Actual", fontsize=9.5, color=MUTED)
        style(ax)
    top = max(float(s.max()) for s in shares.values())
    axes[0].set_ylim(0, top * 1.14)
    axes[0].set_yticks(np.arange(0, top * 1.14, 0.1))
    axes[0].set_yticklabels([f"{v:.0%}" for v in np.arange(0, top * 1.14, 0.1)])
    axes[0].set_ylabel("Share of Forecasts", fontsize=9.5, color=MUTED)
    axes[1].annotate("calibrated: 10% in every bar", xy=(0.5, 0.10), xytext=(0, 4), textcoords="offset points", ha="center", fontsize=8.5, color=MUTED)
    tails = float(shares[True][0] + shares[True][9])
    titles(
        fig,
        f"{tails:.0%} of results fell in the forecast's outer tenths; a calibrated forecast gives 20%",
        "Where each actual result fell within its own forecast distribution. Left bars: the project did less than nearly every simulated outcome;\n"
        "right bars: more. Same forecasts as the calibration plot (day buckets, 26 weeks of history, all horizons).",
        "Title figure: as shipped. A forecast range that is too narrow piles results into the two end bars.",
    )
    fig.subplots_adjust(left=0.075, right=0.965, top=0.775, bottom=0.185, wspace=0.06)
    save(fig, "pit_histogram.png")
    return shares


def calibration_3b(table: pd.DataFrame) -> dict:
    """The calibration plot with the block-resampling experiment beside the main replay's series."""
    rates = table.groupby(["instance", "project", "method"])[backtest.HIT_COLUMNS].mean()
    fig, ax = plt.subplots(figsize=(12, 6.3), facecolor=SURFACE)
    ax.axhspan(*PASS_MARK, color=RULE, alpha=0.22, linewidth=0, zorder=0)  # spans the chart
    ax.plot([40, 100], [0.40, 1.00], color=RULE, linewidth=1.1, linestyle=(0, (4, 3)), zorder=1)
    ax.annotate("perfectly calibrated", xy=(60, 0.60), xytext=(0, 7), textcoords="offset points", ha="center", fontsize=9, color=MUTED)
    means: dict = {}
    for (method, (_, color, marker)), offset in zip(METHOD_STYLE.items(), (-3.3, -1.1, 1.1, 3.3)):
        for level in LEVELS:
            values = rates.xs(method, level="method")[f"hit_p{level}"].to_numpy()
            means[(method, level)] = marks(ax, level + offset, values, color, marker)
            if level == 85:  # the pass test is on P85: label only those, in a row above the group
                ax.annotate(
                    f"{means[(method, level)]:.0%}", xy=(level + offset, 0.985), ha="center", va="bottom",
                    fontsize=10.5, color=INK, fontweight="bold",
                )  # fmt: skip
    ax.set_xlim(42, 101)
    percent_axis(ax, float(rates.min().min()))
    ax.set_xticks(LEVELS)
    ax.set_xticklabels([f"P{level}" for level in LEVELS])
    ax.set_xlabel("Stated Confidence", fontsize=9.5, color=MUTED)
    ax.set_ylabel("Forecasts That Held", fontsize=9.5, color=MUTED)
    style(ax)
    raw, b14, b28 = (means[(m, 85)] for m in ("raw", "block14", "block28"))
    best = max(b14, b28)
    direction = "up from" if best > raw + 0.005 else "down from" if best < raw - 0.005 else "against"
    titles(
        fig,
        f"Block resampling: P85 held {b14:.0%} (14-day blocks) and {b28:.0%} (28-day), {direction} {raw:.0%} with single days",
        f"Share of replayed forecasts in which the project finished at least the forecast amount. {len(table) // len(METHOD_STYLE):,} forecasts per method:\n"
        "26 weeks of history, horizons of 2, 4, 8 and 12 weeks. Block resampling is an experiment in this repository, not what FlowAugur ships.",
        "Figures above the P85 group are its four means. Point = mean of the project rates; bar = middle half of projects.\n"
        "Each project has 34 cutoff dates two weeks apart, so its forecasts overlap and are not independent.",
    )
    handles = [
        Line2D([], [], color=color, marker=marker, markersize=8, markeredgecolor=SURFACE, linewidth=2.2, label=label)
        for label, color, marker in METHOD_STYLE.values()
    ]
    handles.append(Line2D([], [], color=MUTED, marker="o", markersize=4, alpha=0.4, linewidth=0, label="one project"))
    handles.append(Patch(facecolor=RULE, alpha=0.55, label="pass mark for P85: 80–90%"))
    out = fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.036, 0.848), ncols=3, frameon=False, fontsize=9.5, columnspacing=2.0)
    for text in out.get_texts():
        text.set_color(MUTED)
    fig.subplots_adjust(left=0.075, right=0.965, top=0.725, bottom=0.185)
    save(fig, "calibration_3b.png")
    return means


def pit_histogram_3b(table: pd.DataFrame) -> dict:
    """The PIT histogram for all four methods, one panel each."""
    edges = np.linspace(0, 1, 11)
    fig, axes = plt.subplots(1, 4, figsize=(12, 6.3), sharey=True, facecolor=SURFACE)
    shares: dict = {}
    for ax, (method, (label, color, _)) in zip(axes, METHOD_STYLE.items()):
        counts, _ = np.histogram(table.loc[table["method"] == method, "pit"].to_numpy(), bins=edges)
        share = counts / counts.sum()
        shares[method] = share
        ax.bar(edges[:-1] + 0.05, share, width=0.082, color=color, linewidth=0)
        ax.axhline(0.10, color=INK, linewidth=1.0, linestyle=(0, (4, 3)), zorder=3)
        for i in (0, 9):
            ax.annotate(f"{share[i]:.0%}", xy=(edges[i] + 0.05, share[i]), xytext=(0, 4), textcoords="offset points", ha="center", fontsize=10.5, color=INK, fontweight="bold")
        name, _, note = label.partition(" (")
        ax.set_title(name + ("\n(" + note if note else "\n"), fontsize=10.5, color=INK, loc="left", fontweight="bold", pad=8, linespacing=1.25)
        ax.set_xlim(0, 1)
        ax.set_xticks([0, 0.5, 1])
        labels = ax.set_xticklabels(["0%", "50%", "100%"])
        labels[0].set_horizontalalignment("left")
        labels[-1].set_horizontalalignment("right")
        style(ax)
    top = max(float(s.max()) for s in shares.values())
    axes[0].set_ylim(0, top * 1.14)
    axes[0].set_yticks(np.arange(0, top * 1.14, 0.1))
    axes[0].set_yticklabels([f"{v:.0%}" for v in np.arange(0, top * 1.14, 0.1)])
    axes[0].set_ylabel("Share of Forecasts", fontsize=9.5, color=MUTED)
    axes[1].annotate("calibrated: 10% in every bar", xy=(0.5, 0.10), xytext=(0, 4), textcoords="offset points", ha="center", fontsize=8.5, color=MUTED)
    fig.text(0.52, 0.118, "Share of Simulated Outcomes at or Below the Actual", ha="center", fontsize=9.5, color=MUTED)
    outer = {method: float(share[0] + share[9]) for method, share in shares.items()}
    titles(
        fig,
        f"Results in the outer tenths: {outer['raw']:.0%} with single days, {outer['block14']:.0%} with 14-day blocks, {outer['block28']:.0%} with 28-day",
        "Where each actual result fell within its own forecast distribution; a calibrated forecast puts 10% in every bar, so 20% in the two end bars.\n"
        "Same forecasts as the calibration plot. Block resampling is an experiment in this repository, not what FlowAugur ships.",
        "Left bars: the project did less than nearly every simulated outcome; right bars: more. A range that is too narrow piles results into the end bars.",
    )
    fig.subplots_adjust(left=0.075, right=0.965, top=0.735, bottom=0.2, wspace=0.09)
    save(fig, "pit_histogram_3b.png")
    return shares


def where_it_breaks(table: pd.DataFrame) -> pd.DataFrame:
    """Chart #4: per project, how often the pace shifted against how often the forecast expected it."""
    projects = experiment_3b.pace_by_project(table)
    held = projects.drop_duplicates(["instance", "project"]).set_index(["instance", "project"])["p85_shipped"].sort_values()
    per_project = projects.set_index(["instance", "project", "horizon_days"])[["observed_shift", "expected_shift"]]
    per_project = per_project.rename(columns={"observed_shift": "observed", "expected_shift": "expected"})
    diagnostic = experiment_3b.pace_shift(table)
    overall = diagnostic[diagnostic["method"] == "raw"].set_index("horizon_days")[["observed_shift", "expected_shift"]]
    overall = overall.rename(columns={"observed_shift": "observed", "expected_shift": "expected"})
    worst = list(held.index[:3])
    blue = SERIES[True][1]

    fig, axes = plt.subplots(1, 2, figsize=(12, 9.4), sharey=True, facecolor=SURFACE)
    rows = [None, *held.index]  # None = all projects together, drawn first
    ys = [0.0] + [i + 1.6 for i in range(len(held))]
    for ax, horizon_days in zip(axes, (56, 84)):
        ax.axhspan(ys[1] - 0.5, ys[3] + 0.5, color=RULE, alpha=0.2, linewidth=0, zorder=0)
        for key, y in zip(rows, ys):
            point = overall.loc[horizon_days] if key is None else per_project.loc[(*key, horizon_days)]
            strong = key is None or key in worst
            ax.plot(
                [point["expected"], point["observed"]], [y, y], color=INK if strong else MUTED,
                linewidth=2.4 if strong else 1.3, alpha=1 if strong else 0.55, solid_capstyle="round", zorder=2,
            )  # fmt: skip
            size = 95 if key is None else 46
            ax.scatter([point["expected"]], [y], s=size, facecolors=SURFACE, edgecolors=blue, linewidths=1.8, zorder=3)
            ax.scatter([point["observed"]], [y], s=size, color=blue, edgecolors=SURFACE, linewidths=1.0, zorder=4)
            if key is None:
                ax.annotate(f"{point['expected']:.0%}", xy=(point["expected"], y), xytext=(-10, 0), textcoords="offset points", ha="right", va="center", fontsize=10.5, color=INK, fontweight="bold")
                ax.annotate(f"{point['observed']:.0%}", xy=(point["observed"], y), xytext=(10, 0), textcoords="offset points", ha="left", va="center", fontsize=10.5, color=INK, fontweight="bold")
        ax.set_xlim(-0.06, 1.0)
        ax.set_xticks(np.arange(0, 1.01, 0.25))
        ax.set_xticklabels([f"{v:.0%}" for v in np.arange(0, 1.01, 0.25)])
        ax.set_xlabel("Share of Forecasts With a Pace Shift", fontsize=9.5, color=MUTED)
        ax.set_title(f"{horizon_days // 7}-week horizon", fontsize=11.5, color=INK, loc="left", fontweight="bold", pad=10)
        ax.set_facecolor(SURFACE)
        ax.grid(axis="x", color=RULE, linewidth=0.5, alpha=0.5)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(RULE)
        ax.tick_params(colors=MUTED, labelsize=9.5, length=0)
    axes[0].set_ylim(ys[-1] + 0.7, -0.9)
    axes[0].set_yticks(ys)
    labels = axes[0].set_yticklabels(["All 24 projects"] + [f"{instance} {project}" for instance, project in held.index])
    for label, key in zip(labels, rows):
        strong = key is None or key in worst
        label.set_color(INK if strong else MUTED)
        label.set_fontweight("bold" if strong else "normal")
    # Right-hand column: how often each project's 85% forecast held.
    column = axes[1].get_yaxis_transform()
    axes[1].text(1.03, -1.25, "85% forecast\nheld", transform=column, fontsize=9, color=MUTED, va="center", linespacing=1.2)
    axes[1].text(1.03, ys[0], f"{held.mean():.0%}", transform=column, fontsize=10.5, color=INK, fontweight="bold", va="center")
    for key, y in zip(held.index, ys[1:]):
        strong = key in worst
        axes[1].text(1.03, y, f"{held[key]:.0%}", transform=column, fontsize=9.5, color=INK if strong else MUTED, fontweight="bold" if strong else "normal", va="center")

    at12 = overall.loc[84]
    titles(
        fig,
        f"Pace shifted in {at12['observed']:.0%} of 12-week forecasts; resampling the past expected {at12['expected']:.0%}",
        "A pace shift: over the forecast horizon the project finished work at less than 0.8 or more than 1.25 times its rate over the previous 26 weeks.\n"
        "Projects are ordered by how often their 85% forecast held. The three where it held least (shaded) all changed pace far more than expected.",
        "Expected = the same share across each forecast's own 10,000 simulated outcomes, resampling single days of raw history (no outlier drop).\n"
        "34 forecasts per project and horizon, two weeks apart, so they overlap. The 85% column is FlowAugur as shipped, all four horizons.",
        title_y=0.982,
        subtitle_y=0.945,
    )
    handles = [
        Line2D([], [], color=blue, marker="o", markersize=8, markerfacecolor=SURFACE, markeredgewidth=1.8, linewidth=0, label="how often the forecast expected a pace shift"),
        Line2D([], [], color=blue, marker="o", markersize=8, linewidth=0, label="how often one happened"),
    ]
    out = fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.036, 0.903), ncols=2, frameon=False, fontsize=9.5, columnspacing=2.0)
    for text in out.get_texts():
        text.set_color(MUTED)
    fig.subplots_adjust(left=0.165, right=0.905, top=0.83, bottom=0.14, wspace=0.07)
    save(fig, "where_it_breaks.png")
    return per_project


def main() -> int:
    results = pd.read_parquet(HERE / "data" / "results.parquet")
    means = calibration(results)
    print("calibration (app defaults), mean of project hit rates:")
    for drop in (True, False):
        print(f"  {SERIES[drop][0]:<34}" + "  ".join(f"P{level} {means[(drop, level)]:.1%}" for level in LEVELS))
    buckets = bucket_comparison(results)
    print("P85 by bucket, mean of project hit rates:")
    for (history_days, bucket, drop), value in buckets.items():
        print(f"  {history_days:>3}d history  {bucket:<8} {'shipped' if drop else 'raw':<8} {value:.1%}")
    shares = pit_histogram(results)
    for drop in (True, False):
        print(f"PIT {SERIES[drop][0]}: lowest tenth {shares[drop][0]:.1%}, highest tenth {shares[drop][9]:.1%}")

    experiment = HERE / "data" / "experiment_3b.parquet"
    if experiment.exists():
        table = pd.read_parquet(experiment)
        calibration_3b(table)
        pit_histogram_3b(table)
        where_it_breaks(table)
        print("experiment 3b: calibration_3b.png, pit_histogram_3b.png and where_it_breaks.png drawn")
    return 0


if __name__ == "__main__":
    sys.exit(main())

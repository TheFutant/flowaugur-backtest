"""Fan chart: one replayed forecast against what actually happened.

Left panel is the forecast as the app ships it (IQR outlier drop on), right
panel is the raw one. Each shows FlowAugur's four "at least" amounts (P50, P70,
P85, P95) at the end of every week of a 12-week horizon, with the items the
project really completed after the cutoff drawn over them. The amounts at 2,
4, 8 and 12 weeks are the same numbers ``replay.py`` scores.

The cutoff is chosen by rule, not by eye: the middle one of the project's
replay cutoffs. The bucket is the one the app's own cadence detection
(``metrics.detect_cadence``) picks for that history, and the history is the 26
weeks closest to the app's default 180-day lookback.

Usage::

    uv run fan_chart.py --instance Apache --project SPARK
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

import replay  # noqa: E402
from engine.metrics import detect_cadence  # noqa: E402
from engine.simulate import DEFAULT_LEVELS, confidence_amounts, forecast_items_by_date  # noqa: E402

HERE = Path(__file__).resolve().parent
HISTORY_DAYS = 182
HORIZON_DAYS = 84
WEEK_ENDS = np.arange(0, HORIZON_DAYS + 1, 7)  # forecast evaluated at the end of each week
BUCKET_OF = {granularity: name for name, (granularity, _) in replay.BUCKETS.items()}

SURFACE, INK, MUTED, RULE = "#fcfcfb", "#0b0b0b", "#52514e", "#b9b8b2"
# One-hue ordinal ramp, darkest next to the median (validated: dataviz validate_palette --ordinal).
BAND_COLORS = ["#2a78d6", "#5598e7", "#86b6ef"]  # P50-P70, P70-P85, P85-P95


def fan(hist: pd.Series, cutoff: pd.Timestamp, bucket: str, drop: bool) -> dict[float, np.ndarray]:
    """The engine's "at least" amount per level at the end of each week (0 at the cutoff)."""
    _, bucket_days = replay.BUCKETS[bucket]
    samples, _ = replay.prepare(hist, bucket, drop)
    out = {level: np.zeros(len(WEEK_ENDS)) for level in DEFAULT_LEVELS}
    for i, day in enumerate(WEEK_ENDS[1:], start=1):
        totals = forecast_items_by_date(
            samples,
            int(day),
            n_trials=replay.N_TRIALS,
            rng=replay.rng_for(cutoff, bucket_days, HISTORY_DAYS, int(day), drop),
            bucket_days=bucket_days,
        )
        for level, amount in confidence_amounts(totals, lower_is_safer=True).items():
            out[level][i] = amount
    return out


def spread_labels(ys: list[float], gap: float) -> list[float]:
    """Nudge label positions apart so none sits closer than ``gap`` to its neighbour."""
    order = np.argsort(ys)
    placed = np.array(ys, dtype=float)[order]
    for i in range(1, len(placed)):
        placed[i] = max(placed[i], placed[i - 1] + gap)
    shift = (placed - np.array(ys, dtype=float)[order]).mean()  # keep the group centred
    out = np.empty(len(ys))
    out[order] = placed - shift
    return out.tolist()


def draw(instance: str, project: str, cutoff: pd.Timestamp | None, path: Path) -> str:
    daily = replay.load_daily(instance, project)
    cuts = replay.cutoffs(replay.qualifying_window(daily))
    cutoff = cutoff or cuts[len(cuts) // 2]
    hist = replay.history(daily, cutoff, HISTORY_DAYS)
    bucket = BUCKET_OF[detect_cadence(hist).cadence]

    horizon = np.arange(0, HORIZON_DAYS + 1)  # day 0 is the moment the forecast is made
    span = pd.date_range(cutoff, cutoff + (HORIZON_DAYS - 1) * replay.DAY, freq="D")
    cumulative = np.concatenate([[0], daily.reindex(span, fill_value=0).cumsum().to_numpy()])
    done = replay.actual(daily, cutoff, HORIZON_DAYS)

    panels = [("As shipped: IQR outlier drop on", True), ("Raw: no outlier drop", False)]
    fans = {drop: fan(hist, cutoff, bucket, drop) for _, drop in panels}
    floors = {drop: {lv: int(np.floor(f[lv][-1])) for lv in DEFAULT_LEVELS} for drop, f in fans.items()}

    fig, axes = plt.subplots(1, 2, figsize=(12, 6.3), sharey=True, facecolor=SURFACE)
    top = max(done, max(f[0.5][-1] for f in fans.values()))
    for ax, (label, drop) in zip(axes, panels):
        f = fans[drop]
        edges = [f[level] for level in DEFAULT_LEVELS]  # P50 (highest) .. P95 (lowest)
        for upper, lower, color in zip(edges, edges[1:], BAND_COLORS):
            ax.fill_between(WEEK_ENDS, lower, upper, color=color, linewidth=0)
        for edge in edges[1:-1]:  # a hairline of surface between neighbouring bands
            ax.plot(WEEK_ENDS, edge, color=SURFACE, linewidth=0.8)
        ax.plot(
            horizon, cumulative, color=INK, linewidth=2, solid_capstyle="round",
            path_effects=[pe.Stroke(linewidth=4.5, foreground=SURFACE), pe.Normal()],
        )  # fmt: skip

        names = [f"P{round(level * 100)}  ≥ {floors[drop][level]:,}" for level in DEFAULT_LEVELS] + [f"Actual  {done:,}"]
        ys = [float(e[-1]) for e in edges] + [float(done)]
        for name, y, y_text in zip(names, ys, spread_labels(ys, gap=0.052 * top)):
            actual_label = name.startswith("Actual")
            ax.annotate(
                name, xy=(HORIZON_DAYS, y), xytext=(HORIZON_DAYS + 3.5, y_text), va="center", ha="left",
                fontsize=9.5, color=INK if actual_label else MUTED, fontweight="bold" if actual_label else "normal",
                arrowprops={"arrowstyle": "-", "color": RULE, "linewidth": 0.7, "shrinkA": 0, "shrinkB": 2},
                annotation_clip=False,
            )  # fmt: skip
        held = "held" if done >= f[0.85][-1] else "missed"
        ax.set_title(label, fontsize=11.5, color=INK, loc="left", fontweight="bold", pad=22)
        ax.text(
            0, 1.022, f"P85 said at least {floors[drop][0.85]:,} in 12 weeks; {done:,} were done, so it {held}",
            transform=ax.transAxes, fontsize=9.5, color=MUTED, va="bottom",
        )  # fmt: skip
        ax.set_xlim(0, HORIZON_DAYS)
        ax.set_ylim(0, top * 1.05)
        ax.set_xticks(np.arange(0, HORIZON_DAYS + 1, 14))
        ax.set_xticklabels([str(d // 7) for d in range(0, HORIZON_DAYS + 1, 14)])
        ax.set_xlabel("Weeks after the forecast date", fontsize=9.5, color=MUTED)
        ax.set_facecolor(SURFACE)
        ax.grid(axis="y", color=RULE, linewidth=0.5, alpha=0.5)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(RULE)
        ax.tick_params(colors=MUTED, labelsize=9, length=0)
    axes[0].set_ylabel("Items Done", fontsize=9.5, color=MUTED)

    shipped = floors[True][0.85]
    verdict = "held" if done >= fans[True][0.85][-1] else "missed"
    title = (
        f"{instance} {project} finished {done:,} items in 12 weeks; "
        f"FlowAugur's 85% forecast said at least {shipped:,}, and it {verdict}"
    )
    fig.suptitle(title, x=0.045, y=0.975, ha="left", fontsize=14, color=INK, fontweight="bold")
    fig.text(
        0.045, 0.918,
        f"One forecast replayed as of {cutoff.day} {cutoff:%B %Y} from the 26 weeks before it "
        f"({bucket} buckets, {replay.N_TRIALS:,} trials, calendar days), then compared with what happened.",
        ha="left", va="top", fontsize=10, color=MUTED,
    )  # fmt: skip
    handles = [Line2D([], [], color=INK, linewidth=2, label="Items actually done since the forecast")] + [
        Patch(facecolor=color, label=text)
        for color, text in zip(BAND_COLORS, ["Forecast: P50 to P70", "P70 to P85", "P85 to P95"])
    ]
    legend = fig.legend(
        handles=handles, loc="upper left", bbox_to_anchor=(0.036, 0.878), ncols=4, frameon=False, fontsize=9.5,
        handlelength=1.6, columnspacing=1.8,
    )  # fmt: skip
    for text in legend.get_texts():
        text.set_color(MUTED)
    fig.text(
        0.045, 0.022,
        "“P85 ≥ N” = FlowAugur was 85% confident of at least N items by then. "
        f"Cutoff chosen by rule: the middle one of {len(cuts)} replay cutoffs. "
        "Counts Story, Task, Bug and Improvement.\n"
        "Source: The Public Jira Dataset (Montgomery, Lüders & Maalej), CC BY 4.0.",
        ha="left", va="bottom", fontsize=8.5, color=MUTED, linespacing=1.4,
    )  # fmt: skip
    fig.subplots_adjust(left=0.07, right=0.885, top=0.735, bottom=0.165, wspace=0.36)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)

    lines = [f"{instance} {project}, cutoff {cutoff.date()}, {bucket} buckets, {HISTORY_DAYS}-day history: actual {done}"]
    for label, drop in panels:
        row = "  ".join(f"P{round(lv * 100)} >= {floors[drop][lv]}" for lv in DEFAULT_LEVELS)
        lines.append(f"  {label:<32} {row}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--instance", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--cutoff", type=pd.Timestamp, help="YYYY-MM-DD; default is the middle replay cutoff")
    args = ap.parse_args(argv)
    path = HERE / "figures" / f"fan_{args.instance}_{args.project}.png".lower()
    print(draw(args.instance, args.project, args.cutoff, path))
    print(f"-> {path.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

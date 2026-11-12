# Handoff — FlowAugur backtest

Last updated 2026-11-12, end of Session 2 ("engine and one replay").

**Where it stands:** one project (Apache SPARK) runs end to end through
FlowAugur's own engine and is scored; the fan chart exists. Session 3 ("full
backtest") is the same replay over all 24 projects, and needs one decision
confirmed first (see "To confirm before Session 3").

- Plan: <https://claude.ai/artifact/B1G2BVECSviHTERyNtj1d3> (a Claude Doc — read
  it with the Claude Docs tools; a web fetch will not work). Its "Decisions
  locked" table is the spec.
- Schema, done-date rule, data caveats and commands: [README.md](README.md)

## Where the work is

This repository, `TheFutant/flowaugur-backtest` (private until the write-up is
published). It was split out of MonteJiraSim on 2026-11-12; MonteJiraSim keeps
only a pointer in `backtest/README.md`.

The two large Parquet files (`done_dates.parquet`, `status_transitions.parquet`)
are git-ignored and exist only in `data/` of the maintainer's local clone. The
replay does not need them; it runs from `daily_throughput.parquet`, which is
committed.

## How the replay works

`replay.py` calls the app's engine and nothing else; no forecasting logic is
re-implemented:

```
daily history → metrics.remove_outliers_iqr (optional)
              → metrics.bucket_daily_series
              → simulate.forecast_items_by_date (10,000 trials)
              → simulate.confidence_amounts(lower_is_safer=True)
```

How each locked decision is implemented:

| Decision | In the code |
|---|---|
| Done date: first transition into Done | `daily_throughput.parquet` is built from `done_date` |
| Keep all resolutions; drop Mojang | Nothing filtered by resolution; Mojang is simply not in the project list |
| Outlier removal: both | Every forecast is run twice, `outlier_drop` true and false |
| Calendar days for all bucket sizes | History is every calendar day, zero-filled; the app's weekday default is not used |
| Variants | Bucket day / week / 2 weeks × history 84 / 182 days × horizon 14 / 28 / 56 / 84 days |
| Hit rule | A band holds when actual ≥ its amount, as in `reconcile.reconcile_bydate` |

For a cutoff day *T*: history is the days *T−W … T−1*, the horizon is
*T … T+H−1*. Nothing on or after *T* reaches the forecast (tested).

The outlier drop is applied the way the app applies it: to the *daily* series,
before bucketing, so a dropped day simply vanishes from its week.

## To confirm before Session 3

The plan marks the cutoff range as "assumed default, not yet confirmed". To run
SPARK I had to make it precise. These three choices were made before looking at
any SPARK result, but they are mine, not the plan's:

1. **Which window, when several qualify.** The window is the 104 consecutive
   Monday-start weeks with the highest share of weeks at 10+ done; ties go to
   the **most recent**. SPARK has many 100% windows, so this picks 2020–2021.
   "Earliest" would pick a different era for most projects.
2. **History stays inside the window too.** The first cutoff is 26 weeks after
   the window starts, the last is 12 weeks before it ends, so every variant is
   scored on the same **34 cutoffs** per project. Letting history reach back
   before the window would give 47.
3. **The last observed week of a project is ignored** when finding the window,
   because the download stopped part-way through it.

All three live in `qualifying_window` and `cutoffs` in `replay.py`. Changing
any of them after seeing results is the forking-paths problem the plan is
trying to avoid, so confirm or change them first.

The plan's project list reproduces exactly from `data/project_overview.csv`
with the locked filter (24 projects, 10 instances), and all 24 have a
qualifying window under these rules. Their windows range from 2010–2012
(Sakai SAK, Qt QTCREATORBUG, RedHat JBIDE) to 2020–2021 (SPARK, FLINK, DAOS,
MongoDB SERVER, QTBUG).

## Session 2 result: Apache SPARK

Window 2020-01-06 to 2022-01-02; 34 cutoffs from 2020-07-06 to 2021-10-11;
1,632 scored forecasts; about 4 seconds to run.

Share of forecasts whose band held:

| | Forecasts | P50 | P70 | P85 | P95 |
|---|---:|---:|---:|---:|---:|
| As shipped (IQR outlier drop on) | 816 | 56% | 71% | 81% | 86% |
| Raw (no outlier drop) | 816 | 48% | 63% | 77% | 84% |

Read this as a pipeline check, not a finding. It is one project, and 34
cutoffs two weeks apart with horizons up to 12 weeks overlap heavily, so there
are far fewer independent observations than 816.

What it does show, to check against all 24 projects in Session 3:

- **The forecast distribution looks too narrow.** The actual landed above 95%
  of simulated totals in 22% of raw forecasts (30% as shipped), and below 5%
  in 16% (14%); each should be about 5%. That is why P95 holds only about 85%
  of the time while P50 is close to 50%.
- **Bucket size made almost no difference for SPARK.** Raw P85 coverage is 76%
  (day), 75% (week), 78% (2 weeks).
- **One bulk week drives a lot.** The week of 24 May 2021 has 593 done items
  against a median of 35. It sits in the history of 10 cutoffs and the horizon
  of 6. The outlier drop removes it from history, which is most of why "as
  shipped" scores better (mean CRPS 70 against 86).

The fan chart (`figures/fan_apache_spark.png`) shows one forecast: cutoff
1 March 2021, day buckets, 26 weeks of history. SPARK finished 480 items in the
12 weeks; P85 said at least 385 as shipped and 398 raw, so both held. The
actual was above 99% (as shipped) and 95% (raw) of simulated totals, so the
chart also shows the narrowness: the line runs well above the whole fan.

How the chart's inputs were chosen, by rule rather than by eye:

- Cutoff: the middle one of the 34.
- Bucket: what the app's own `metrics.detect_cadence` picks for that history.
- History: 26 weeks, the closest variant to the app's 180-day default lookback.
- The band edges at 2, 4, 8 and 12 weeks are the same numbers `replay.py`
  scores (same random seeds).

The chart shows only FlowAugur's four "at least" amounts, which is what the
plan asks for and what the app displays. It does not show the upper half of
the distribution.

## What was checked

- Actual counts match a count taken straight from `done_dates.parquet` for all
  136 cutoff × horizon pairs.
- 60 sampled forecasts were rebuilt from the per-issue data with direct engine
  calls, without `replay.py`'s helpers, and match exactly.
- Poisoning every day on or after a cutoff leaves the forecast inputs unchanged.
- `tests/test_backtest_replay.py`: 16 tests covering the window, the cutoffs,
  the history/horizon split, the app-faithful prep, CRPS and an exact
  constant-throughput replay.
- CI gate from the lockfile: pyright clean, 819 tests at 100% coverage, ruff
  and bandit clean. (`pip-audit` was failing on `urllib3` 2.7.0 in Session 1,
  unrelated to this work; not re-run.)
- The replay's inline dependency pins are the app's locked numpy, pandas and
  pyarrow versions.

Not checked: the extract scripts from Session 1 still have no unit tests.

## Session 3: full backtest

Goal from the plan: all projects × cutoffs × variants in one results table,
then the calibration plot (#1) and the bucket-size comparison (#3).

- `replay.replay(daily, instance, project)` already returns the results table
  for one project. Loop it over the 24 projects and concatenate; expect about
  39,000 rows and a couple of minutes.
- The project list is in the plan. It can be derived instead of typed:
  `best_2y_share_weeks_ge10 >= 0.9`, drop Mojang, top 5 per instance by `done`.
- The calibration plot wants one point per confidence level with a whisker
  across projects, so compute coverage per project first, then summarise.
- For the bucket comparison the plan splits "batchy" from "steady" projects.
  That split is not defined yet; `metrics.detect_cadence` is the app's own
  definition and would avoid inventing one.
- `naive` is in the table (history average per day × horizon). It is a point
  forecast, so compare it on absolute error or CRPS, not on coverage.
- `pit` is in the table. A histogram of it across all projects is the direct
  test of "too narrow".
- 12 weeks of history in 2-week buckets is 6 samples; the app warns below 8
  (`LOW_SAMPLE_BUCKETS`). Say so next to that variant.

## Data gotchas

Full list in the README. The ones most likely to bite:

- The data ends in January 2022 (MariaDB January 2021, Mindville April 2021).
  Five of the 24 windows end on 2 January 2022, days before the download.
- Bulk events are in the data. Besides SPARK's 593-item week, the selected
  windows include weeks of 488 (MongoDB DOCS), 420 (RedHat KEYCLOAK), 400
  (Apache FLINK) and 301 (Jira CONFSERVER) items.
- Three quarters of counted items are Bugs; Story is 30,000 of 1.84 million.
- Converting `created` to nanosecond timestamps overflows (290 issues are
  dated before 2000, some in year 0012). `done_date` has no such values.
- Qt treats "Need More Info" as a Done status, and Spring categorises its
  "Done" status as in progress. The extract follows Jira's categories as they
  are; both are candidates for the "where it breaks" section.

## CI gate

`backtest/` is omitted from coverage and excluded from pyright in
`pyproject.toml`, the way `scripts/` is omitted from coverage. Its scripts run
under `uv run` with their own inline dependencies, which are deliberately not
in the app's lockfiles. Ruff and bandit still cover the folder, and
`tests/test_backtest_replay.py` runs in CI like any other test.

## Environment

- Every script carries its dependencies inline: `uv run backtest/<script>.py`.
- The 5.8 GB ZIP was downloaded to a session scratch directory under `/tmp`
  and should be assumed gone. It is only needed to re-run the extract from
  scratch.
- A single connection to Zenodo runs at about 1 MB/s (roughly 90 minutes).
  Zenodo honours HTTP range requests, and 8 parallel ranges took 10 minutes.

## Not done

- The plan doc is unchanged by this session: "Session 2" is not ticked, and the
  cutoff-range row still says "not yet confirmed".
- Open from the plan: whether the backtest moves to its own public repo. This
  repo is private, so a public write-up cannot link to the code as it stands.

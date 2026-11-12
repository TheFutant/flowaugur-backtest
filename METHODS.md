# Methods

How the FlowAugur backtest was built, what was decided and when, what was
checked, and what to keep in mind about the data. The front page is
[README.md](README.md).

## The question

When a throughput Monte Carlo forecast says "85% confident of at least N
items", does the team finish at least N about 85% of the time? Each forecast
here is rebuilt as if it had been made on a past date, from the data before
that date only, and scored against what the project then completed.

## Data

**Source.** The Public Jira Dataset (Montgomery, Lüders and Maalej, University
of Hamburg): 16 public Jira instances downloaded through the Jira API in
January 2022, with personal names replaced by UUIDs. Zenodo record
[15719919](https://zenodo.org/records/15719919), published 2025-06-23; the ZIP
is 5,813,135,238 bytes with md5 `02f85309d966092ea130ca0797aea795`.

**Extraction.** `extract_done_dates.py` reads the `mongodump` archive inside
the ZIP directly, with no MongoDB restore: ZIP member → gzip → archive framing
→ BSON documents. It finds 2,686,223 issues in 1,276 projects after dropping
59 identical duplicate copies, and 5,676,383 status changes. Per-instance issue
counts match the dataset's published figures. The dataset's headline of 1,822
projects also counts projects that issues were moved out of.

**Done date.** The first changelog transition that takes an issue into a
Done-category status from outside that category; if the changelog shows none,
the resolution date. Of 2,329,617 issues with a done date, 97.7% come from the
changelog, 2.2% from the resolution date because the changelog has no entry
into Done, and 0.1% from the resolution date because there is no changelog.

- *Moves between Done statuses are not entries.* About 32,000 issues first
  appear in a Done status through a move such as Resolved → Closed, because
  the changelog has lost the original resolve; the move is typically a bulk
  migration years later (median gap 3.9 years). Counting it would date 3,917
  Confluence Cloud issues to 22 August 2019. Those issues use the resolution
  date.
- *Status categories are inferred.* Changelogs record status names and ids,
  not categories. A status takes the category of the issues currently in it
  (97.6% of status changes). For statuses no issue holds any more: the same
  name in the same instance, then Jira's built-in ids (5 Resolved and 6 Closed
  are Done), then "at least 80% of at least 100 transitions into it set a
  resolution". The remaining 2.0% of status changes go to statuses of unknown
  category, which never count as Done. `data/status_categories.csv` lists
  every status and how it was categorised.

**What is counted.** Issues of type Story, Task, Bug or Improvement that are
not sub-tasks: 1,835,779 done items in 1,227 projects. Every resolution is
kept, including Duplicate and Invalid, because that is how FlowAugur counts
completed work. `data/daily_throughput.parquet` holds the counts per instance,
project, issue type and UTC day; everything after the extract runs from it.

**Projects.** For each project, find the 104 consecutive Monday-start weeks
with the highest share of weeks at 10 or more done items. A project qualifies
at 90%. Within each Jira instance the five largest qualifying projects by done
items are taken. Mojang is left out: it is a public bug-report queue (47% of
its closures are Duplicate, 22% Invalid, 5% Fixed). That leaves 24 projects in
10 instances, listed with their windows in `data/frozen_windows.csv`.

## The replay

`replay.py` rebuilds a forecast at each cutoff with FlowAugur's own functions
and nothing re-implemented:

```
daily history → remove_outliers_iqr (optional)
              → bucket_daily_series
              → forecast_items_by_date (10,000 trials)
              → confidence_amounts(lower_is_safer=True)
```

- **History and horizon.** For a cutoff day *T*, history is the days
  *T−W … T−1*, every calendar day, zero-filled. The horizon is *T … T+H−1*.
  Nothing on or after *T* reaches the forecast.
- **Cutoffs.** Every two weeks inside the project's qualifying window. If
  several windows tie for the best share, the most recent is used. The longest
  history (26 weeks) and the longest horizon (12 weeks) both have to fit inside
  the window, which gives every project the same 34 cutoffs for every variant.
  A project's last observed week is ignored when finding the window, because
  the download stopped part-way through it.
- **Variants.** Bucket size (day, week, 2 weeks) × history (12, 26 weeks) ×
  horizon (2, 4, 8, 12 weeks) × outlier drop (on, off): 48 forecasts per
  cutoff, 39,168 in all.
- **The app's defaults.** For these projects FlowAugur's cadence detection
  picks day buckets at every cutoff, its default lookback is 180 days, and its
  outlier drop is on. "As shipped" means day buckets, 26 weeks of history,
  outlier drop on; "raw" is the same without the outlier drop. One difference
  from the app is deliberate: the app counts weekdays only for day buckets by
  default, and this backtest uses calendar days for every bucket size so the
  bucket sizes can be compared like for like.
- **The outlier drop** is applied as the app applies it: to the daily series,
  before bucketing, so a dropped day vanishes from its week.

**Scoring** (`replay.score`, used for every method):

- A band *holds* when the actual count is at least the band's amount. This is
  the rule FlowAugur's own accuracy tracking uses.
- *PIT* is the share of simulated totals at or below the actual. The *outer
  tenths* are PIT below 0.1 and PIT of 0.9 or more.
- *CRPS* is the continuous ranked probability score of the simulated totals;
  lower is better. For a point forecast it equals the absolute error.
- *Naive* is the history's average per day times the horizon.

**Charts** (`charts.py`). A project's hit rate is the share of its forecasts
whose band held. Each point is the mean of the 24 project rates; every project
has the same number of forecasts, so this is also the pooled rate. Each bar
spans the interquartile range across projects, and each faint dot is one
project. The calibration plot and the PIT histogram use the app's defaults,
all four horizons together.

## The engine snapshot

FlowAugur's application code is in a private repository, MonteJiraSim.
`engine/simulate.py` and `engine/metrics.py` are a verbatim copy of the 13
definitions this backtest calls, taken from that repository at commit
`ae17e093878c68057ee8f5f3e7e54d8fc325305a`.

- `uv run python engine/snapshot.py` checks every definition against the
  hashes pinned in `engine/SNAPSHOT.json`. Anyone can run it, and the test
  suite does.
- `uv run python engine/snapshot.py --source <checkout>` rebuilds the vendored
  files from that commit and compares them byte for byte. It needs a checkout
  of the private repository.

So anyone can read the forecasting code and rerun every number here. That the
code matches what the app ships rests on the author's word.

## Experiment 3b: block resampling

Experiment code, in `experiment_3b.py`. It is not FlowAugur's engine and is
not in the app.

- **Question.** Does resampling multi-week runs of real days, instead of
  single days, fix the too-narrow range?
- **Variants.** A moving-block bootstrap on the daily history with 14-day and
  28-day blocks. Start days are drawn uniformly from every position where a
  full block fits in the history; blocks are joined end to end and the last is
  cut to the horizon.
- **Fixed settings.** Raw history, calendar days, 26 weeks of history, the
  four horizons, 10,000 trials, the same windows and cutoffs.
- **Pass test, all three:** P85 holds 80–90% of the time; at most 30% of
  actuals fall in the outer tenths; mean CRPS is at most 64.
- **Pace-shift diagnostic, descriptive only.** The pace ratio is a forecast's
  horizon daily rate divided by its history's daily rate. A shift is a ratio
  below 0.8 or above 1.25. *Observed* is the share of forecasts that shifted.
  *Expected* is the same share across each forecast's own 10,000 simulated
  totals, averaged over forecasts. *Excess* is observed minus expected.

## Results in full

All figures are shares of forecasts whose band held, unless stated.

**At the app's defaults** (6,528 forecasts):

| | P50 | P70 | P85 | P95 |
|---|---:|---:|---:|---:|
| As shipped (outlier drop on) | 56% | 65% | 73% | 82% |
| Raw (no outlier drop) | 43% | 55% | 66% | 77% |

As shipped, P85 ranges across projects from 44% (Apache AMBARI) to 94% (Spring
SPR); the middle half of projects sits between 66% and 80%. Five projects are
inside the 80–90% pass mark, one is above it and 18 are below.
`data/project_summary.csv` has every project.

**By horizon**, P85: 78%, 74%, 72% and 70% at 2, 4, 8 and 12 weeks as shipped;
73%, 68%, 64% and 59% raw.

**By bucket size**, P85, all horizons:

| | Day | Week | 2 weeks |
|---|---:|---:|---:|
| As shipped, 26 weeks of history | 73% | 76% | 79% |
| Raw, 26 weeks of history | 66% | 68% | 72% |
| As shipped, 12 weeks of history | 76% | 77% | 79% |
| Raw, 12 weeks of history | 69% | 70% | 73% |

Twelve weeks of history in 2-week buckets is 6 samples; the app itself warns
below 8. Every variant pooled, P85 held 77% as shipped and 70% raw.

**Range.** At the app's defaults, as shipped, 56% of actuals fell in the outer
tenths of their forecast (23% bottom, 33% top); raw, 50% (29% bottom, 22% top).
A calibrated forecast gives 20%.

**The outlier drop** lowers the forecast: the mean simulated total is a median
8% below the raw one. A lower forecast is easier to beat, so coverage rises
from 66% to 73%, while mean CRPS barely moves (63.9 against 65.0). The raw
forecast runs a median 8% above the actual and the as-shipped one 3% below.

**Against the naive forecast.** The average-rate point forecast misses by a
mean of 84 items; the Monte Carlo forecast's mean CRPS is about 64.

**Experiment 3b** (3,264 forecasts per method):

| Method | P85 | Outer tenths | Mean CRPS | P85 | Tails | CRPS | Passes |
|---|---:|---:|---:|---|---|---|---|
| Day buckets, as shipped | 73.4% | 56.0% | 63.9 | fail | fail | pass | no |
| Day buckets, raw | 66.0% | 50.4% | 65.0 | fail | fail | fail | no |
| 14-day blocks, raw (experiment) | 70.9% | 43.4% | 63.9 | fail | fail | pass | no |
| 28-day blocks, raw (experiment) | 72.4% | 42.2% | 64.6 | fail | fail | fail | no |

Neither block length passes. Against raw day buckets, blocks close a third to
a half of the gap to the pass marks for coverage and for the tails. They widen
the range a little (the gap between the P50 and P95 amounts is a median 27% of
the forecast, against 24%) and do not change its level.

**Pace shifts** (816 forecasts per horizon; excess in percentage points):

| Horizon | Observed | Expected: day buckets | Excess | Expected: 14-day blocks | Excess | Expected: 28-day blocks | Excess |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2 weeks | 57.0% | 40.5% | +16.5 | 49.1% | +7.8 | 48.8% | +8.1 |
| 4 weeks | 52.2% | 26.2% | +26.0 | 35.0% | +17.2 | 40.5% | +11.7 |
| 8 weeks | 46.1% | 14.3% | +31.8 | 21.7% | +24.4 | 25.3% | +20.8 |
| 12 weeks | 46.9% | 9.2% | +37.7 | 15.0% | +31.9 | 18.2% | +28.8 |

Over 12 weeks the pace shifted in 47% of forecasts; single-day resampling
expected 9%. Blocks cut that excess by 15% (14-day) and 24% (28-day). At 2
weeks chance swings dominate, so the 8- and 12-week rows are the ones to read.
The pace ratio's median is 0.93: these projects ran about 7% slower over the
horizon than over the 26 weeks before it.

**Per project** (`data/experiment_3b_pace_by_project.csv`, added after the
results were known, for the "where it breaks" chart). At 12 weeks the pace
shifted more often than single-day resampling expected in 23 of the 24
projects. Across projects, the excess correlates at −0.84 with how often the
85% forecast held. The three projects where it held least are Apache AMBARI
(a shift in 68% of 12-week forecasts, 1% expected), Red Hat JBEAP (74%, 5%)
and Qt QTCREATORBUG (74%, 5%).

## Decisions log

All of the work was done on 12 November 2026. Decisions were written down in a
planning document before the run they apply to, in this order:

| # | Decision | Made |
|---|---|---|
| 1 | Done date is the first transition into Done; resolution date as the fallback | Before any forecast was run |
| 2 | Count Story, Task, Bug and Improvement; no sub-tasks or Epics | Before any forecast was run |
| 3 | Project filter: best 2-year window with 10 or more done in at least 90% of weeks, five largest per instance | After looking at weekly throughput, before any forecast was run |
| 4 | Keep every resolution; exclude Mojang as a bug-triage queue | Before any forecast was run |
| 5 | Run with and without the app's outlier drop and report both | Before any forecast was run |
| 6 | Calendar days for every bucket size | Before any forecast was run |
| 7 | P85 pass mark of 80–90% | Before any forecast was run |
| 8 | Cutoff rules: best window with ties to the most recent, history inside the window, last observed week ignored | When the first project (Apache SPARK) was replayed, before its results were looked at; confirmed before the full run |
| 9 | Windows and cutoffs for the 24 projects frozen | Before the full run; the run stops if the code computes anything different |
| 10 | Calibration plot and PIT histogram use the app's defaults; point, bar and dot definitions | Before the full run |
| 11 | Bucket-size comparison drawn for all 24 projects with no batchy/steady split | Before the full run, on finding that FlowAugur's cadence detection labels every project steady |
| 12 | Experiment 3b: variants, settings and pass test | After the full run, before any 3b result |
| 13 | Pace-shift diagnostic changed to observed against expected | After the first 3b run. Its first version, the share of outer-tenth misses that came with a shift, was circular and was dropped. Nothing else in 3b changed |
| 14 | "Where it breaks" chart and the per-project pace table | After all results were known; descriptive |

**What the commit history shows, and what it does not.** The replay rules
(windows, cutoffs, variants, scoring) and the data were committed before the
full results. The frozen windows table, the chart definitions, the pass mark
and experiment 3b's pass test first appear in this repository in the same
commits as the results they apply to, so the commit order alone does not show
that they came first; that rests on the planning document and on the author.
The first version of the pace-shift diagnostic and its figures are in commit
`766abce`.

## What was checked

- The download's md5 matches Zenodo's.
- Done dates agree with a slow reference implementation on 301 sampled issues
  from four instances (0 mismatches). Re-deriving them from the saved status
  changes reproduces the extract exactly.
- The engine copy matches its source commit byte for byte.
- For Apache SPARK, actual counts match a count taken straight from the
  per-issue extract for all 136 cutoff and horizon pairs, and 60 sampled
  forecasts rebuilt with direct engine calls match exactly.
- Poisoning every day on or after a cutoff leaves the forecast inputs
  unchanged.
- The results table has 39,168 rows, 1,632 per project, no nulls, and the four
  amounts are ordered in every row.
- Before each full run, every project's window and first and last cutoff are
  recomputed and compared with `data/frozen_windows.csv`; the run stops on any
  difference. All 24 match.
- Experiment 3b regenerates the raw day-bucket forecasts and stops unless they
  are identical to the rows in `data/results.parquet`. Moving the scoring into
  one shared function left the results table unchanged.
- 46 tests cover the replay wiring, the gate, the summaries, the engine
  snapshot, the block sampler, the pass test and the diagnostic.

Not checked:

- The extract scripts have no unit tests, and the reference check on done
  dates covered four of the 16 instances.
- The random streams for the main replay are keyed by cutoff date and variant,
  not by project, so the five projects that share cutoff dates used the same
  draws. Each forecast is still a valid 10,000-trial sample. Experiment 3b
  keys its streams by project as well.
- The charts were checked by eye.
- There is no continuous integration on this repository.

## Limits and data caveats

- **Steady projects only.** FlowAugur's own cadence detection labels all 24
  projects as continuous flow, so nothing here says how the forecast does for
  teams that close work in batches, or whether wider buckets help them.
- **Open-source projects.** Volunteer and vendor teams on public trackers are
  not the same as a company team.
- **Mostly bugs.** Three quarters of counted items are Bugs (1.36 of 1.84
  million); Story is 30,000.
- **"Done" includes closures that are not delivery.** Among counted done
  items 41% are Fixed and 15% Done, but 15% are Duplicate and 7% Invalid.
- **Overlapping forecasts.** A project's 34 cutoffs are two weeks apart with
  horizons up to 12 weeks, so its forecasts are not independent.
- **2020–2021.** Seven of the 24 two-year windows fall in 2020–2021, five of
  them on the same dates, so those years carry extra weight.
- **Throughput only.** The forecast is of items completed. It does not model
  scope added after the forecast, which is a main way real plans miss.
- **Bulk events.** Imports and mass closes put hundreds of items in one week:
  593 in one Apache SPARK week against a median of 35, and similar weeks in
  MongoDB DOCS, Red Hat KEYCLOAK, Apache FLINK and Jira CONFSERVER.
- **First done, not last.** 9.1% of done issues were reopened. Jira's
  resolution date is the last resolve; it is more than a day later than the
  done date used here for 7.0% of issues.
- **Jira's own categories.** Qt counts "Need More Info" as a Done status, and
  Spring puts its "Done" status in the in-progress category. The extract
  follows the categories as they are.
- **The data ends in January 2022** (MariaDB in January 2021). Five of the 24
  windows end on 2 January 2022, days before the download.
- **A few impossible dates are kept.** 290 issues were created before 2000
  (some in year 0012), and 24 are done before they were created. Converting
  `created` to nanosecond timestamps overflows.

## Files

| File | Rows | What |
|---|---:|---|
| `data/daily_throughput.parquet` | 603,503 | Done items per instance, project, issue type and UTC day |
| `data/frozen_windows.csv` | 24 | The projects with their window and first and last cutoff |
| `data/results.parquet` | 39,168 | Every scored forecast of the main replay |
| `data/project_summary.csv` | 24 | Per project: window, detected cadence, how often each band held |
| `data/experiment_3b.parquet` | 13,056 | Experiment 3b: four methods, scored, with pace ratio and expected shift share |
| `data/experiment_3b_summary.csv` | 4 | Each method against the pass test |
| `data/experiment_3b_pace.csv` | 12 | Observed, expected and excess shift share by horizon and method |
| `data/experiment_3b_pace_by_project.csv` | 96 | The same per project, for raw day buckets |
| `data/experiment_3b_pace_distribution.csv` | 5 | The pace ratio's quantiles |
| `data/project_overview.csv` | 1,227 | Every project's span and steadiness, used to choose the filter |
| `data/status_categories.csv` | 756 | The status-to-category map and how each was inferred |
| `data/instance_summary.csv` | 16 | Counts and date span per Jira instance |
| `data/done_dates.parquet` | 2,686,223 | One row per issue (66 MB, not in the repository; rebuilt by the extract) |
| `data/status_transitions.parquet` | 5,676,383 | Every status change (49 MB, not in the repository) |

`results.parquet` has one row per project, cutoff, bucket, history, horizon
and outlier setting:

| Column | Meaning |
|---|---|
| `p50`, `p70`, `p85`, `p95` | The engine's "at least this many items" amount at each confidence level |
| `hit_p50` … `hit_p95` | Whether the actual met that amount |
| `actual` | Items done in the horizon |
| `mean`, `naive` | Mean of the simulated totals; history average per day × horizon |
| `pit`, `crps` | As defined under Scoring |
| `n_samples`, `days_dropped` | Buckets fed to the engine; days removed by the outlier drop |

`done_dates.parquet`, if you rebuild it:

| Column | Meaning |
|---|---|
| `instance`, `project`, `key`, `issue_id` | Jira instance, project key, issue key, numeric id |
| `issue_type`, `is_subtask` | Issue type name and Jira's sub-task flag |
| `status`, `status_id`, `status_category` | Status at download time; category is `new`, `indeterminate` or `done` |
| `resolution` | Resolution name, null if unresolved |
| `created`, `resolutiondate` | From Jira, converted to UTC |
| `first_done`, `last_done`, `done_entries` | First and last entry into Done from outside it; number of entries (more than 1 means reopened) |
| `done_date`, `done_source` | The done date used here, and whether it came from the changelog or the resolution date |
| `n_histories`, `n_status_changes`, `changelog_truncated` | Changelog size; truncated means Jira returned fewer entries than it reported |

Figures: `calibration.png`, `fan_apache_spark.png`, `bucket_comparison.png`,
`where_it_breaks.png`, `pit_histogram.png`, `calibration_3b.png`,
`pit_histogram_3b.png`, and `weekly_throughput.png` (weekly throughput for 119
candidate projects, used to choose the filter).

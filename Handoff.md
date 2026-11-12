# Handoff — FlowAugur backtest

Last updated 2026-11-12, end of Session 3b ("block resampling").

**Where it stands:** the backtest has its own repository and has been run in
full: 24 projects, 39,168 scored forecasts, one results table, and the
calibration plot, bucket-size comparison and PIT histogram. The headline is
that FlowAugur's 85% forecasts held 73% of the time, below the 80–90% pass
mark. Experiment 3b then tested whether block resampling fixes the too-narrow
range: it helps, and neither block length passes the test declared for it.
Session 4 is the write-up and making this repository public.

- Plan: <https://claude.ai/artifact/B1G2BVECSviHTERyNtj1d3> (a Claude Doc — read
  it with the Claude Docs tools; a web fetch will not work). Its "Decisions
  locked" table is the spec.
- What the repository is, how to run it, schema and data caveats:
  [README.md](README.md)

## Where the work is

This repository, `TheFutant/flowaugur-backtest`, private until Session 4. It
was split out of MonteJiraSim on 2026-11-12 with fresh history; MonteJiraSim
keeps only a pointer in `backtest/README.md`. Everything is on `main` and
pushed.

- Commits here use the maintainer's GitHub noreply address (set in this
  clone's git config), because the history will become public.
- The two large Parquet files (`done_dates.parquet`,
  `status_transitions.parquet`) are git-ignored and exist only in `data/` of
  the maintainer's local clone. Nothing after the extract needs them.

## Session 3 results

All figures are shares of forecasts whose band held, meaning the project
finished at least the forecast amount.

**At the app's defaults** (day buckets, 26 weeks of history, all four horizons;
6,528 forecasts). This is what the calibration plot shows.

| | P50 | P70 | P85 | P95 |
|---|---:|---:|---:|---:|
| As shipped (IQR outlier drop on) | 56% | 65% | 73% | 82% |
| Raw (no outlier drop) | 43% | 55% | 66% | 77% |

Across projects, as shipped: P85 ranges from 44% (Apache AMBARI) to 94%
(Spring SPR); the middle half of projects sits between 66% and 80%. Five
projects are inside the 80–90% pass mark, one is above it, 18 are below.
`data/project_summary.csv` has every project.

**By horizon**, as shipped, P85: 78% at 2 weeks, 74% at 4, 72% at 8, 70% at 12.
Raw: 73%, 68%, 64%, 59%. Coverage falls as the horizon grows.

**By bucket size**, P85, all horizons:

| | Day | Week | 2 weeks |
|---|---:|---:|---:|
| As shipped, 26 weeks of history | 73% | 76% | 79% |
| Raw, 26 weeks of history | 66% | 68% | 72% |
| As shipped, 12 weeks of history | 76% | 77% | 79% |
| Raw, 12 weeks of history | 69% | 70% | 73% |

**Every variant pooled** (not the headline): P85 held 77% as shipped, 70% raw.

### What the results say

- **The forecast range is too narrow.** At the app's defaults, as shipped, 56%
  of actual results fell in the outer tenths of their own forecast
  distribution (23% in the bottom tenth, 33% in the top); a calibrated forecast
  gives 20%. Raw: 50% (29% bottom, 22% top; 50.4% before rounding. This file
  and the plan said 51% until Session 3b, from adding the rounded halves). This
  is the main finding, and it is why P95 holds only about 80% of the time.
- **The outlier drop is more cautious, not more accurate.** It removes the
  busiest days from history, which lowers the forecast: the mean simulated
  total is a median 8% below the raw one, and lower in 93% of pairs. The "did
  it hold" test rewards any lower forecast, so coverage rises from 66% to 73%.
  The continuous score barely moves: mean CRPS 64 as shipped against 65 raw,
  and as shipped is the lower of the two in 14 of 24 projects. One nuance: the
  raw forecast runs high (median forecast-to-actual ratio 1.08, against 0.97
  as shipped), so the drop also removes an upward bias. In the write-up, say
  "more cautious".
- **Wider buckets help a little even for steady projects**: about 5 points of
  P85 coverage from day to 2-week buckets. CRPS is nearly the same across
  bucket sizes (63 to 68), so this too is mostly a wider or lower forecast, not
  a sharper one.
- **The batchy-team question cannot be answered from this sample.** FlowAugur's
  own `detect_cadence` labels all 24 projects as steady (daily) at every
  cutoff, because the project filter only admits continuous-flow projects. The
  bucket comparison is drawn for all 24 with no split, and says so.
- **Against the naive baseline:** the average-rate point forecast misses by a
  mean of 84 items; the Monte Carlo forecast's CRPS is about 64. CRPS of a
  point forecast is its absolute error, so the two are comparable.

Why the range is too narrow was a hypothesis at the end of Session 3 (the
engine resamples days independently, while real throughput has good and bad
stretches). Experiment 3b tested it; see the next section.

## Experiment 3b: block resampling

**Neither block length passes.** The spec is the plan's "Experiment 3b"
section, declared before any 3b results; variants, settings and pass test were
run as written. The sampler is experiment code in `experiment_3b.py`. It is
not FlowAugur's engine, not in `engine/`, and not in the app.

What was run: a moving-block bootstrap on the raw daily history (no outlier
drop), 14-day and 28-day blocks, start points uniform over every full block in
the 26-week history, blocks joined end to end and the last one cut to the
horizon; horizons of 2, 4, 8 and 12 weeks; 10,000 trials; the frozen windows
and 34 cutoffs per project. 3,264 forecasts per method, scored by the same
`replay.score` function as Session 3.

Pass test: P85 holds 80–90%; at most 30% of actuals in the outer tenths; mean
CRPS at most 64. All three are needed.

| Method | P50 | P70 | P85 | P95 | Outer tenths | Mean CRPS | P85 | Tails | CRPS | Passes |
|---|---:|---:|---:|---:|---:|---:|---|---|---|---|
| Day buckets, as shipped (Session 3) | 56% | 65% | 73.4% | 82% | 56.0% | 63.9 | fail | fail | pass | no |
| Day buckets, raw (Session 3) | 43% | 55% | 66.0% | 77% | 50.4% | 65.0 | fail | fail | fail | no |
| 14-day blocks, raw (experiment) | 44% | 58% | 70.9% | 82% | 43.4% | 63.9 | fail | fail | pass | no |
| 28-day blocks, raw (experiment) | 45% | 58% | 72.4% | 82% | 42.2% | 64.6 | fail | fail | fail | no |

Comparisons use unrounded values; none of the verdicts hinges on rounding
(14-day CRPS is 63.87, 28-day is 64.65).

What it shows:

- **Block resampling helps, by a third to a half of what is needed.** Against
  raw day buckets, P85 rises 5 to 6 points toward a pass mark 14 points away,
  and the outer-tenth share falls 7 to 8 points toward a limit 20 points away.
- **It widens the range only a little.** The gap between the P50 and P95
  amounts is a median 27% of the forecast with blocks, against 24% with single
  days.
- **It does not change the level.** Blocks and single days both run a median
  8% above the actual (forecast-to-actual ratio 1.08). The outlier drop is what
  moves the level (0.97); blocks were declared on raw history.
- **By horizon** the pattern is the same as before: 14-day blocks hold P85 79%
  of the time at 2 weeks and 64% at 12 weeks.
- **Across projects:** 2 of 24 are inside the P85 pass mark with 14-day blocks
  and 3 with 28-day, against 3 for raw day buckets and 5 as shipped.

### Pace-shift diagnostic (descriptive only, revised)

The pace ratio is each forecast's horizon daily rate divided by the daily rate
of its 26-week history. A shift is a ratio below 0.8 or above 1.25.

- **Observed:** the share of forecasts whose actual ratio is a shift.
- **Expected:** the same share across each forecast's own 10,000 simulated
  totals, averaged over forecasts. It is how often such a swing would happen
  if the method's model of variation were right.
- **Excess:** observed minus expected.

816 forecasts per horizon; the observed share is the same for every method.

| Horizon | Observed | Expected: day buckets | Excess | Expected: 14-day blocks | Excess | Expected: 28-day blocks | Excess |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2 weeks | 57.0% | 40.5% | +16.5 | 49.1% | +7.8 | 48.8% | +8.1 |
| 4 weeks | 52.2% | 26.2% | +26.0 | 35.0% | +17.2 | 40.5% | +11.7 |
| 8 weeks | 46.1% | 14.3% | +31.8 | 21.7% | +24.4 | 25.3% | +20.8 |
| 12 weeks | 46.9% | 9.2% | +37.7 | 15.0% | +31.9 | 18.2% | +28.8 |

All methods are on raw history. Excess is in percentage points.

- **Teams change pace far more than resampling expects.** Over 12 weeks the
  pace shifted in 47% of forecasts; single-day resampling expected 9%.
- **Blocks expect more shifts but leave most of the excess.** At 12 weeks the
  excess falls from 38 points to 32 (14-day) and 29 (28-day), a cut of 15% and
  24%. At 8 weeks the cut is 23% and 35%.
- **The plan's reading applies:** a large excess that blocks do not shrink
  means teams change pace in ways no resampling of their past predicts.
- **At 2 weeks chance swings dominate**, as the plan says: even single-day
  resampling expects a shift 40% of the time. Read the 8- and 12-week rows.
- The expected share falls with horizon for every method, because simulated
  totals average out; the observed share barely falls after 8 weeks.

Supporting detail, in `data/experiment_3b_pace_distribution.csv`: the ratio's
median is 0.93 and its middle half runs from 0.74 to 1.15. The median below 1
means these projects ran about 7% slower in the horizon than in the history,
the same upward bias the raw forecast has.

**History of the diagnostic.** The first run of 3b (commit `766abce`) computed
the plan's first version: the share of outer-tenth misses that came with a
shift. That figure is circular, since a miss is nearly by definition a result
far from the history rate, so the plan replaced it with the version above and
the figure is no longer computed. The variants, settings, pass test and every
score are unchanged between the two runs.

## Decisions

The plan's "Decisions locked" table, plus what was settled in Session 3 before
any full results existed:

| Decision | Choice |
|---|---|
| Cutoff rules | Confirmed as written in Session 2: best 104-week window, ties to the most recent; history stays inside it; 34 cutoffs per project; the last observed week is ignored |
| Calibration plot and PIT histogram | The app's defaults: day buckets, 26 weeks of history, all four horizons, as shipped and raw |
| Bucket-size comparison | All 24 projects, no batchy/steady split, because there are no batchy projects |
| Closures | All resolutions kept, no filter; Mojang excluded (plan) |
| Points and bars in the charts | Point = mean of the 24 project rates; bar = interquartile range across projects; every project shown as a dot |

Before the run, each project's window and first and last cutoff were checked
against the plan's "Windows and cutoffs" table: all 24 match. The table is in
`data/frozen_windows.csv`, and `backtest.py` refuses to run if the code ever
computes something different.

## How it works

`replay.py` calls the engine snapshot and nothing else; no forecasting logic is
re-implemented:

```
daily history → metrics.remove_outliers_iqr (optional)
              → metrics.bucket_daily_series
              → simulate.forecast_items_by_date (10,000 trials)
              → simulate.confidence_amounts(lower_is_safer=True)
```

For a cutoff day *T*: history is the days *T−W … T−1*, the horizon is
*T … T+H−1*. Nothing on or after *T* reaches the forecast. A band holds when
the actual is at least its amount, which is the app's own scoring rule. The
outlier drop is applied the way the app applies it: to the daily series,
before bucketing. History is every calendar day, zero-filled; the app's
weekday default is not used.

**The engine snapshot.** `engine/simulate.py` and `engine/metrics.py` are a
verbatim copy of 13 definitions from MonteJiraSim at commit
`ae17e093878c68057ee8f5f3e7e54d8fc325305a`. `engine/snapshot.py` checks them
against pinned hashes (anyone can run this; it is in the test suite) or, given
a MonteJiraSim checkout, against that commit byte for byte. It also vendors
`detect_cadence`, which `replay.py` does not call but the fan chart and
`backtest.py` do.

## What was checked

- The engine copy matches MonteJiraSim `ae17e09` byte for byte.
- The Apache SPARK replay run here with the vendored engine is identical to
  the table produced inside MonteJiraSim in Session 2, and SPARK's rows in the
  full results table are identical to that again.
- The results table has 39,168 rows, 1,632 per project, no nulls, and the four
  amounts are ordered in every row.
- 45 tests pass: replay wiring, the frozen-window gate, the summaries, the
  engine snapshot, and the block sampler, pass test and diagnostic. Ruff is
  clean.
- For the diagnostic, Session 3's raw day-bucket forecasts are regenerated
  from the same engine call and random stream, and `experiment_3b.py` stops if
  they are not identical to the rows in `results.parquet`. They are. Scores for
  the block variants are identical between the two 3b runs.
- Session 3b: the frozen-window gate passed again before the run. Scoring was
  moved into one function (`replay.score`) so the experiment is scored by the
  same code; re-running the Session 3 backtest afterwards reproduced
  `results.parquet` exactly. The baseline rows in `experiment_3b.parquet` are
  Session 3's rows. Re-drawing the Session 3 figures left them byte-identical.
- MonteJiraSim after the move: pyright clean, 803 tests at 100% coverage, ruff
  and bandit clean; `pyproject.toml` is back to its state before the backtest.
- From Session 2, still true: actual counts match a count from the per-issue
  extract, and 60 forecasts rebuilt with direct engine calls match exactly.

Not checked:

- Session 3's random streams are keyed by cutoff date and variant, not by
  project, so the five projects that share cutoff dates used the same draws.
  Each forecast is still a valid 10,000-trial sample; it was left alone so the
  published numbers stand. Experiment 3b keys its streams by project too.
- The extract scripts (`extract_done_dates.py`, `throughput_overview.py`) have
  no unit tests.
- There is no CI on this repository; tests and lint are run by hand.
- The charts were checked by eye, not by test.

## Session 4: write-up and going public

From the plan: the "where it breaks" chart (#4), the shareable page, the
LinkedIn post draft, and this repository made public.

Experiment 3b is part of the story: the plan says to publish that neither
block length passed, with the observed-against-expected pace-shift table as
the reason resampling the past has a ceiling. Everywhere it appears it has to be labelled as experiment
code, not FlowAugur's engine.

Material for "where it breaks":

- Apache AMBARI held P85 44% of the time, the worst of the 24; Red Hat JBEAP
  and Qt QTCREATORBUG are under 60%.
- Coverage falls with horizon (78% at 2 weeks to 70% at 12, as shipped).
- Bulk weeks: 593 items in one SPARK week against a median of 35; similar
  weeks in MongoDB DOCS, Red Hat KEYCLOAK, Apache FLINK and Jira CONFSERVER.
- The top-tenth pile-up as shipped (33%): the forecast is beaten by a wide
  margin a third of the time.
- Jira's own categories: Qt counts "Need More Info" as Done; Spring puts its
  "Done" status in the in-progress category.

Before making the repository public:

- **Outsiders cannot verify the engine against its source**, because
  MonteJiraSim is private. They can see the engine code and that it matches
  the pinned hashes; "this is what the app runs" rests on the maintainer's
  word unless MonteJiraSim or that commit's two files are published.
- Publishing puts the vendored engine functions under the MIT license.
- Read this file and the README once as an outsider. This file names private
  things (the plan link, MonteJiraSim internals); decide whether it stays.
- The README's "Results so far" section was written by the coding session, not
  in the maintainer's voice.
- The plan notes that seven windows fall in 2020–2021 and wants one line on
  it in the write-up.

## Data gotchas

Full list in the README. The ones most likely to bite:

- The data ends in January 2022 (MariaDB January 2021). Five of the 24 windows
  end on 2 January 2022, days before the download.
- Three quarters of counted items are Bugs; Story is 30,000 of 1.84 million.
- "Done" includes Duplicate and Invalid closures (15% and 7% of counted items).
- Converting `created` to nanosecond timestamps overflows (290 issues are
  dated before 2000). `done_date` has no such values.

## Environment

- `uv run <script>.py` installs everything from `uv.lock`. numpy, pandas and
  pyarrow are pinned to the versions the app locks; ruff to the app's lint pin.
- `uv run --group extract extract_done_dates.py <zip>` is the only step that
  needs the 5.8 GB download, and the ZIP should be assumed gone from this
  machine. A single connection to Zenodo runs at about 1 MB/s; 8 parallel
  range requests took 10 minutes.
- To check the engine against its source:
  `uv run python engine/snapshot.py --source ../MonteJiraSim`.

## Not done

- The plan doc was not edited in Session 3b. Its 3b pass test says "from 51%
  raw"; the exact figure is 50.4%, so that should read 50%.
- The plan heads the revised diagnostic "before any 3b results". In this
  repository's history the first version's figures come first (`766abce`), so
  the two do not match as worded.
- Chart #4 ("where it breaks"), the shareable page and the post are Session 4.
- No batchy projects were added; that would need its own filter, fixed before
  looking at results.
- Nothing was tried beyond the two declared block lengths. Other fixes (a
  wider range by design, a trend or pace model) would be new experiments with
  their own pass tests declared first.

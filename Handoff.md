# Handoff — FlowAugur backtest

Last updated 2026-11-12, end of Session 3 ("full backtest").

**Where it stands:** the backtest has its own repository and has been run in
full: 24 projects, 39,168 scored forecasts, one results table, and the
calibration plot, bucket-size comparison and PIT histogram. The headline is
that FlowAugur's 85% forecasts held 73% of the time, below the 80–90% pass
mark. Session 4 is the write-up and making this repository public.

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
  gives 20%. Raw: 51% (29% bottom, 22% top). This is the main finding, and it
  is why P95 holds only about 80% of the time.
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

Not established: *why* the range is too narrow. The likely cause is that the
engine resamples days independently, while real throughput has good and bad
stretches that last weeks. That is a hypothesis; nothing here tests it.

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
- 27 tests pass: replay wiring, the frozen-window gate, the summaries, the
  engine snapshot. Ruff is clean.
- MonteJiraSim after the move: pyright clean, 803 tests at 100% coverage, ruff
  and bandit clean; `pyproject.toml` is back to its state before the backtest.
- From Session 2, still true: actual counts match a count from the per-issue
  extract, and 60 forecasts rebuilt with direct engine calls match exactly.

Not checked:

- The extract scripts (`extract_done_dates.py`, `throughput_overview.py`) have
  no unit tests.
- There is no CI on this repository; tests and lint are run by hand.
- The charts were checked by eye, not by test.

## Session 4: write-up and going public

From the plan: the "where it breaks" chart (#4), the shareable page, the
LinkedIn post draft, and this repository made public.

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

- The plan doc is unchanged by this session: "Session 3" is not ticked and the
  results are not recorded there.
- Chart #4 ("where it breaks"), the shareable page and the post are Session 4.
- No batchy projects were added; that would need its own filter, fixed before
  looking at results.

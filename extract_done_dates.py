"""Extract per-issue done dates from The Public Jira Dataset — no MongoDB restore.

The dataset (Montgomery, Lüders & Maalej; Zenodo, CC BY 4.0) ships as a ZIP
holding ``mongodump --gzip --archive`` output. Instead of restoring ~60 GB into
MongoDB, this streams the bytes straight through::

    ZIP member (deflate) -> gzip -> mongodump archive framing -> BSON issue docs

and keeps only what a throughput backtest needs. One pass over the archive
(about 3.5 minutes, constant memory) writes, into ``data/``:

``done_dates.parquet``
    One row per issue (every issue, done or not), sorted by instance / project /
    issue number: type, current status + category, resolution, created,
    resolutiondate, and the derived ``first_done`` / ``last_done`` /
    ``done_entries`` / ``done_date`` / ``done_source``.

``status_transitions.parquet``
    Every changelog status change: instance, issue_id, at (UTC), from/to status
    id + name, and whether the same changelog entry also set a resolution. Kept
    so done dates can be re-derived under a different rule (``--derive-only``)
    without the 5.8 GB download.

``status_categories.csv``
    The status -> category map the derivation used, and how each was inferred.

``instance_summary.csv``
    Issue / project / done counts and date span per Jira instance.

Done-date rule
--------------
``done_date`` is the first changelog transition that takes the issue *into* a
Done-category status from outside that category (``first_done``); if the
changelog shows no such entry, the resolution date. ``done_source`` says which:

* ``changelog``               an entry into Done was found
* ``resolutiondate``          the issue has no changelog at all
* ``resolutiondate_no_entry`` it has a changelog, but no entry into Done: no
  status changes, only moves between Done statuses (e.g. a bulk Resolved ->
  Closed years after the fact), or only moves to non-Done statuses

Moves *between* Done statuses are not entries: where the changelog has lost the
original resolve, counting a later Resolved -> Closed would date thousands of
old issues to one bulk-migration day.

Status categories
-----------------
Jira changelogs record status ids/names but not categories, so each status's
category is inferred per instance, first match wins (``source`` in the CSV):

1. ``current``     the category of issues currently in that status (by id)
2. ``name``        same name as exactly one current category in the instance
3. ``builtin``     Jira's built-in statuses: 1 Open, 3 In Progress, 4 Reopened,
                   5 Resolved, 6 Closed (5 and 6 are Done)
4. ``resolution``  a status no issue holds any more counts as Done when at least
                   80% of the (>= 100) transitions into it also set a resolution
5. ``unknown``     everything else; never counts as Done

Usage::

    uv run --group extract extract_done_dates.py path/to/ThePublicJiraDataset.zip
    uv run --group extract extract_done_dates.py --derive-only   # re-derive, no ZIP
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
import zipfile
import zlib
from collections.abc import Iterable, Iterator
from pathlib import Path

import bson
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ARCHIVE_MAGIC = b"\x6d\xe2\x99\x81"  # mongodump archive magic number 0x8199e26d
_INT32 = struct.Struct("<i")
_TERMINATOR = object()  # the archive's 0xFFFFFFFF segment terminator

FLUSH_ISSUES = 100_000

# Jira's built-in statuses keep these ids and categories on every Server/DC instance.
BUILTIN_CATEGORY = {"1": "new", "3": "indeterminate", "4": "new", "5": "done", "6": "done"}
# A retired status counts as Done when this share of the transitions into it set a resolution.
RESOLUTION_SHARE = 0.8
RESOLUTION_MIN_TRANSITIONS = 100
TS = pa.timestamp("s", tz="UTC")

TRANSITION_SCHEMA = pa.schema(
    [
        ("instance", pa.string()),
        ("issue_id", pa.int64()),
        ("at", TS),
        ("from_id", pa.string()),
        ("from_status", pa.string()),
        ("to_id", pa.string()),
        ("to_status", pa.string()),
        ("with_resolution", pa.bool_()),
    ]
)

ISSUE_SCHEMA = pa.schema(
    [
        ("instance", pa.string()),
        ("issue_id", pa.int64()),
        ("key", pa.string()),
        ("project", pa.string()),
        ("issue_type", pa.string()),
        ("is_subtask", pa.bool_()),
        ("status", pa.string()),
        ("status_id", pa.string()),
        ("status_category", pa.string()),
        ("resolution", pa.string()),
        ("created", TS),
        ("resolutiondate", TS),
        ("n_histories", pa.int32()),
        ("n_status_changes", pa.int32()),
        ("changelog_truncated", pa.bool_()),
    ]
)

# Final column order of done_dates.parquet.
DONE_COLUMNS = [
    "instance",
    "project",
    "key",
    "issue_id",
    "issue_type",
    "is_subtask",
    "status",
    "status_id",
    "status_category",
    "resolution",
    "created",
    "resolutiondate",
    "first_done",
    "last_done",
    "done_entries",
    "done_date",
    "done_source",
    "n_histories",
    "n_status_changes",
    "changelog_truncated",
]


# --------------------------------------------------------------------------
# Streaming: ZIP -> gzip -> mongodump archive -> raw BSON documents
# --------------------------------------------------------------------------


def archive_chunks(zip_path: Path) -> Iterator[bytes]:
    """Yield the decompressed mongodump archive in chunks, straight from the ZIP.

    zipfile inflates the member (and checks its CRC-32 at the end); the member
    itself is a gzip stream, inflated here. Handles concatenated gzip members.
    """
    with zipfile.ZipFile(zip_path) as zf:
        members = [i for i in zf.infolist() if i.filename.endswith(".archive")]
        if len(members) != 1:
            raise ValueError(f"expected one .archive member, found {len(members)}")
        with zf.open(members[0]) as member:
            gunzip = zlib.decompressobj(zlib.MAX_WBITS | 16)
            while raw := member.read(1 << 20):
                while raw:
                    out = gunzip.decompress(raw)
                    if out:
                        yield out
                    if not gunzip.eof:
                        break
                    raw = gunzip.unused_data
                    gunzip = zlib.decompressobj(zlib.MAX_WBITS | 16)


class ArchiveReader:
    """Iterate ``(collection, raw_bson_doc)`` over a mongodump archive stream.

    Archive layout (mongo-tools ``common/archive``)::

        magic  header-doc  collection-metadata-doc*  TERMINATOR
        ( namespace-header  data-doc*  TERMINATOR
        | namespace-header{EOF: true}  TERMINATOR )*

    Segments of different collections interleave (``concurrent_collections``).
    """

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = iter(chunks)
        self._buf = b""
        self._pos = 0
        self.bytes_read = 0
        self.header: dict = {}
        self.collections: list[str] = []
        self.finished: set[str] = set()

    def _fill(self, n: int) -> bool:
        """Make ``n`` bytes available at the cursor; False on a clean end of stream."""
        while len(self._buf) - self._pos < n:
            chunk = next(self._chunks, None)
            if chunk is None:
                if len(self._buf) == self._pos:
                    return False
                raise EOFError("archive ends in the middle of a document")
            self._buf = self._buf[self._pos :] + chunk
            self._pos = 0
            self.bytes_read += len(chunk)
        return True

    def _next(self) -> bytes | object | None:
        """Next raw BSON doc, ``_TERMINATOR``, or None at end of stream."""
        if not self._fill(4):
            return None
        (n,) = _INT32.unpack_from(self._buf, self._pos)
        if n == -1:
            self._pos += 4
            return _TERMINATOR
        if n < 5:
            raise ValueError(f"bad BSON length {n}")
        if not self._fill(n):
            raise EOFError("archive ends in the middle of a document")
        doc = self._buf[self._pos : self._pos + n]
        self._pos += n
        return doc

    def _doc(self) -> dict:
        el = self._next()
        if not isinstance(el, bytes):
            raise ValueError("expected a BSON document")
        return bson.decode(el)

    def __iter__(self) -> Iterator[tuple[str, bytes]]:
        if not self._fill(4) or self._buf[self._pos : self._pos + 4] != ARCHIVE_MAGIC:
            raise ValueError("not a mongodump archive (bad magic number)")
        self._pos += 4
        self.header = self._doc()
        while (el := self._next()) is not _TERMINATOR:
            if not isinstance(el, bytes):
                raise EOFError("archive ends inside its prelude")
            self.collections.append(bson.decode(el)["collection"])
        while (el := self._next()) is not None:
            if not isinstance(el, bytes):
                raise ValueError("unexpected terminator between segments")
            ns = bson.decode(el)
            name = ns["collection"]
            if ns.get("EOF"):
                if self._next() is not _TERMINATOR:
                    raise ValueError("EOF header not followed by a terminator")
                self.finished.add(name)
                continue
            while (el := self._next()) is not _TERMINATOR:
                if not isinstance(el, bytes):
                    raise EOFError("archive ends inside a collection segment")
                yield name, el


# --------------------------------------------------------------------------
# Pass 1: pull the few fields we need out of every issue document
# --------------------------------------------------------------------------


def _utc(values: list) -> pa.Array:
    """Jira timestamps ('2020-11-01T23:04:01.521+0200') -> UTC, second precision."""
    ts = pd.to_datetime(pd.Series(values, dtype="object"), utc=True, format="ISO8601")
    return pa.Array.from_pandas(ts.dt.floor("s")).cast(TS)


class Extractor:
    """Accumulates per-issue rows in memory and streams transitions to Parquet."""

    def __init__(self, transitions_path: Path) -> None:
        self._writer = pq.ParquetWriter(
            transitions_path, TRANSITION_SCHEMA, compression="zstd", compression_level=9
        )
        self._tables: list[pa.Table] = []
        self._seen: dict[str, set[int]] = {}
        self.n_duplicates = 0
        self.n_issues = 0
        self.n_histories = 0
        self.n_change_items = 0
        self.n_transitions = 0
        self._reset()

    def _reset(self) -> None:
        self._i: dict[str, list] = {f.name: [] for f in ISSUE_SCHEMA}
        self._t: dict[str, list] = {f.name: [] for f in TRANSITION_SCHEMA}

    def add(self, instance: str, raw: bytes) -> None:
        doc = bson.decode(raw)
        f = doc.get("fields") or {}
        issue_id = int(doc["id"])
        seen = self._seen.setdefault(instance, set())
        if issue_id in seen:  # the dump holds a few issues twice (identical copies)
            self.n_duplicates += 1
            return
        seen.add(issue_id)
        itype = f.get("issuetype") or {}
        status = f.get("status") or {}
        changelog = doc.get("changelog") or {}
        histories = changelog.get("histories") or []

        t = self._t
        n_status = 0
        for h in histories:
            items = h.get("items") or ()
            self.n_change_items += len(items)
            resolves = None
            for it in items:
                if it.get("field") != "status":
                    continue
                if resolves is None:  # does this same changelog entry set a resolution?
                    resolves = any(
                        j.get("field") == "resolution"
                        and (j.get("to") is not None or bool(j.get("toString")))
                        for j in items
                    )
                n_status += 1
                t["instance"].append(instance)
                t["issue_id"].append(issue_id)
                t["at"].append(h.get("created"))
                t["from_id"].append(it.get("from"))
                t["from_status"].append(it.get("fromString"))
                t["to_id"].append(it.get("to"))
                t["to_status"].append(it.get("toString"))
                t["with_resolution"].append(resolves)

        i = self._i
        i["instance"].append(instance)
        i["issue_id"].append(issue_id)
        i["key"].append(doc.get("key"))
        i["project"].append((f.get("project") or {}).get("key"))
        i["issue_type"].append(itype.get("name"))
        i["is_subtask"].append(itype.get("subtask"))
        i["status"].append(status.get("name"))
        i["status_id"].append(status.get("id"))
        i["status_category"].append((status.get("statusCategory") or {}).get("key"))
        i["resolution"].append((f.get("resolution") or {}).get("name"))
        i["created"].append(f.get("created"))
        i["resolutiondate"].append(f.get("resolutiondate"))
        i["n_histories"].append(len(histories))
        i["n_status_changes"].append(n_status)
        total = changelog.get("total")
        i["changelog_truncated"].append(total is not None and total > len(histories))

        self.n_issues += 1
        self.n_histories += len(histories)
        self.n_transitions += n_status
        if len(i["instance"]) >= FLUSH_ISSUES:
            self.flush()

    def flush(self) -> None:
        for cols, schema, sink in (
            (self._i, ISSUE_SCHEMA, self._tables.append),
            (self._t, TRANSITION_SCHEMA, self._writer.write_table),
        ):
            if cols["instance"]:
                arrays = [
                    _utc(cols[f.name]) if f.type == TS else pa.array(cols[f.name], f.type)
                    for f in schema
                ]
                sink(pa.Table.from_arrays(arrays, schema=schema))
        self._reset()

    def finish(self) -> pa.Table:
        self.flush()
        self._writer.close()
        return pa.concat_tables(self._tables)


# --------------------------------------------------------------------------
# Pass 2: status categories -> done dates
# --------------------------------------------------------------------------


def _name_key(names: pd.Series) -> pd.Series:
    return names.str.strip().str.lower()


def _ends(transitions: pd.DataFrame, side: str) -> pd.DataFrame:
    """The status on one side ('to' / 'from') of every transition."""
    ends = pd.DataFrame(
        {
            "instance": transitions["instance"].to_numpy(),
            "status_id": transitions[f"{side}_id"].to_numpy(),
            "status": transitions[f"{side}_status"].to_numpy(),
        }
    )
    ends["name_key"] = _name_key(ends["status"])
    return ends


def categorize(issues: pd.DataFrame, transitions: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    """Status category of each transition's target and origin, plus the audit map.

    Tiers are described in the module docstring: current -> name -> builtin ->
    resolution -> unknown.
    """
    key = ["instance", "status_id", "name_key"]
    cur = (
        issues.dropna(subset=["status_id", "status_category"])
        .groupby(["instance", "status_id", "status", "status_category"])
        .size()
        .rename("n_issues")
        .reset_index()
        .sort_values("n_issues", ascending=False)
    )
    cur["name_key"] = _name_key(cur["status"])
    # One category per id: if an id ever shows two, the more common one wins.
    by_id = cur.drop_duplicates(["instance", "status_id"])[["instance", "status_id", "status_category"]]
    by_id = by_id.rename(columns={"status_category": "c_current"})
    by_name = (
        cur.drop_duplicates(["instance", "name_key", "status_category"])
        .drop_duplicates(["instance", "name_key"], keep=False)[["instance", "name_key", "status_category"]]
        .rename(columns={"status_category": "c_name"})
    )
    n_issues = cur.groupby(["instance", "status_id"])["n_issues"].sum().rename("n_issues").reset_index()

    to, origin = _ends(transitions, "to"), _ends(transitions, "from")
    to["with_resolution"] = transitions["with_resolution"].to_numpy()
    seen = (
        to.groupby(key, dropna=False)
        .agg(status=("status", "first"), n_transitions_into=("instance", "size"), n_res=("with_resolution", "sum"))
        .reset_index()
    )
    # Statuses that only ever appear as an origin, or only as a current status.
    others = pd.concat([origin[key + ["status"]], cur[key + ["status"]]]).drop_duplicates(key)
    seen = seen.merge(others, on=key, how="outer", suffixes=("", "_other"))
    seen["status"] = seen["status"].fillna(seen.pop("status_other"))
    seen[["n_transitions_into", "n_res"]] = seen[["n_transitions_into", "n_res"]].fillna(0).astype("int64")

    # Share of transitions into the status (all its names) that set a resolution.
    same = seen["status_id"].fillna("name:" + seen["name_key"].fillna(""))
    into = seen.groupby([seen["instance"], same])["n_transitions_into"].transform("sum")
    share = seen.groupby([seen["instance"], same])["n_res"].transform("sum") / into.where(into > 0)
    seen["share_setting_resolution"] = share.round(4)

    seen = seen.merge(by_id, on=["instance", "status_id"], how="left")
    seen = seen.merge(by_name, on=["instance", "name_key"], how="left")
    seen.loc[seen["status_id"].isna(), "c_current"] = None  # a null id is not an id match
    tiers = {
        "current": seen["c_current"],
        "name": seen["c_name"],
        "builtin": seen["status_id"].map(BUILTIN_CATEGORY),
        "resolution": pd.Series(
            np.where((share >= RESOLUTION_SHARE) & (into >= RESOLUTION_MIN_TRANSITIONS), "done", None),
            index=seen.index,
            dtype="object",
        ),
    }
    seen["status_category"] = None
    seen["source"] = "unknown"
    for source, category in reversed(tiers.items()):
        hit = category.notna()
        seen.loc[hit, "status_category"] = category[hit]
        seen.loc[hit, "source"] = source
    seen["status_category"] = seen["status_category"].fillna("unknown")

    def category_of(ends: pd.DataFrame) -> pd.Series:
        return ends.merge(seen[key + ["status_category"]], on=key, how="left")["status_category"].fillna("unknown")

    audit = seen.merge(n_issues, on=["instance", "status_id"], how="left")
    audit["n_issues"] = audit["n_issues"].fillna(0).astype("int64")
    audit = audit.sort_values(
        ["instance", "status_category", "n_transitions_into"], ascending=[True, True, False]
    ).reset_index(drop=True)
    cols = [
        "instance", "status_id", "status", "status_category", "source",
        "n_issues", "n_transitions_into", "share_setting_resolution",
    ]  # fmt: skip
    return category_of(to), category_of(origin), audit[cols]


def derive(issues: pd.DataFrame, transitions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add first_done / last_done / done_entries / done_date / done_source."""
    to_cat, from_cat, audit = categorize(issues, transitions)
    # An entry: lands in a Done-category status, coming from outside the category.
    entry = (to_cat == "done").to_numpy() & (from_cat != "done").to_numpy()
    keys = ["instance", "issue_id"]
    entries = transitions[entry].groupby(keys)["at"].agg(first_done="min", last_done="max", done_entries="size")

    derived = ["first_done", "last_done", "done_entries", "done_date", "done_source"]
    out = issues.drop(columns=derived, errors="ignore").join(entries, on=keys)
    out["done_entries"] = out["done_entries"].fillna(0).astype("int32")

    from_log = out["first_done"].notna()
    from_res = ~from_log & out["resolutiondate"].notna()
    has_log = out["n_histories"] > 0
    out["done_date"] = out["first_done"].where(from_log, out["resolutiondate"])
    out["done_source"] = np.select(
        [from_log, from_res & ~has_log, from_res & has_log],
        ["changelog", "resolutiondate", "resolutiondate_no_entry"],
        None,
    )

    out["_num"] = pd.to_numeric(out["key"].str.rsplit("-", n=1).str[-1], errors="coerce")
    out = out.sort_values(["instance", "project", "_num", "issue_id"]).reset_index(drop=True)
    return out[DONE_COLUMNS], audit


def instance_summary(done: pd.DataFrame, transitions: pd.DataFrame) -> pd.DataFrame:
    """Counts and date span per instance. ``last_activity`` (latest issue created
    or status change) approximates the date the instance was downloaded."""
    g = done.groupby("instance")
    source = done["done_source"]
    out = pd.DataFrame(
        {
            "issues": g.size(),
            "projects": g["project"].nunique(),
            "with_done_date": g["done_date"].count(),
            "from_changelog": (source == "changelog").groupby(done["instance"]).sum(),
            "from_resolutiondate": (source == "resolutiondate").groupby(done["instance"]).sum(),
            "from_resolutiondate_no_entry": (source == "resolutiondate_no_entry")
            .groupby(done["instance"])
            .sum(),
            "first_created": g["created"].min(),
            "last_created": g["created"].max(),
            "last_status_change": transitions.groupby("instance")["at"].max(),
        }
    )
    out["last_activity"] = out[["last_created", "last_status_change"]].max(axis=1)
    for c in ("first_created", "last_created", "last_status_change", "last_activity"):
        out[c] = out[c].dt.date
    return out.drop(columns=["last_status_change"]).rename_axis("instance").reset_index()


def write_outputs(done: pd.DataFrame, audit: pd.DataFrame, summary: pd.DataFrame, out_dir: Path) -> None:
    summary.to_csv(out_dir / "instance_summary.csv", index=False)
    audit.to_csv(out_dir / "status_categories.csv", index=False)
    table = pa.Table.from_pandas(done[DONE_COLUMNS], preserve_index=False)
    for name in ("created", "resolutiondate", "first_done", "last_done", "done_date"):
        table = table.set_column(table.schema.get_field_index(name), name, table[name].cast(TS))
    pq.write_table(
        table,
        out_dir / "done_dates.parquet",
        compression="zstd",
        compression_level=19,
        use_dictionary=[c for c in DONE_COLUMNS if c != "key"],
        column_encoding={"key": "DELTA_BYTE_ARRAY"},
        row_group_size=1 << 20,
    )


# --------------------------------------------------------------------------


def run_stream(zip_path: Path, out_dir: Path, max_docs: int | None) -> pd.DataFrame:
    t0 = time.time()
    reader = ArchiveReader(archive_chunks(zip_path))
    ex = Extractor(out_dir / "status_transitions.parquet")
    for name, raw in reader:
        ex.add(name, raw)
        if ex.n_issues % 250_000 == 0:
            el = time.time() - t0
            print(
                f"  {ex.n_issues:>9,} issues  {reader.bytes_read / 1e9:6.1f} GB  "
                f"{el:5.0f}s  ({reader.bytes_read / 1e6 / el:.0f} MB/s)",
                flush=True,
            )
        if max_docs and ex.n_issues >= max_docs:
            break
    per_collection = {k: len(v) for k, v in ex._seen.items()}
    issues = ex.finish()
    complete = not max_docs or ex.n_issues < max_docs
    if complete and set(reader.collections) != reader.finished:
        missing = sorted(set(reader.collections) - reader.finished)
        raise RuntimeError(f"archive ended without EOF markers for {missing}")
    print(
        f"mongodump {reader.header.get('tool_version')} / server {reader.header.get('server_version')}; "
        f"{len(reader.collections)} collections; {reader.bytes_read / 1e9:.1f} GB of BSON in {time.time() - t0:.0f}s"
    )
    print(
        f"{ex.n_issues:,} issues ({ex.n_duplicates} duplicate copies skipped), "
        f"{ex.n_histories:,} changelog entries, {ex.n_change_items:,} field changes, "
        f"{ex.n_transitions:,} status changes"
    )
    for name in sorted(per_collection):
        print(f"  {name:<14} {per_collection[name]:>9,}")
    # Staged so a failure in the derive step doesn't cost a re-read of the dump.
    pq.write_table(issues, out_dir / "_issues_stage.parquet", compression="zstd")
    return issues.to_pandas()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("zip", nargs="?", type=Path, help="path to the dataset ZIP from Zenodo")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "data")
    ap.add_argument("--derive-only", action="store_true", help="re-derive from existing outputs")
    ap.add_argument("--max-docs", type=int, help="stop after N issues (smoke test)")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    stage = args.out / "_issues_stage.parquet"

    if args.derive_only:
        issues = pd.read_parquet(stage if stage.exists() else args.out / "done_dates.parquet")
    elif args.zip:
        issues = run_stream(args.zip, args.out, args.max_docs)
    else:
        ap.error("give the dataset ZIP, or --derive-only")

    transitions = pd.read_parquet(args.out / "status_transitions.parquet")
    done, audit = derive(issues, transitions)
    write_outputs(done, audit, instance_summary(done, transitions), args.out)
    stage.unlink(missing_ok=True)

    n_projects = done.groupby("instance")["project"].nunique().sum()
    print(
        f"done_dates.parquet: {len(done):,} issues in {n_projects:,} projects, "
        f"{done['done_date'].notna().sum():,} with a done date"
    )
    print(f"  done_source: {done['done_source'].value_counts(dropna=False).to_dict()}")
    into = audit.groupby("source")["n_transitions_into"].sum().to_dict()
    print(f"  status changes by how the target status was categorised: {into}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

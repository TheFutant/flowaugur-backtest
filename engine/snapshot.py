"""Vendor and verify the FlowAugur engine functions this backtest calls.

The backtest's claim is that it runs FlowAugur's own forecasting code. That code
lives in MonteJiraSim, the app's repository; this folder holds a verbatim copy of
just the definitions the backtest uses, taken at one named commit.

Two checks, both exact text comparisons:

``python engine/snapshot.py``
    Each vendored definition still hashes to the value pinned in
    ``SNAPSHOT.json``. Needs nothing but this repo; the test suite runs it.

``python engine/snapshot.py --source ../MonteJiraSim``
    Rebuilds every vendored file from the pinned commit of a MonteJiraSim
    checkout and compares it byte for byte. Also reports whether those
    definitions have changed in the checkout since the snapshot.

``python engine/snapshot.py --source ../MonteJiraSim --write [--commit SHA]``
    Re-vendors (default: the checkout's HEAD) and rewrites ``SNAPSHOT.json``.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "SNAPSHOT.json"

# Source file -> the imports its vendored subset needs, and the top-level names taken.
SPEC: dict[str, dict] = {
    "simulate.py": {
        "imports": "import numpy as np",
        "names": ["DEFAULT_LEVELS", "forecast_items_by_date", "confidence_amounts"],
    },
    "metrics.py": {
        "imports": "from dataclasses import dataclass\n\nimport numpy as np\nimport pandas as pd",
        "names": [
            "GRANULARITIES",
            "DEFAULT_SPRINT_DAYS",
            "MIN_CADENCE_SPAN",
            "MIN_CADENCE_ITEMS",
            "_FLOW_ACTIVE_RATIO",
            "_BURST_GAP_DAYS",
            "bucket_daily_series",
            "CadenceReport",
            "detect_cadence",
            "remove_outliers_iqr",
        ],
    },
}

HEADER = '''"""FlowAugur engine snapshot: selected definitions from ``{file}``.

Copied verbatim from MonteJiraSim, the FlowAugur app's repository, at commit
{commit}.

Do not edit. ``engine/snapshot.py`` regenerates this file from a MonteJiraSim
checkout and checks it against that commit; ``engine/SNAPSHOT.json`` pins a
hash of every definition below.

Definitions: {names}.
"""
from __future__ import annotations

{imports}
'''


def _defined(node: ast.stmt) -> list[str]:
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def extract(source: str, names: list[str]) -> dict[str, str]:
    """Exact source text of each named top-level definition, in ``names`` order.

    A definition is its statement (decorators included) plus any comment lines
    sitting directly above it.
    """
    lines = source.splitlines()
    found: dict[str, str] = {}
    for node in ast.parse(source).body:
        wanted = [n for n in _defined(node) if n in names]
        if not wanted:
            continue
        start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
        while start > 1 and lines[start - 2].lstrip().startswith("#"):
            start -= 1
        text = "\n".join(lines[start - 1 : node.end_lineno])
        for name in wanted:
            found[name] = text
    missing = [n for n in names if n not in found]
    if missing:
        raise KeyError(f"not defined at top level: {missing}")
    return {name: found[name] for name in names}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def render(file: str, source: str, commit: str) -> str:
    """The vendored file for ``file`` as it stands in ``source``."""
    spec = SPEC[file]
    parts = extract(source, spec["names"])
    blocks = list(dict.fromkeys(parts.values()))  # one statement may define several names
    header = HEADER.format(file=file, commit=commit, names=", ".join(spec["names"]), imports=spec["imports"])
    return header + "\n\n" + "\n\n\n".join(blocks) + "\n"


def git_show(source: Path, commit: str, file: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source), "show", f"{commit}:{file}"], check=True, capture_output=True, text=True
    ).stdout


def write(source: Path, commit: str) -> None:
    commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", commit], check=True, capture_output=True, text=True
    ).stdout.strip()
    manifest: dict = {"source": "MonteJiraSim", "commit": commit, "files": {}}
    for file, spec in SPEC.items():
        text = git_show(source, commit, file)
        (HERE / file).write_text(render(file, text, commit))
        manifest["files"][file] = {name: digest(part) for name, part in extract(text, spec["names"]).items()}
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")


def check_pinned() -> list[str]:
    """Problems found comparing the vendored definitions with the pinned hashes."""
    manifest = json.loads(MANIFEST.read_text())
    problems = []
    for file, spec in SPEC.items():
        pinned = manifest["files"].get(file, {})
        vendored = (HERE / file).read_text()
        if manifest["commit"] not in vendored:
            problems.append(f"{file}: header does not name commit {manifest['commit']}")
        for name, text in extract(vendored, spec["names"]).items():
            if pinned.get(name) != digest(text):
                problems.append(f"{file}: {name} differs from the pinned snapshot")
        if sorted(pinned) != sorted(spec["names"]):
            problems.append(f"{file}: pinned names do not match SPEC")
    return problems


def check_source(source: Path) -> tuple[list[str], list[str]]:
    """(problems against the pinned commit, definitions that have since changed at HEAD)."""
    commit = json.loads(MANIFEST.read_text())["commit"]
    problems, drift = [], []
    for file, spec in SPEC.items():
        if (HERE / file).read_text() != render(file, git_show(source, commit, file), commit):
            problems.append(f"{file}: vendored file is not what commit {commit[:10]} produces")
        then = extract(git_show(source, commit, file), spec["names"])
        now = extract(git_show(source, "HEAD", file), spec["names"])
        drift += [f"{file}: {name}" for name in spec["names"] if then[name] != now[name]]
    return problems, drift


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", type=Path, help="path to a MonteJiraSim checkout")
    ap.add_argument("--write", action="store_true", help="re-vendor from --source")
    ap.add_argument("--commit", default="HEAD", help="commit to vendor with --write (default HEAD)")
    args = ap.parse_args(argv)

    if args.write:
        if not args.source:
            ap.error("--write needs --source")
        write(args.source, args.commit)
    problems = check_pinned()
    commit = json.loads(MANIFEST.read_text())["commit"]
    n = sum(len(spec["names"]) for spec in SPEC.values())
    if args.source and not problems:
        problems, drift = check_source(args.source)
        if drift:
            print("changed in MonteJiraSim since the snapshot (re-vendor to pick up):", ", ".join(drift))
    for problem in problems:
        print("MISMATCH", problem)
    if not problems:
        against = "the pinned hashes and the source commit" if args.source else "the pinned hashes"
        print(f"engine snapshot OK: {n} definitions match {against} (MonteJiraSim {commit[:10]})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

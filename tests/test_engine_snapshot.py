"""The vendored engine must stay a verbatim copy of the FlowAugur code it claims to be."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine import snapshot


def test_vendored_definitions_match_the_pinned_hashes() -> None:
    assert snapshot.check_pinned() == []


def test_an_edited_definition_is_caught(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*snapshot.SPEC, "SNAPSHOT.json"):
        (tmp_path / name).write_text((snapshot.HERE / name).read_text())
    target = tmp_path / "simulate.py"
    target.write_text(target.read_text().replace("replace=True", "replace=False"))
    monkeypatch.setattr(snapshot, "HERE", tmp_path)
    monkeypatch.setattr(snapshot, "MANIFEST", tmp_path / "SNAPSHOT.json")
    assert snapshot.check_pinned() == ["simulate.py: forecast_items_by_date differs from the pinned snapshot"]


def test_extract_takes_the_statement_and_the_comments_directly_above_it() -> None:
    source = "import x\n\n# about A\n# more\nA = 1\n\n\n@decorator\ndef f():\n    return A\n\nB = 2\n"
    parts = snapshot.extract(source, ["A", "f"])
    assert parts == {"A": "# about A\n# more\nA = 1", "f": "@decorator\ndef f():\n    return A"}
    with pytest.raises(KeyError, match="missing"):
        snapshot.extract(source, ["missing"])


@pytest.mark.skipif("FLOWAUGUR_SOURCE" not in os.environ, reason="set FLOWAUGUR_SOURCE to a MonteJiraSim checkout")
def test_vendored_files_are_what_the_source_commit_produces() -> None:
    problems, _ = snapshot.check_source(Path(os.environ["FLOWAUGUR_SOURCE"]))
    assert problems == []

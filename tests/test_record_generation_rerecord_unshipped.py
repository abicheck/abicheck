"""``--rerecord-unshipped``: re-record an unshipped generation, gated on origin/main.

The oracle is a fixture ``generation_fingerprints.json`` standing in for
``origin/main``, not the script's own helper. The record itself is kept in
memory: these tests never write the real fingerprint file.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from abicheck.storage import generation_scope as scope, versioning
from scripts import record_generation_fingerprints as script


def _main_text(extractor: int) -> str:
    return json.dumps(
        {"extractor": {"generation": extractor}, "resolver": {"generation": 4}}
    )


@pytest.fixture
def record(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"extractor": {"generation": 85, "fingerprint": "old"}}
    written: dict[str, Any] = {}
    monkeypatch.setattr(scope, "load_record", lambda: dict(state))
    monkeypatch.setattr(scope, "write_record", lambda data: written.update(data))
    monkeypatch.setattr(versioning, "EXTRACTOR_GENERATION", 85)
    monkeypatch.setattr(
        script,
        "_entry",
        lambda axis, gen, reason: {
            "generation": gen,
            "fingerprint": "new",
            "reason": reason,
            "files": {},
        },
    )
    return written


def _run(argv: list[str], main_text: str | None = None, *, fail: bool = False) -> int:
    def reader() -> str:
        if fail:
            raise RuntimeError("main unreadable")
        assert main_text is not None
        return main_text

    return script.main(argv, read_main_text=reader)


def _argv(*extra: str) -> list[str]:
    return ["--axis", "extractor", "--rerecord-unshipped", *extra]


def test_a_generation_above_main_is_re_recorded_with_its_reason(
    record: dict[str, Any],
) -> None:
    assert (
        _run(_argv("--reason", "by-value fact and indirection kind"), _main_text(84))
        == 0
    )
    assert record["extractor"]["generation"] == 85
    assert record["extractor"]["reason"] == "by-value fact and indirection kind"
    assert record["extractor"]["fingerprint"] == "new"


def test_a_shipped_generation_equal_to_main_is_refused(record: dict[str, Any]) -> None:
    assert _run(_argv("--reason", "x"), _main_text(85)) == 2
    assert record == {}


def test_a_generation_below_main_is_refused(record: dict[str, Any]) -> None:
    assert _run(_argv("--reason", "x"), _main_text(86)) == 2
    assert record == {}


def test_a_missing_reason_is_refused_even_when_allowed(record: dict[str, Any]) -> None:
    assert _run(_argv(), _main_text(84)) == 2
    assert _run(_argv("--reason", "   "), _main_text(84)) == 2
    assert record == {}


def test_an_unreadable_main_fails_closed(record: dict[str, Any]) -> None:
    assert _run(_argv("--reason", "x"), fail=True) == 2
    assert record == {}


def test_a_main_record_without_the_axis_fails_closed(record: dict[str, Any]) -> None:
    assert (
        _run(_argv("--reason", "x"), json.dumps({"resolver": {"generation": 4}})) == 2
    )
    assert record == {}


def test_a_constant_not_equal_to_the_record_is_refused(
    record: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(versioning, "EXTRACTOR_GENERATION", 86)
    assert _run(_argv("--reason", "x"), _main_text(84)) == 2
    assert record == {}

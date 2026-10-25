"""The warning-hygiene gate itself (``tests/_model_warning_hygiene.py``).

Oracle: the records each test emits by hand, not the gate's own filter.
"""

from __future__ import annotations

import logging

import pytest

from abicheck.model import AbiSnapshot
from abicheck.model.entities import RecordType
from tests._model_warning_hygiene import model_warning_gate, unexpected


def _rec(name: str, level: int, msg: str) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, msg, None, None)


def test_unexpected_keeps_unallowed_warnings_only() -> None:
    records = [
        _rec("abicheck.model.x", logging.WARNING, "Duplicate type names skipped"),
        _rec("abicheck.model.x", logging.ERROR, "known noise 42"),
        _rec("abicheck.model.x", logging.INFO, "chatter"),
    ]
    assert unexpected(records, {}) == [
        "abicheck.model.x: Duplicate type names skipped",
        "abicheck.model.x: known noise 42",
    ]
    assert unexpected(records, {r"known noise \d+": "reason"}) == [
        "abicheck.model.x: Duplicate type names skipped"
    ]


def test_gate_fails_on_a_real_first_wins_duplicate() -> None:
    """A true duplicate qualified name in the model index trips the gate."""
    snap = AbiSnapshot(
        library="l.so",
        version="1",
        types=[
            RecordType(name="a::P", kind="struct"),
            RecordType(name="a::P", kind="struct"),
        ],
    )
    with pytest.raises(AssertionError, match="Duplicate type names"):
        with model_warning_gate():
            snap.index()


def test_gate_ignores_other_loggers_and_restores_the_level() -> None:
    logger = logging.getLogger("abicheck.model")
    before = logger.level
    with model_warning_gate() as handler:
        logging.getLogger("abicheck.report.x").warning("not model")
        logging.getLogger("abicheck.model.y").info("below threshold")
    assert handler.records == []
    assert logger.level == before
    assert handler not in logger.handlers


@pytest.mark.model_warning_hygiene
def test_marker_passes_a_quiet_model_run() -> None:
    snap = AbiSnapshot(
        library="l.so",
        version="1",
        types=[
            RecordType(name="P", kind="struct", qualified_name=f"{ns}::P")
            for ns in "ab"
        ],
    )
    snap.index()
    assert snap.type_by_name("b::P").qualified_name == "b::P"


def _first_wins(keys: str) -> logging.LogRecord:
    rec = logging.LogRecord(
        "abicheck.model.snapshot_index", logging.WARNING, __file__, 1,
        "Duplicate %s skipped (first-wins) in %s: %s",
        ("mangled symbols", "lib.so@1", keys), None,
    )  # fmt: skip
    return rec


@pytest.mark.parametrize(
    ("keys", "allowed"),
    [
        ("wcschr (×2), wmemchr (×2)", True),
        ("__abicheck_ctor__std::tuple<a *, b>(const tuple<a *, b>&) (×3)", True),
        ("wcschr (×2), Parameter (×5)", False),  # one new name fails it
        ("__abicheck_ctor__mylib::W(int) (×2)", False),  # not std::
    ],
)
def test_first_wins_warning_allowed_only_when_every_key_is(
    keys: str, allowed: bool
) -> None:
    keys_allowed = {r"wcschr|wmemchr": "test", r"__abicheck_ctor__std::.*": "test"}
    assert (unexpected([_first_wins(keys)], {}, keys_allowed) == []) is allowed


def test_first_wins_allowlist_is_empty() -> None:
    """The benign duplicates are fixed at the source, not allowlisted."""
    assert unexpected([_first_wins("wcschr (×2)")], {}) != []

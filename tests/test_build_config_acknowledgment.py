"""``.abicheck.yml``'s ``acknowledgment:`` block: strict findings and the
lenient parse agree on every value.

Oracle: a value is valid exactly when ``file`` is a non-blank string or
absent, ``unacknowledged_additions`` is one of allow/warn/block or absent,
and no other key is present. The lenient parse keeps exactly the valid
fields, and the strict check reports a finding for every invalid one.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

from abicheck.buildsource.build_config_acknowledgment import (
    AcknowledgmentConfig,
    acknowledgment_block,
    acknowledgment_findings,
    parse_acknowledgment_config,
)

_FILES = [None, "acks.yml", "", "   ", 3, ["a"]]
_ACTIONS = [None, "allow", "warn", "block", "maybe", 1, ""]
_EXTRA = [False, True]


def _valid_file(v):
    return isinstance(v, str) and bool(v.strip())


def _valid_action(v):
    return v in ("allow", "warn", "block")


@pytest.mark.parametrize(
    ("path", "action", "extra"), list(itertools.product(_FILES, _ACTIONS, _EXTRA))
)
def test_findings_and_parse_agree(path, action, extra) -> None:
    block: dict[str, object] = {}
    if path is not None:
        block["file"] = path
    if action is not None:
        block["unacknowledged_additions"] = action
    if extra:
        block["records"] = "x"
    findings = acknowledgment_findings(block)
    expected = (
        (path is not None and not _valid_file(path))
        + (action is not None and not _valid_action(action))
        + extra
    )
    assert len(findings) == expected, findings
    parsed = parse_acknowledgment_config({"acknowledgment": block})
    kept_file = path if _valid_file(path) else None
    kept_action = action if _valid_action(action) else None
    if kept_file is None and kept_action is None:
        assert parsed is None
    else:
        assert parsed == AcknowledgmentConfig(
            file=kept_file, unacknowledged_additions=kept_action
        )
        assert acknowledgment_block(SimpleNamespace(acknowledgment=parsed)) == {
            k: v
            for k, v in (("file", kept_file), ("unacknowledged_additions", kept_action))
            if v is not None
        }


@pytest.mark.parametrize("value", ["block", 3, ["file"]])
def test_non_mapping_block(value) -> None:
    assert len(acknowledgment_findings(value)) == 1
    assert parse_acknowledgment_config({"acknowledgment": value}) is None


def test_absent_block() -> None:
    assert acknowledgment_findings(None) == []
    assert parse_acknowledgment_config({}) is None
    assert acknowledgment_block(SimpleNamespace(acknowledgment=None)) == {}

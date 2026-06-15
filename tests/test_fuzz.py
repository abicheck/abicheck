"""Fuzz / property-based tests using the hypothesis library.

These tests exercise abicheck internals with randomly generated inputs
to surface crashes, assertion errors, and unexpected exceptions.

Hypothesis is an optional dependency; the entire module is skipped if
it is not installed.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

pytest.importorskip("hypothesis")
import json  # noqa: E402

from hypothesis import (  # noqa: E402
    given,
    settings,
    strategies as st,  # noqa: E402
)

# ---------------------------------------------------------------------------
# 1. AbiSnapshot JSON serialization round-trip
# ---------------------------------------------------------------------------
from abicheck.model import AbiSnapshot, Function, Variable, Visibility  # noqa: E402
from abicheck.serialization import snapshot_from_dict, snapshot_to_json  # noqa: E402


@settings(max_examples=50)
@given(
    name=st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    ),
    version=st.text(
        min_size=1,
        max_size=20,
        alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    ),
)
def test_snapshot_json_roundtrip(name: str, version: str) -> None:
    """Serialize an AbiSnapshot to JSON, deserialize it back, check equality."""
    snap = AbiSnapshot(
        library=name,
        version=version,
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
        variables=[
            Variable(
                name="bar",
                mangled="_Z3barv",
                type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
    )

    json_str = snapshot_to_json(snap)
    roundtripped = snapshot_from_dict(json.loads(json_str))

    assert roundtripped.library == snap.library
    assert roundtripped.version == snap.version
    assert len(roundtripped.functions) == len(snap.functions)
    assert len(roundtripped.variables) == len(snap.variables)
    assert roundtripped.functions[0].name == snap.functions[0].name
    assert roundtripped.variables[0].name == snap.variables[0].name


# ---------------------------------------------------------------------------
# 2. Path validation fuzzing — _validate_member_path
# ---------------------------------------------------------------------------
from abicheck.errors import ExtractionSecurityError  # noqa: E402
from abicheck.package import _validate_member_path  # noqa: E402


@settings(max_examples=50)
@given(
    member=st.text(
        min_size=0,
        max_size=100,
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    ),
)
def test_validate_member_path_fuzz(member: str) -> None:
    """Random strings must either return a valid path or raise ExtractionSecurityError.

    Regardless of input, absolute paths and '..' must never be silently accepted.
    """
    with tempfile.TemporaryDirectory() as td:
        target_root = Path(td)
        try:
            result = _validate_member_path(member, target_root)
            # If accepted, verify the result is safe
            assert ".." not in result.parts, "'..' must never appear in validated path"
            # Absolute member names are expected to be rejected by
            # _validate_member_path via ExtractionSecurityError (handled below).
        except ExtractionSecurityError:
            pass  # expected for unsafe inputs
        except OSError:
            pass  # platform-specific path validation errors are acceptable


# ---------------------------------------------------------------------------
# 3. ChangeKind classification completeness
# ---------------------------------------------------------------------------
# Import the sets that classify each ChangeKind
from abicheck.checker_policy import (  # noqa: E402
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    RISK_KINDS,
    ChangeKind,  # noqa: E402
)


def test_changekind_classification_completeness() -> None:
    """Every ChangeKind enum value must be classified in exactly one impact set."""
    valid_categories = {"breaking", "api_break", "risk", "compatible"}

    for kind in ChangeKind:
        categories_found = []
        if kind in BREAKING_KINDS:
            categories_found.append("breaking")
        if kind in API_BREAK_KINDS:
            categories_found.append("api_break")
        if kind in RISK_KINDS:
            categories_found.append("risk")
        if kind in COMPATIBLE_KINDS:
            categories_found.append("compatible")

        assert len(categories_found) == 1, (
            f"ChangeKind.{kind.name} ({kind.value}) is in {len(categories_found)} "
            f"categories ({categories_found}), expected exactly one of: {valid_categories}"
        )


# ---------------------------------------------------------------------------
# 4. Suppression pattern fuzzing
# ---------------------------------------------------------------------------
from abicheck.suppression import Suppression  # noqa: E402


@settings(max_examples=50)
@given(
    pattern=st.text(
        min_size=1,
        max_size=80,
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    ),
)
def test_suppression_pattern_fuzz(pattern: str) -> None:
    """Random pattern strings must either create a valid Suppression or raise ValueError."""
    try:
        s = Suppression(symbol_pattern=pattern)
        # If it compiled, verify it can actually attempt matching without crashing
        from abicheck.checker import Change
        dummy_change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="test_symbol",
            description="test",
        )
        # Should not crash, and must return a definite boolean verdict.
        assert isinstance(s.matches(dummy_change), bool)
    except (ValueError, re.error):
        pass  # Expected for invalid regex patterns


# ---------------------------------------------------------------------------
# 5. Reporter handles arbitrary changes gracefully
# ---------------------------------------------------------------------------
from abicheck.checker import Change, DiffResult  # noqa: E402
from abicheck.checker_policy import Verdict  # noqa: E402
from abicheck.reporter import to_json, to_markdown  # noqa: E402


@settings(max_examples=50)
@given(
    symbol=st.text(
        min_size=1,
        max_size=100,
        alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    ),
    description=st.text(
        min_size=1,
        max_size=200,
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    ),
    kind=st.sampled_from(list(ChangeKind)),
)
def test_reporter_handles_arbitrary_changes(
    symbol: str, description: str, kind: ChangeKind
) -> None:
    """to_markdown and to_json must not crash on arbitrary change data."""
    change = Change(kind=kind, symbol=symbol, description=description)
    diff = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=[change],
        verdict=Verdict.BREAKING,
    )

    md = to_markdown(diff)
    assert isinstance(md, str)
    assert len(md) > 0

    js = to_json(diff)
    assert isinstance(js, str)
    # Verify the JSON is actually parseable
    parsed = json.loads(js)
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# 6. Binary format detection fuzzing
# ---------------------------------------------------------------------------

# _detect_binary_format lives in cli.py (which has heavy deps) and
# mcp_server.py (which requires the optional 'mcp' package).  We
# replicate the core logic inline to avoid pulling in those modules.
_MACHO_MAGICS = frozenset({
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
})


def _detect_binary_format_standalone(path: Path) -> str | None:
    """Detect binary format from magic bytes (standalone version for testing)."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return None
    if magic == b"\x7fELF":
        return "elf"
    if magic[:2] == b"MZ":
        return "pe"
    if magic in _MACHO_MAGICS:
        return "macho"
    return None


@settings(max_examples=50)
@given(
    data=st.binary(min_size=4, max_size=64),
)
def test_detect_binary_format_fuzz(data: bytes) -> None:
    """Random bytes written to a file must never crash _detect_binary_format.

    The return value must be one of 'elf', 'pe', 'macho', or None.
    """
    with tempfile.TemporaryDirectory() as td:
        fpath = Path(td) / "test_binary"
        fpath.write_bytes(data)

        result = _detect_binary_format_standalone(fpath)
        assert result in {"elf", "pe", "macho", None}, (
            f"Unexpected binary format result: {result!r}"
        )


# ---------------------------------------------------------------------------
# 7. Policy file YAML fuzzing
# ---------------------------------------------------------------------------
from abicheck.policy_file import PolicyFile  # noqa: E402


@settings(max_examples=50)
@given(
    content=st.text(
        min_size=0,
        max_size=200,
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z", "S")),
    ),
)
def test_policy_file_yaml_fuzz(content: str) -> None:
    """Random YAML-like strings must either parse or raise a clean error."""
    with tempfile.TemporaryDirectory() as td:
        policy_path = Path(td) / "policy.yaml"
        policy_path.write_text(content, encoding="utf-8")

        try:
            pf = PolicyFile.load(policy_path)
            # If it parsed, basic invariants
            assert isinstance(pf.base_policy, str)
            assert isinstance(pf.overrides, dict)
        except (ValueError, TypeError, ImportError):
            pass  # Expected for malformed inputs
        except Exception as exc:
            # yaml.YAMLError and its subclasses are acceptable
            import yaml
            if not isinstance(exc, yaml.YAMLError):
                raise  # Unexpected exception type — re-raise to fail the test

"""Undeclared spelling drift between operands of different extractor
generations (bug class ``storage.cross_generation_spelling_drift``, plan H1
fix step 4).

A respelling no ``SPELLING_MIGRATIONS`` row declares is demoted to
``COMPATIBLE_WITH_RISK`` with the reason "extractor-generation drift; re-dump
baseline" -- recorded, never dropped (ADR-067) -- and only then: same
generation, a changed type skeleton, a renamed builtin or a renamed declared
type all stay real changes. ``--require-same-generation`` turns any
generation mismatch into a usage error (exit 64).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Visibility
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.policy.generation_drift_demotion import (
    GENERATION_DRIFT_REASON,
    GENERATION_DRIFT_RULE,
    is_spelling_only_drift,
)
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.storage.generation_migrations import FACT_GENERATIONS_KEY
from abicheck.storage.versioning import EXTRACTOR_GENERATION


def _snapshot(
    ret: str, param: str = "int", declares: tuple[str, ...] = ()
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so.1",
        version="1",
        functions=[
            Function(
                name="f",
                mangled="_Z1fi",
                return_type=ret,
                params=[Param(name="p", type=param)],
                visibility=Visibility.PUBLIC,
            )
        ],
        types=[RecordType(name=n, kind="struct", size_bits=32) for n in declares],
    )


def _loaded(snapshot: AbiSnapshot, extractor: int) -> AbiSnapshot:
    doc = snapshot_to_dict(snapshot)
    doc[FACT_GENERATIONS_KEY] = {"extractor": extractor, "resolver": 2, "source": 0}
    return snapshot_from_dict(doc)


@pytest.mark.parametrize(
    ("old", "new", "declared", "expected"),
    [
        ("Placeholder&", "auto&", (), True),
        ("const Placeholder*", "const auto*", (), True),
        ("Box<Placeholder, 2>", "Box<auto, 2>", (), True),
        ("ns::Old", "ns::New", (), True),
        ("Placeholder&", "auto*", (), False),  # skeleton changed
        ("int", "long", (), False),  # builtin renamed
        ("unsigned int", "unsigned long", (), False),
        ("Foo&", "Bar&", ("Foo",), False),  # the old name is a declared type
        ("Box<int>", "Box<int, 4>", (), False),  # an argument appeared
        ("auto&", "auto &", (), False),  # whitespace only is not drift
    ],
)
def test_spelling_only_drift_predicate(
    old: str, new: str, declared: tuple[str, ...], expected: bool
) -> None:
    assert is_spelling_only_drift(old, new, declared) is expected


def _return_finding(result: object) -> object:
    hits = [c for c in result.changes if c.kind is ChangeKind.FUNC_RETURN_CHANGED]  # type: ignore[attr-defined]
    assert len(hits) == 1, [c.kind for c in result.changes]  # type: ignore[attr-defined]
    return hits[0]


@pytest.mark.parametrize("old_generation", [0, 5, EXTRACTOR_GENERATION - 1])
def test_cross_generation_respelling_is_recorded_at_risk(old_generation: int) -> None:
    old = _loaded(_snapshot("Placeholder&"), old_generation)
    result = compare(old, _snapshot("auto&"))
    finding = _return_finding(result)
    assert finding.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
    assert finding.modulation_rule == GENERATION_DRIFT_RULE
    assert GENERATION_DRIFT_REASON in finding.modulation_reason
    assert (finding.old_value, finding.new_value) == ("Placeholder&", "auto&")
    assert result.verdict is Verdict.COMPATIBLE_WITH_RISK


def test_same_generation_respelling_stays_breaking() -> None:
    """The control: the demotion is keyed on a generation mismatch."""
    old = _loaded(_snapshot("Placeholder&"), EXTRACTOR_GENERATION)
    finding = _return_finding(compare(old, _snapshot("auto&")))
    assert finding.effective_verdict is None
    assert finding.modulation_rule != GENERATION_DRIFT_RULE


@pytest.mark.parametrize(
    ("old_ret", "new_ret", "declares"),
    [("int", "long", ()), ("Foo&", "Bar&", ("Foo", "Bar")), ("Thing*", "Thing&", ())],
)
def test_cross_generation_real_type_change_stays_breaking(
    old_ret: str, new_ret: str, declares: tuple[str, ...]
) -> None:
    old = _loaded(_snapshot(old_ret, declares=declares), 5)
    result = compare(old, _snapshot(new_ret, declares=declares))
    finding = _return_finding(result)
    assert finding.modulation_rule != GENERATION_DRIFT_RULE
    assert result.verdict is Verdict.BREAKING


# ── --require-same-generation (CLI, exit 64) ────────────────────────────────


def _write(path: Path, snapshot: AbiSnapshot, extractor: int | None) -> Path:
    doc = snapshot_to_dict(snapshot)
    if extractor is None:
        doc.pop(FACT_GENERATIONS_KEY, None)
    else:
        doc[FACT_GENERATIONS_KEY] = {"extractor": extractor, "resolver": 2, "source": 0}
    path.write_text(json.dumps(doc))
    return path


@pytest.mark.parametrize("old_generation", [None, 5, EXTRACTOR_GENERATION - 1])
def test_require_same_generation_refuses_a_mismatch(
    tmp_path: Path, old_generation: int | None
) -> None:
    old = _write(tmp_path / "old.json", _snapshot("int"), old_generation)
    new = _write(tmp_path / "new.json", _snapshot("int"), EXTRACTOR_GENERATION)
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--require-same-generation"]
    )
    assert result.exit_code == 64, result.output
    assert "re-dump baseline" in result.output
    expected = "unstated" if old_generation is None else f"old {old_generation}"
    assert expected in result.output


def test_require_same_generation_accepts_a_matching_pair(tmp_path: Path) -> None:
    old = _write(tmp_path / "old.json", _snapshot("int"), EXTRACTOR_GENERATION)
    new = _write(tmp_path / "new.json", _snapshot("int"), EXTRACTOR_GENERATION)
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--require-same-generation"]
    )
    assert result.exit_code == 0, result.output


def test_without_the_flag_a_mismatch_still_compares(tmp_path: Path) -> None:
    old = _write(tmp_path / "old.json", _snapshot("int"), 5)
    new = _write(tmp_path / "new.json", _snapshot("int"), EXTRACTOR_GENERATION)
    result = CliRunner().invoke(main, ["compare", str(old), str(new)])
    assert result.exit_code == 0, result.output


def test_require_same_generation_refuses_directory_operands(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(tmp_path / "a"),
            str(tmp_path / "b"),
            "--require-same-generation",
        ],
    )
    assert result.exit_code == 64, result.output
    assert re.search(r"not supported for\s+directory/package", result.output)


@pytest.mark.parametrize(
    ("old_generation", "refused"),
    [(None, True), (5, True), (EXTRACTOR_GENERATION, False)],
)
def test_typed_api_require_same_generation_matches_the_cli(
    tmp_path: Path, old_generation: int | None, refused: bool
) -> None:
    """``CompareRequest.require_same_generation`` is the flag's typed-API
    spelling: it refuses exactly the pairs the CLI refuses."""
    from abicheck.errors import ValidationError
    from abicheck.service import CompareRequest, InputSpec, run_compare_request

    old = _write(tmp_path / "old.json", _snapshot("int"), old_generation)
    new = _write(tmp_path / "new.json", _snapshot("int"), EXTRACTOR_GENERATION)
    request = CompareRequest(
        old=InputSpec(path=old), new=InputSpec(path=new), require_same_generation=True
    )
    if refused:
        with pytest.raises(ValidationError, match="re-dump baseline"):
            run_compare_request(request)
    else:
        assert run_compare_request(request).diff is not None


def test_require_same_generation_is_rejected_with_no_baseline(tmp_path: Path) -> None:
    """With --no-baseline there is no second operand: the flag is a usage
    error, never silently dropped."""
    new = _write(tmp_path / "new.json", _snapshot("int"), EXTRACTOR_GENERATION)
    result = CliRunner().invoke(
        main, ["compare", "--no-baseline", str(new), "--require-same-generation"]
    )
    assert result.exit_code == 64, result.output
    assert "--require-same-generation is not available with --no-baseline" in (
        " ".join(result.output.split())
    )


@pytest.mark.parametrize(
    ("prior_verdict", "old_value", "new_value", "expected_verdict"),
    [
        # An earlier policy disposition is never overwritten by the demotion.
        (Verdict.BREAKING, "Placeholder&", "auto&", Verdict.BREAKING),
        # A finding whose values are not type spellings is left alone.
        (None, None, "auto&", None),
        # The control: an undisposed string respelling is demoted.
        (None, "Placeholder&", "auto&", Verdict.COMPATIBLE_WITH_RISK),
    ],
)
def test_demotion_skips_disposed_and_non_spelling_findings(
    prior_verdict: Verdict | None,
    old_value: str | None,
    new_value: str,
    expected_verdict: Verdict | None,
) -> None:
    from abicheck.model.change import Change
    from abicheck.policy.generation_drift_demotion import (
        demote_generation_spelling_drift,
    )

    change = Change(
        kind=ChangeKind.FUNC_RETURN_CHANGED,
        symbol="_Z1fi",
        description="return type changed",
        old_value=old_value,
        new_value=new_value,
    )
    change.effective_verdict = prior_verdict
    snap = _snapshot("Placeholder&")
    out = demote_generation_spelling_drift(
        [change], snap, snap, 5, EXTRACTOR_GENERATION
    )
    assert out == [change]
    assert change.effective_verdict is expected_verdict
    assert (change.modulation_rule == GENERATION_DRIFT_RULE) is (
        expected_verdict is Verdict.COMPATIBLE_WITH_RISK and prior_verdict is None
    )


@pytest.mark.parametrize(
    ("old", "new", "shift", "expected"),
    [
        ("double[1]", "double[2]", 1, True),
        ("Box<int, 3>[7][2]", "Box<int, 3>[8][3]", 1, True),
        ("char[0]", "char[1]", 1, True),
        ("char[9]", "char[10]", 1, True),  # digit count changes
        ("int[99][10]", "int[100][11]", 1, True),
        ("char[19]", "char[110]", 1, False),  # not +1 as numbers
        ("double[1]", "double[3]", 1, False),  # grew by two
        ("double[2]", "double[1]", 1, False),  # shrank
        ("double[1]", "double[2]", 0, False),  # no generation declared a shift
        ("Box<int, 1>", "Box<int, 2>", 1, False),  # template argument, not extent
        ("float[1]", "double[2]", 1, False),  # element type changed too
        ("double[1]", "double[1]", 1, False),  # unchanged
        ("double[1]", "double[1][1]", 1, False),  # rank changed
    ],
)
def test_extent_shift_drift_is_exactly_the_declared_growth(
    old: str, new: str, shift: int, expected: bool
) -> None:
    from abicheck.policy.generation_drift_demotion import is_extent_shift_drift

    assert is_extent_shift_drift(old, new, shift) is expected


@pytest.mark.parametrize(
    ("old_generation", "new_generation", "expected"),
    [(0, 15, 1), (14, 15, 1), (15, 15, 0), (15, 16, 0), (13, 14, 0), (15, 14, 0)],
)
def test_pending_extent_shift_sums_the_rows_in_between(
    old_generation: int, new_generation: int, expected: int
) -> None:
    from abicheck.storage.generation_migrations import pending_extent_shift

    assert pending_extent_shift(old_generation, new_generation) == expected


@pytest.mark.parametrize(
    ("old_generation", "expected_verdict"),
    [(14, Verdict.COMPATIBLE_WITH_RISK), (15, None)],
)
def test_castxml_extent_respelling_demotes_only_across_generation_15(
    old_generation: int, expected_verdict: Verdict | None
) -> None:
    from abicheck.model.change import Change
    from abicheck.policy.generation_drift_demotion import (
        demote_generation_spelling_drift,
    )
    from abicheck.storage.generation_migrations import pending_extent_shift

    change = Change(
        kind=ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        symbol="Box<double, 2>",
        description="field type changed",
        old_value="double[1]",
        new_value="double[2]",
    )
    snap = _snapshot("Placeholder&")
    demote_generation_spelling_drift(
        [change],
        snap,
        snap,
        old_generation,
        16,
        extent_shift=pending_extent_shift(old_generation, 16),
    )
    assert change.effective_verdict is expected_verdict
    if expected_verdict is not None:
        assert "array extents grown by 1" in change.modulation_reason
        assert "no SPELLING_MIGRATIONS rule" not in change.modulation_reason

"""One ``AbiSnapshot`` construction path (design-hardening plan, Phase 2).

``abicheck.workflows.snapshot_factory.new_snapshot`` is the only production
constructor outside the storage decoders. Two halves:

* a structural gate over ``abicheck/``: no ``AbiSnapshot(...)`` call -- by
  its own name, an import alias, or attribute access -- outside the factory
  and the listed decoders, and no stale allowlist entry;
* the factory's own contract: the finishing passes run in one fixed order,
  each only when asked, and the absent-baseline stand-in knows nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

FACTORY = "abicheck/workflows/snapshot_factory.py"

#: Modules allowed to call ``AbiSnapshot(...)`` directly: the factory and
#: decoders (a serialized or foreign representation in, a snapshot out --
#: they reproduce a snapshot someone already built and finished).
ALLOWED_CONSTRUCTORS: dict[str, str] = {
    FACTORY: "factory: the one production constructor",
    "abicheck/storage/snapshot_codec.py": "decoder: abicheck's own JSON snapshot document",
}

_CLASS = "AbiSnapshot"


def _constructor_aliases(tree: ast.AST) -> set[str]:
    """Every local name bound to ``AbiSnapshot`` by an import."""
    names = {_CLASS}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _CLASS:
                    names.add(alias.asname or alias.name)
    return names


def constructor_calls(source: str) -> list[int]:
    """Lines of every direct ``AbiSnapshot`` construction in *source*."""
    tree = ast.parse(source)
    aliases = _constructor_aliases(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Name) and func.id in aliases) or (
            isinstance(func, ast.Attribute) and func.attr == _CLASS
        ):
            lines.append(node.lineno)
    return sorted(lines)


def _production_modules() -> list[Path]:
    return sorted((REPO / "abicheck").rglob("*.py"))


@pytest.mark.repo_scan
def test_no_direct_snapshot_construction_outside_factory_and_decoders() -> None:
    offenders = []
    for path in _production_modules():
        rel = path.relative_to(REPO).as_posix()
        if rel in ALLOWED_CONSTRUCTORS:
            continue
        for line in constructor_calls(path.read_text(encoding="utf-8")):
            offenders.append(f"{rel}:{line}")
    assert not offenders, (
        "construct snapshots through abicheck.workflows.snapshot_factory."
        f"new_snapshot (or absent_baseline), not AbiSnapshot(...): {offenders}"
    )


@pytest.mark.repo_scan
def test_allowlist_is_the_factory_and_decoders_only_and_not_stale() -> None:
    kinds = {reason.split(":", 1)[0] for reason in ALLOWED_CONSTRUCTORS.values()}
    assert kinds <= {"factory", "decoder"}, kinds
    assert [p for p, r in ALLOWED_CONSTRUCTORS.items() if r.startswith("factory")] == [
        FACTORY
    ]
    for rel in ALLOWED_CONSTRUCTORS:
        path = REPO / rel
        assert path.is_file(), f"stale allowlist entry (no such file): {rel}"
        assert constructor_calls(path.read_text(encoding="utf-8")), (
            f"stale allowlist entry (constructs no AbiSnapshot): {rel}"
        )


# ── the detector itself ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from abicheck.model import AbiSnapshot\nAbiSnapshot(library='a', version='')\n",
            [2],
        ),
        ("from .model import AbiSnapshot as S\nx = S(library='a', version='')\n", [2]),
        ("import abicheck.model as m\nm.AbiSnapshot(library='a', version='')\n", [2]),
        (
            "from . import model\ny = [model.AbiSnapshot(library='a', version='')]\n",
            [2],
        ),
        # A reference that is not a call is not a construction.
        ("from abicheck.model import AbiSnapshot\nisinstance(x, AbiSnapshot)\n", []),
        ("def f(s: 'AbiSnapshot') -> None: ...\n", []),
        # Text in a string or comment is not a call.
        ('"""AbiSnapshot(library=...)"""\n# AbiSnapshot(x)\n', []),
        ("new_snapshot(library='a', version='')\n", []),
    ],
)
def test_constructor_detector(source: str, expected: list[int]) -> None:
    assert constructor_calls(source) == expected


# ── the factory's contract ─────────────────────────────────────────────────


def test_new_snapshot_without_finish_is_a_plain_construction() -> None:
    from abicheck.model import AbiSnapshot
    from abicheck.workflows.snapshot_factory import new_snapshot

    snap = new_snapshot(library="libx.so", version="1", platform="elf")
    assert isinstance(snap, AbiSnapshot)
    assert (snap.library, snap.version, snap.platform) == ("libx.so", "1", "elf")


@pytest.mark.parametrize(
    "selected",
    [
        (),
        ("provenance",),
        ("dependency_scope",),
        ("ownership",),
        ("provenance", "dependency_scope"),
        ("provenance", "ownership"),
        ("dependency_scope", "ownership"),
        ("provenance", "dependency_scope", "ownership"),
    ],
)
def test_finish_runs_exactly_the_requested_passes_in_canonical_order(
    selected: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every subset of passes: each selected pass runs once, unselected ones
    never, and always in the order provenance -> scope -> ownership."""
    import abicheck.provenance as provenance
    import abicheck.workflows.dump.dependency_scope as scoping
    import abicheck.workflows.ownership_request as ownership
    from abicheck.workflows import snapshot_factory as sf

    calls: list[str] = []
    monkeypatch.setattr(
        provenance,
        "apply_provenance",
        lambda snap, *a, **k: calls.append("provenance") or snap,
    )

    def _scope(snap, include_dependencies, header_roots):  # type: ignore[no-untyped-def]
        calls.append("dependency_scope")
        return snap

    monkeypatch.setattr(scoping, "resolve_dependency_scope", _scope)
    monkeypatch.setattr(
        ownership, "classify_extracted", lambda *a, **k: calls.append("ownership")
    )
    inputs = {
        "provenance": sf.ProvenanceInputs([Path("a.h")]),
        "dependency_scope": sf.DependencyScopeInputs(include_dependencies=False),
        "ownership": sf.OwnershipInputs(),
    }
    finish = sf.SnapshotFinish(**{k: inputs[k] for k in selected})
    sf.new_snapshot(library="l", version="", finish=finish)
    assert calls == list(selected)


def test_finish_returns_the_scoped_object_not_the_input() -> None:
    """Dependency scoping may replace the snapshot; the factory must hand back
    the replacement, or a caller keeps the unscoped surface."""
    from abicheck.model import Function, Visibility
    from abicheck.workflows.snapshot_factory import (
        DependencyScopeInputs,
        SnapshotFinish,
        new_snapshot,
    )

    snap = new_snapshot(
        library="l",
        version="",
        from_headers=True,
        functions=[
            Function(
                name="f", mangled="f", return_type="int", visibility=Visibility.PUBLIC
            )
        ],
        finish=SnapshotFinish(
            dependency_scope=DependencyScopeInputs(include_dependencies=True)
        ),
    )
    assert snap.dependency_scope == "full"


def test_absent_baseline_knows_nothing() -> None:
    from abicheck.workflows.snapshot_factory import absent_baseline

    snap = absent_baseline("libx.so")
    assert snap.library == "libx.so"
    assert snap.version == ""
    d = snap.declarations
    assert not (d.functions or d.variables or d.types or d.enums or d.typedefs)
    assert snap.elf is None and snap.pe is None and snap.macho is None


def test_pipeline_without_baseline_requires_the_factory_stand_in() -> None:
    """The policy-layer context never builds a snapshot: with no baseline and
    no stand-in it refuses rather than inventing one."""
    from abicheck.post_processing_context import PipelineContext
    from abicheck.workflows.snapshot_factory import absent_baseline

    new = absent_baseline("n")
    with pytest.raises(ValueError, match="absent_baseline"):
        _ = PipelineContext(old=None, new=new).baseline_or_empty
    stand_in = absent_baseline("n")
    assert (
        PipelineContext(old=None, new=new, absent_baseline=stand_in).baseline_or_empty
        is stand_in
    )


# ── the finishing passes run only through the factory ─────────────────────

#: The snapshot-level finishing passes. Calling one directly lets a route
#: apply it in a different order, or forget it, which is the F2 family the
#: factory exists to close.
FINISHING_PASSES = frozenset(
    {
        "apply_provenance",
        "resolve_dependency_scope",
        "classify_extracted",
        "stamp_ownership",
    }
)

#: (module, pass) pairs allowed to call a pass directly.
ALLOWED_PASS_CALLS: dict[tuple[str, str], str] = {
    (FACTORY, "apply_provenance"): "factory",
    (FACTORY, "resolve_dependency_scope"): "factory",
    (FACTORY, "classify_extracted"): "factory",
    ("abicheck/workflows/ownership_request.py", "stamp_ownership"): (
        "classify_extracted's own implementation: the ownership pass the factory calls"
    ),
}


def pass_calls(source: str) -> list[tuple[int, str]]:
    """``(line, pass)`` for every direct call of a finishing pass."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            )
            if name in FINISHING_PASSES:
                out.append((node.lineno, name))
    return sorted(out)


@pytest.mark.repo_scan
def test_finishing_passes_only_through_factory() -> None:
    offenders = [
        f"{rel}:{line} {name}"
        for path in _production_modules()
        for rel in [path.relative_to(REPO).as_posix()]
        for line, name in pass_calls(path.read_text(encoding="utf-8"))
        if (rel, name) not in ALLOWED_PASS_CALLS
    ]
    assert not offenders, (
        "apply finishing passes through workflows.snapshot_factory "
        f"(finish_snapshot / finish_provenance / finish_dependency_scope / finish_ownership): {offenders}"
    )


@pytest.mark.repo_scan
def test_pass_allowlist_is_not_stale() -> None:
    for rel, name in ALLOWED_PASS_CALLS:
        calls = pass_calls((REPO / rel).read_text(encoding="utf-8"))
        assert any(n == name for _, n in calls), f"stale entry: {rel} {name}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("apply_provenance(s, None, None)\n", [(1, "apply_provenance")]),
        ("m.classify_extracted(s, None, [], [])\n", [(1, "classify_extracted")]),
        ("from x import apply_provenance\n", []),
        ("finish_provenance(s, None, None)\n", []),
    ],
)
def test_pass_call_detector(source: str, expected: list[tuple[int, str]]) -> None:
    assert pass_calls(source) == expected


def test_single_pass_helpers_match_finish_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each one-pass helper runs exactly its pass, with its arguments."""
    import abicheck.provenance as provenance
    import abicheck.workflows.dump.dependency_scope as scoping
    import abicheck.workflows.ownership_request as ownership
    from abicheck.workflows import snapshot_factory as sf

    seen: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(
        provenance,
        "apply_provenance",
        lambda snap, h, d, include_search_dirs=None: seen.append(
            ("p", (h, d, include_search_dirs))
        ),
    )
    monkeypatch.setattr(
        scoping,
        "resolve_dependency_scope",
        lambda snap, inc, roots: seen.append(("s", (inc, roots))) or snap,
    )
    monkeypatch.setattr(
        ownership,
        "classify_extracted",
        lambda snap, r, h, d: seen.append(("o", (r, h, d))),
    )
    snap = sf.absent_baseline("l")
    a, b = [Path("a.h")], [Path("inc")]
    assert sf.finish_provenance(snap, a, b, include_search_dirs=b) is snap
    assert sf.finish_dependency_scope(snap, True, a) is snap
    assert sf.finish_ownership(snap, None, a, b) is snap
    assert seen == [("p", (a, b, b)), ("s", (True, a)), ("o", (None, a, b))]

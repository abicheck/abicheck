"""B5: Duplicate mangled symbols (abi-dumper #41).

When the same mangled name appears twice in castxml output (e.g. from
template instantiations in multiple translation units), abicheck must
handle deduplication deterministically.

Policy: first-wins — when building the snapshot index (function_map), the
first Function with a given mangled name takes precedence.

This is safe because:
1. Same mangled name → same function signature (by definition)
2. Linker would also deduplicate these at link time
3. Keeping first-seen is deterministic and auditable

abi-dumper #41: the original tool did not deduplicate and could produce
unstable diffs when template instantiations appeared in multiple TUs.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.report import diff_report as _reporter
from abicheck.serialization import load_snapshot, save_snapshot


def _func(
    name: str, mangled: str, return_type: str = "void", **kwargs: object
) -> Function:
    defaults: dict[str, object] = dict(visibility=Visibility.PUBLIC)
    defaults.update(kwargs)
    return Function(name=name, mangled=mangled, return_type=return_type, **defaults)  # type: ignore[arg-type]


class TestDuplicateMangledSymbols:
    """Verify deduplication behavior for duplicate mangled names."""

    def test_snapshot_index_first_wins(self) -> None:
        """When two functions share a mangled name, first one wins in function_map.

        Policy: first-wins (abi-dumper #41 fix). The first Function inserted
        with a given mangled name takes precedence; subsequent duplicates are
        skipped with a warning.
        """
        mangled = "_Z3foov"
        f1 = _func("foo", mangled, return_type="void")
        f2 = _func(
            "foo_alt", mangled, return_type="int"
        )  # same mangled, different name

        snap = AbiSnapshot(library="lib.so", version="1.0", functions=[f1, f2])
        # first-wins: f1 takes precedence
        indexed = snap.function_map[mangled]
        assert indexed.name == "foo"
        assert indexed.return_type == "void"

    def test_snapshot_index_deduplication_deterministic(self) -> None:
        """function_map must always return the same function for a given mangled name."""
        mangled = "_ZN3Bar4initEv"
        func_a = _func("Bar::init", mangled, return_type="bool")
        func_b = _func("Bar::init", mangled, return_type="bool")  # identical duplicate

        snap = AbiSnapshot(library="lib.so", version="1.0", functions=[func_a, func_b])
        # Both are the same — just verify no crash and consistent result
        result = snap.function_map[mangled]
        assert result.mangled == mangled

    def test_compare_with_duplicate_mangled_no_crash(self) -> None:
        """compare() must not crash when snapshots have duplicate mangled names."""
        mangled = "_Z9templateEv"
        f_old = _func("template_func", mangled, return_type="void")
        f_new = _func("template_func", mangled, return_type="void")

        old = AbiSnapshot(library="lib.so", version="1.0", functions=[f_old, f_old])
        new = AbiSnapshot(library="lib.so", version="2.0", functions=[f_new, f_new])

        # Must not raise
        result = compare(old, new)
        assert result.verdict == Verdict.NO_CHANGE

    def test_compare_duplicate_vs_single_no_false_positives(self) -> None:
        """Comparing snapshot with duplicate (v1) to single (v2) must not falsely
        report a removal if the function is still there."""
        mangled = "_Z4funcEv"
        f = _func("func", mangled, return_type="int")

        old = AbiSnapshot(
            library="lib.so", version="1.0", functions=[f, f]
        )  # duplicate
        new = AbiSnapshot(library="lib.so", version="2.0", functions=[f])  # single

        result = compare(old, new)
        # The function is still present — no removal
        removed = [c for c in result.changes if c.kind == ChangeKind.FUNC_REMOVED]
        assert not removed

    def test_duplicate_mangled_first_wins_for_comparison(self) -> None:
        """First-wins: when two functions share mangled name, first signature is used."""
        mangled = "_Z6updatev"
        f1_old = _func("update", mangled, return_type="void")  # first → wins
        f2_old = _func(
            "update_extra", mangled, return_type="int"
        )  # duplicate → skipped

        # New snapshot has return_type="void" — same as first (first-wins) in old
        f1_new = _func("update", mangled, return_type="void")

        old = AbiSnapshot(library="lib.so", version="1.0", functions=[f1_old, f2_old])
        new = AbiSnapshot(library="lib.so", version="2.0", functions=[f1_new])

        result = compare(old, new)
        # First-wins: old index has f1_old (return=void), new has f1_new (return=void)
        # → no return type change
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FUNC_RETURN_CHANGED not in kinds

    def test_dedup_documented_behavior(self) -> None:
        """Document first-wins deduplication: first inserted entry wins."""
        mangled = "_Z3bazv"
        first = _func("baz_first", mangled, return_type="void")
        second = _func("baz_second", mangled, return_type="int")

        snap = AbiSnapshot(library="lib.so", version="1.0", functions=[first, second])
        indexed = snap.function_map[mangled]
        # First inserted must win
        assert indexed.name == "baz_first"
        assert indexed.return_type == "void"

    def test_first_wins_semantics(self) -> None:
        """Explicit first-wins: first Function with a given mangled name is kept."""
        mangled = "_Z5firstEv"
        kept = _func("kept", mangled, return_type="int")
        dropped = _func("dropped", mangled, return_type="double")

        snap = AbiSnapshot(library="lib.so", version="1.0", functions=[kept, dropped])
        assert snap.function_map[mangled].name == "kept"

    def test_duplicate_logs_warning(self) -> None:
        """Inserting duplicate mangled symbol: first-wins and warning is logged."""
        import logging

        mangled = "_Z3dupv"
        f1 = _func("dup", mangled, return_type="void")
        f2 = _func("dup2", mangled, return_type="int")

        snap = AbiSnapshot(library="lib.so", version="1.0", functions=[f1, f2])

        # Capture WARNING from the module that owns index *construction*.
        # ADR-061 Phase 5 moved ``AbiSnapshot`` into
        # ``abicheck.model.snapshot``; schema v47 then moved the building of
        # the three first-wins maps (and these warnings) one step further
        # out, to ``abicheck.model.snapshot_index``. Still under the
        # ``abicheck.model`` hierarchy, so logging *configuration* keyed on
        # the package is unaffected, but an exact-name patch has to name the
        # real owner -- which is exactly why this test caught the move.
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
            logging.getLogger("abicheck.model.snapshot_index"), "warning"
        ) as mock_warn:
            _ = snap.function_map

        # Warning should have been emitted for the duplicate
        assert mock_warn.called
        call_args = mock_warn.call_args[0]
        assert mangled in call_args[1] or mangled in str(call_args)

        # First-wins
        assert snap.function_map[mangled].name == "dup"

    def test_variable_first_wins(self) -> None:
        """Variable deduplication: first-wins for duplicate mangled names."""
        from abicheck.model import Variable

        mangled = "_ZN3Foo3gVarE"
        v1 = Variable(name="Foo::gVar", mangled=mangled, type="int")
        v2 = Variable(name="Foo::gVar", mangled=mangled, type="double")  # duplicate

        snap = AbiSnapshot(library="lib.so", version="1.0", variables=[v1, v2])
        indexed = snap.variable_map[mangled]
        # First wins
        assert indexed.type == "int"

    def test_variable_dedup_consistent(self) -> None:
        """Variable deduplication must also be consistent."""
        from abicheck.model import Variable

        mangled = "_ZN3Foo3gVarE"
        v1 = Variable(name="Foo::gVar", mangled=mangled, type="int")
        v2 = Variable(name="Foo::gVar", mangled=mangled, type="double")  # duplicate

        snap = AbiSnapshot(library="lib.so", version="1.0", variables=[v1, v2])
        indexed = snap.variable_map[mangled]
        # Must be first one
        assert indexed.mangled == mangled
        assert indexed.type == "int"

    def test_index_is_idempotent_no_repeated_warning(self) -> None:
        """``index()`` must not re-log the duplicate-name warning on a second
        call for the same snapshot.

        Several detector modules (diff_cpp_patterns.py, diff_templates.py,
        diff_filtering.py, post_processing.py) call ``snap.index()`` directly
        rather than through the memoized ``function_map``/``variable_map``/
        ``type_by_name`` accessors — before this fix, ``index()`` itself
        unconditionally rebuilt all three maps (and re-logged every
        duplicate-name warning) on every call, so one compare/scan run with a
        genuine duplicate could log the identical warning repeatedly.
        """
        import logging

        from abicheck.model import RecordType

        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            types=[
                RecordType(name="Dup", kind="struct"),
                RecordType(name="Dup", kind="struct"),
            ],
        )
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
            logging.getLogger("abicheck.model.snapshot_index"), "warning"
        ) as mock_warn:
            snap.index()
            snap.index()
            snap.index()
        assert mock_warn.call_count == 1


# ---------------------------------------------------------------------------
# First-wins survives a stored roundtrip (the stored-operand form)
# ---------------------------------------------------------------------------
#
# The policy above is only safe if a *stored* snapshot keeps every duplicate in
# its original order: otherwise a baseline file could resolve a different
# winner than the live snapshot it was saved from, and the same comparison
# would report different findings depending on the operand form. The oracle
# is a plain first-occurrence scan over the declaration list, not the index.

_MANGLED_POOL = st.sampled_from(["_Z1av", "_Z1bv", "_Z1cv"])
_TYPES = st.sampled_from(["void", "int", "long", "char*"])


def _roundtrip(snap: AbiSnapshot) -> AbiSnapshot:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "snap.json"
        save_snapshot(snap, path)
        return load_snapshot(path)


def _first_by(items: list, key: str) -> dict:
    first: dict = {}
    for item in items:
        first.setdefault(getattr(item, key), item)
    return first


def _signature(entry: object) -> tuple:
    if isinstance(entry, Function):
        return ("f", entry.name, entry.return_type)
    if isinstance(entry, Variable):
        return ("v", entry.name, entry.type)
    assert isinstance(entry, RecordType)
    return ("t", entry.size_bits, tuple(f.type for f in entry.fields))


#: One side as plain data -- (functions, variables, types) of (name, key,
#: type) triples -- so Hypothesis can print a counterexample (its dataclass
#: pretty-printer trips over ``AbiSnapshot``'s retired ``functions`` field).
_Spec = tuple[
    tuple[tuple[str, str], ...],
    tuple[tuple[str, str], ...],
    tuple[tuple[str, int, str], ...],
]

_SPEC_ST = st.tuples(
    st.lists(st.tuples(_MANGLED_POOL, _TYPES), max_size=5).map(tuple),
    st.lists(st.tuples(st.sampled_from(["g1", "g2"]), _TYPES), max_size=4).map(tuple),
    st.lists(
        st.tuples(st.sampled_from(["S", "T"]), st.sampled_from([32, 64]), _TYPES),
        max_size=3,
    ).map(tuple),
)


def _colliding_snapshot(spec: _Spec, version: str) -> AbiSnapshot:
    functions, variables, types = spec
    return AbiSnapshot(
        library="libdup.so",
        version=version,
        functions=[
            _func(f"fn_{i}", mangled, return_type=ret)
            for i, (mangled, ret) in enumerate(functions)
        ],
        variables=[
            Variable(
                name=f"var_{i}", mangled=mangled, type=typ, visibility=Visibility.PUBLIC
            )
            for i, (mangled, typ) in enumerate(variables)
        ],
        types=[
            RecordType(
                name=name,
                kind="struct",
                size_bits=size,
                fields=[TypeField(name="x", type=typ, offset_bits=0)],
            )
            for name, size, typ in types
        ],
    )


def _winners(snap: AbiSnapshot) -> dict[str, tuple]:
    return {
        **{f"f:{k}": _signature(v) for k, v in snap.function_map.items()},
        **{f"v:{k}": _signature(v) for k, v in snap.variable_map.items()},
        **{
            f"t:{t.name}": _signature(snap.type_by_name(t.name))
            for t in snap.declarations.types
        },
    }


def _oracle_winners(snap: AbiSnapshot) -> dict[str, tuple]:
    decls = snap.declarations
    return {
        **{
            f"f:{k}": _signature(v)
            for k, v in _first_by(list(decls.functions), "mangled").items()
        },
        **{
            f"v:{k}": _signature(v)
            for k, v in _first_by(list(decls.variables), "mangled").items()
        },
        **{
            f"t:{k}": _signature(v)
            for k, v in _first_by(list(snap.declarations.types), "name").items()
        },
    }


class TestDuplicateMangledStoredRoundtrip:
    def test_conflicting_entries_keep_winner_and_findings_after_roundtrip(self) -> None:
        mangled = "_Z3foov"
        old = AbiSnapshot(
            library="lib.so",
            version="1.0",
            functions=[
                _func("foo", mangled, return_type="void"),
                _func("foo_alt", mangled, return_type="int"),
            ],
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2.0",
            functions=[_func("foo", mangled, return_type="int")],
        )
        stored_old = _roundtrip(old)

        assert stored_old.function_map[mangled].name == "foo"
        assert stored_old.function_map[mangled].return_type == "void"
        for a, b in ((old, new), (new, old)):
            live = compare(a, b)
            stored = compare(_roundtrip(a), _roundtrip(b))
            assert [(c.kind, c.symbol) for c in live.changes] == [
                (ChangeKind.FUNC_RETURN_CHANGED, mangled)
            ]
            assert json.loads(_reporter.to_json(stored)) == json.loads(
                _reporter.to_json(live)
            )
        # Both duplicates are kept in the stored document, in order.
        assert [f.name for f in stored_old.declarations.functions] == ["foo", "foo_alt"]

    @settings(
        max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    @given(old_spec=_SPEC_ST, new_spec=_SPEC_ST)
    def test_first_wins_is_stable_across_roundtrip(
        self, old_spec: _Spec, new_spec: _Spec
    ) -> None:
        old = _colliding_snapshot(old_spec, "1.0")
        new = _colliding_snapshot(new_spec, "2.0")
        stored_old, stored_new = _roundtrip(old), _roundtrip(new)
        for live, stored in ((old, stored_old), (new, stored_new)):
            assert _winners(live) == _oracle_winners(live)
            assert _winners(stored) == _winners(live)
        assert json.loads(
            _reporter.to_json(compare(stored_old, stored_new))
        ) == json.loads(_reporter.to_json(compare(old, new)))

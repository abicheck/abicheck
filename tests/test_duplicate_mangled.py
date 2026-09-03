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

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.model import AbiSnapshot, Function, Visibility


def _func(name: str, mangled: str, return_type: str = "void", **kwargs: object) -> Function:
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
        f2 = _func("foo_alt", mangled, return_type="int")  # same mangled, different name

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

        old = AbiSnapshot(library="lib.so", version="1.0", functions=[f, f])  # duplicate
        new = AbiSnapshot(library="lib.so", version="2.0", functions=[f])     # single

        result = compare(old, new)
        # The function is still present — no removal
        removed = [c for c in result.changes if c.kind == ChangeKind.FUNC_REMOVED]
        assert not removed

    def test_duplicate_mangled_first_wins_for_comparison(self) -> None:
        """First-wins: when two functions share mangled name, first signature is used."""
        mangled = "_Z6updatev"
        f1_old = _func("update", mangled, return_type="void")       # first → wins
        f2_old = _func("update_extra", mangled, return_type="int")  # duplicate → skipped

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

        # Capture WARNING from the module that owns ``index()``. ADR-061
        # Phase 5 moved ``AbiSnapshot`` into ``abicheck.model.snapshot``, so
        # its logger is that submodule's — still under the ``abicheck.model``
        # hierarchy, so logging *configuration* keyed on the package is
        # unaffected, but an exact-name patch has to name the real owner.
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
            logging.getLogger("abicheck.model.snapshot"), "warning"
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
            library="lib.so", version="1.0",
            types=[
                RecordType(name="Dup", kind="struct"),
                RecordType(name="Dup", kind="struct"),
            ],
        )
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
            logging.getLogger("abicheck.model.snapshot"), "warning"
        ) as mock_warn:
            snap.index()
            snap.index()
            snap.index()
        assert mock_warn.call_count == 1

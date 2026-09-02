# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""ADR-061 Phase 2 item 5: the markdown/text/review appends
``cli_compare_fold.py`` folds in after ``to_markdown``'s own whole-report
demangle pass must demangle their own content under ``--demangle`` too --
``_fold_scoped_compat_into_text`` (missing symbol/entrypoint names, scoped-
only change descriptions) and ``_fold_suppression_audit_into_text`` (a
suppression rule's own selector echo). Each is exercised through a real
``compare --required-symbol``/``--audit-suppressions`` CLI invocation
against pre-dumped JSON snapshot operands (the same pattern
``test_cli_compare_audit_suppressions.py`` uses), so this proves the whole
pipeline -- real ``demangle`` resolution, real fold-in wiring -- not just
the fold function in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# The real Itanium mangling of `api_b()` -- demangle_text must turn it into
# exactly that string.
_MANGLED = "_Z5api_bv"
_DEMANGLED = "api_b()"


def _fn(name: str, mangled: str, ret: str = "int") -> Function:
    return Function(
        name=name, mangled=mangled, return_type=ret, visibility=Visibility.PUBLIC
    )


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    old = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[_fn("api_b", _MANGLED)],
        from_headers=True,
    )
    new = AbiSnapshot(
        library="libfoo.so.1", version="2.0", functions=[], from_headers=True
    )
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def _invoke(*args: str):
    return CliRunner().invoke(main, list(args))


class TestScopedCompatFoldDemangle:
    """``_fold_scoped_compat_into_text``'s ``into_text`` append, via
    ``--required-symbol`` (no external appcompat binary needed, unlike
    ``--used-by``)."""

    def test_missing_entrypoint_demangled_by_default(self, tmp_path) -> None:
        old_p, new_p = _write_pair(tmp_path)
        result = _invoke(
            "compare", str(old_p), str(new_p), "--required-symbol", _MANGLED
        )
        assert "## Scoped to --required-symbol(s) contract" in result.output
        assert f"missing entrypoint: `{_DEMANGLED}`" in result.output
        assert f"missing entrypoint: `{_MANGLED}`" not in result.output

    def test_missing_entrypoint_stays_mangled_with_no_demangle(
        self, tmp_path
    ) -> None:
        old_p, new_p = _write_pair(tmp_path)
        result = _invoke(
            "compare", str(old_p), str(new_p),
            "--required-symbol", _MANGLED, "--no-demangle",
        )
        assert f"missing entrypoint: `{_MANGLED}`" in result.output
        assert f"missing entrypoint: `{_DEMANGLED}`" not in result.output

    def test_json_output_never_demangles(self, tmp_path) -> None:
        old_p, new_p = _write_pair(tmp_path)
        result = _invoke(
            "compare", str(old_p), str(new_p),
            "--required-symbol", _MANGLED, "--format", "json",
        )
        data = json.loads(result.stdout)
        assert data["required_symbol_contract"]["missing_entrypoints"] == [_MANGLED]


class TestSuppressionAuditFoldDemangle:
    """A rule's own :func:`suppression_rule_label` selector echo (the
    ``symbol=...`` part of a label) must never be demangled -- only the
    free-standing symbol prose this section separately adds (``audit.
    summary()``'s own text, a high-risk match's ``{kind}: {symbol}`` tail)
    is. Demangling a label would defeat its whole purpose: two distinct
    mangled selectors (e.g. Itanium C1/C2 constructor variants) can
    demangle to the identical display string, which would make two
    different rules indistinguishable in the report (Codex review)."""

    def test_high_risk_match_tail_demangled_but_label_stays_raw(
        self, tmp_path
    ) -> None:
        old_p, new_p = _write_pair(tmp_path)
        suppress = tmp_path / "suppress.yml"
        suppress.write_text(
            f"version: 1\nsuppressions:\n  - symbol: {_MANGLED}\n"
            "    reason: intentional removal\n",
            encoding="utf-8",
        )
        result = _invoke(
            "compare", str(old_p), str(new_p),
            "--suppress", str(suppress), "--audit-suppressions",
        )
        assert "## Suppression Audit" in result.output
        # The label's own selector echo is never demangled.
        assert f"`intentional removal (symbol={_MANGLED})`" in result.output
        assert f"(symbol={_DEMANGLED})" not in result.output
        # The trailing free-text symbol mention is demangled by default.
        assert f"suppressed func_removed: {_DEMANGLED}" in result.output
        assert f"suppressed func_removed: {_MANGLED}" not in result.output

    def test_high_risk_match_tail_stays_mangled_with_no_demangle(
        self, tmp_path
    ) -> None:
        old_p, new_p = _write_pair(tmp_path)
        suppress = tmp_path / "suppress.yml"
        suppress.write_text(
            f"version: 1\nsuppressions:\n  - symbol: {_MANGLED}\n"
            "    reason: intentional removal\n",
            encoding="utf-8",
        )
        result = _invoke(
            "compare", str(old_p), str(new_p),
            "--suppress", str(suppress), "--audit-suppressions", "--no-demangle",
        )
        assert f"suppressed func_removed: {_MANGLED}" in result.output
        assert f"suppressed func_removed: {_DEMANGLED}" not in result.output

    def test_colliding_demangled_names_stay_distinguishable(self, tmp_path) -> None:
        """Two suppression rules on distinct symbols that demangle to the
        *same* display string (Itanium C1/C2 constructor variants both
        read `Foo::Foo()`) must still produce two distinct labels in the
        demangled report -- proving the fix doesn't just avoid a crash but
        actually preserves disambiguation."""
        ctor1, ctor2 = "_ZN3FooC1Ev", "_ZN3FooC2Ev"
        old = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[
                _fn("Foo::Foo", ctor1, ret="void"),
                _fn("Foo::Foo", ctor2, ret="void"),
            ],
            from_headers=True,
        )
        new = AbiSnapshot(
            library="libfoo.so.1", version="2.0", functions=[], from_headers=True
        )
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")
        suppress = tmp_path / "suppress.yml"
        suppress.write_text(
            "version: 1\nsuppressions:\n"
            f"  - symbol: {ctor1}\n    reason: intentional removal\n"
            f"  - symbol: {ctor2}\n    reason: intentional removal\n",
            encoding="utf-8",
        )
        result = _invoke(
            "compare", str(old_p), str(new_p),
            "--suppress", str(suppress), "--audit-suppressions",
        )
        assert f"`intentional removal (symbol={ctor1})`" in result.output
        assert f"`intentional removal (symbol={ctor2})`" in result.output

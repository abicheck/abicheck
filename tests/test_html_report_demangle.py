"""HTML report C++ symbol demangling tests -- split out of
tests/test_sprint9_html.py to keep that module under the
architecture gate's per-test-module line cap (AGENTS.md's
`check_architecture.py` `new-test-size` check; Codex review)."""

from __future__ import annotations

from abicheck.html_report import generate_html_report

# ---------------------------------------------------------------------------
# Real demangling through the actual Change dataclass (item 8 of the abicheck
# code-review report: the HTML report never actually demangled anything --
# `_symbol_cell` read a `demangled_symbol` attribute that `Change` (the real
# production dataclass) never sets, so it always silently fell back to the
# raw mangled symbol; `description` was never touched at all. The tests
# above (`test_demangled_symbol_shown_as_text`/`test_mangled_symbol_in_
# tooltip`) never caught this because their `SimpleNamespace` fake
# hand-sets `demangled_symbol` itself, masking the gap.
# ---------------------------------------------------------------------------


class TestRealDemanglingThroughTheProductionChangeDataclass:
    def _elf_only_removal(self):
        from abicheck.checker import Change, DiffResult, Verdict
        from abicheck.checker_policy import ChangeKind

        mangled = "_ZN3FooC1Ev"
        change = Change(
            kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
            symbol=mangled,
            description=f"Elf_only function removed: {mangled}",
        )
        return DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=[change],
            verdict=Verdict.BREAKING,
        )

    def test_default_demangles_symbol_cell_for_a_real_change(self) -> None:
        out = generate_html_report(self._elf_only_removal())
        assert "Foo::Foo()" in out
        assert "_ZN3FooC1Ev" in out  # mangled kept as the <abbr> tooltip

    def test_default_demangles_the_description_too(self) -> None:
        """The gap `demangled_symbol` never covered at all: the
        Description column embeds the same mangled name inline."""
        out = generate_html_report(self._elf_only_removal())
        assert "Elf_only function removed: Foo::Foo()" in out

    def test_demangle_false_keeps_both_raw(self) -> None:
        out = generate_html_report(self._elf_only_removal(), demangle=False)
        assert "Foo::Foo()" not in out
        assert out.count("_ZN3FooC1Ev") >= 2  # symbol cell AND description

    def test_default_demangles_old_and_new_value(self) -> None:
        """Codex review: a finding carrying mangled names in `old_value`/
        `new_value` (as `buildsource/crosscheck.py` does) left those raw
        even under the default `--demangle`, since `_changes_table` only
        ever applied `demangle_text` to the description and primary
        symbol."""
        from abicheck.checker import Change, DiffResult, Verdict
        from abicheck.checker_policy import ChangeKind

        old_mangled, new_mangled = "_ZN3FooC1Ev", "_ZN3Bar3runEv"
        change = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED,
            symbol="widget",
            description="Return type changed",
            old_value=old_mangled,
            new_value=new_mangled,
        )
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=[change],
            verdict=Verdict.BREAKING,
        )
        out = generate_html_report(result)
        assert "Foo::Foo()" in out
        assert "Bar::run()" in out

    def test_old_and_new_value_preserve_mangled_identity(self) -> None:
        """Codex review, fresh evidence: a SOURCE_TO_BINARY_MAPPING_CHANGED
        finding changing between two ABI-distinct linker names that
        demangle identically (C1/C2 constructor variants, both
        `Foo::Foo()`) must not render both value cells as identical text
        with the exact linker names gone -- the row's own primary symbol
        is the source declaration label, not either mangled value, so
        there's no other tooltip to recover them from."""
        from abicheck.checker import Change, DiffResult, Verdict
        from abicheck.checker_policy import ChangeKind

        old_mangled, new_mangled = "_ZN3FooC1Ev", "_ZN3FooC2Ev"
        change = Change(
            kind=ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED,
            symbol="Foo::Foo",
            description="Source-to-binary mapping changed",
            old_value=old_mangled,
            new_value=new_mangled,
        )
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=[change],
            verdict=Verdict.BREAKING,
        )
        out = generate_html_report(result)
        assert out.count("Foo::Foo()") == 2
        assert '<abbr title="_ZN3FooC1Ev">Foo::Foo()</abbr>' in out
        assert '<abbr title="_ZN3FooC2Ev">Foo::Foo()</abbr>' in out

    def test_default_demangles_affected_symbols(self) -> None:
        """Codex review: a finding's `affected_symbols` list (as
        `diff_cpp_patterns.py` populates) was rendered raw, so the default
        HTML report could show a demangled Symbol column next to a still-
        mangled Affected list for the identical name."""
        from abicheck.checker import Change, DiffResult, Verdict
        from abicheck.checker_policy import ChangeKind

        affected = ["_ZN3FooC1Ev", "_ZN3Bar3runEv"]
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Widget",
            description="Type size changed",
            affected_symbols=affected,
        )
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=[change],
            verdict=Verdict.BREAKING,
        )
        out = generate_html_report(result)
        assert "Foo::Foo()" in out
        assert "Bar::run()" in out

    def test_affected_symbols_preserve_mangled_identity(self) -> None:
        """Codex review, fresh evidence: two ABI-distinct affected symbols
        that demangle identically (a class's C1/C2 constructor variants,
        both `Foo::Foo()`) must not collapse into indistinguishable text
        with the exact linker names gone -- especially since a grouped
        finding's own primary symbol can be a placeholder (`<isa:...>`)
        with no tooltip of its own to recover them from."""
        from abicheck.checker import Change, DiffResult, Verdict
        from abicheck.checker_policy import ChangeKind

        affected = ["_ZN3FooC1Ev", "_ZN3FooC2Ev"]
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="<isa:Widget>",
            description="Type size changed",
            affected_symbols=affected,
        )
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=[change],
            verdict=Verdict.BREAKING,
        )
        out = generate_html_report(result)
        assert out.count("Foo::Foo()") == 2
        assert '<abbr title="_ZN3FooC1Ev">Foo::Foo()</abbr>' in out
        assert '<abbr title="_ZN3FooC2Ev">Foo::Foo()</abbr>' in out

    def test_abbr_symbol_text_skips_redundant_abbr_when_demangled_equals_raw(
        self, monkeypatch
    ) -> None:
        """No real Itanium demangler ever produces output identical to its
        own mangled input, so _abbr_symbol_text's defensive guard against a
        redundant `<abbr title="X">X</abbr>` (identical tooltip and text)
        can only be exercised by forcing the demangler's return value
        directly (Codecov: this was this PR's one uncovered new line)."""
        import abicheck.html_report as html_report_mod
        import abicheck.report.render_html as render_html_mod

        # Patch the implementation owner (ADR-061 D10): the formatter moved
        # into `report/render_html.py` with the Phase 2 HTML compute/render
        # split; `html_report._abbr_symbol_text` is now an alias for it, so
        # patching the alias's old home would no longer reach the demangler
        # the function actually calls.
        monkeypatch.setattr(render_html_mod, "_demangle_symbol", lambda raw, **kw: raw)
        out = html_report_mod._abbr_symbol_text("safe_name")
        assert out == "safe_name"
        assert "<abbr" not in out

    def test_impact_summary_demangles_root_change_symbol(self) -> None:
        """Codex review, fresh evidence: the Impact Summary table
        (`--report-mode impact` / `show_impact=True`) rendered
        `change.symbol` directly via `html.escape`, bypassing the
        demangling setting entirely -- the normal change table right above
        it demangles the identical symbol, so the same root read as
        `Foo::Foo()` in one table and the raw `_ZN3FooC1Ev` in the other."""
        from abicheck.checker import Change, DiffResult, Verdict
        from abicheck.checker_policy import ChangeKind

        mangled = "_ZN3FooC1Ev"
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol=mangled,
            description="Type size changed",
            affected_symbols=["f1", "f2"],
        )
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=[change],
            verdict=Verdict.BREAKING,
        )
        out = generate_html_report(result, show_impact=True)
        assert "Impact Summary" in out
        # Scope the assertion to the Impact Summary section itself -- the
        # normal changes table above it renders the identical symbol
        # correctly, so asserting on the whole document would pass even
        # when only the impact table's own Root Change cell stays raw.
        impact_section = out[out.index("id='impact'") :]
        assert f'<abbr title="{mangled}">Foo::Foo()</abbr>' in impact_section

    def test_impact_summary_demangle_false_keeps_root_change_raw(self) -> None:
        from abicheck.checker import Change, DiffResult, Verdict
        from abicheck.checker_policy import ChangeKind

        mangled = "_ZN3FooC1Ev"
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol=mangled,
            description="Type size changed",
            affected_symbols=["f1", "f2"],
        )
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=[change],
            verdict=Verdict.BREAKING,
        )
        out = generate_html_report(result, show_impact=True, demangle=False)
        impact_section = out[out.index("id='impact'") :]
        assert "Foo::Foo()" not in impact_section
        assert mangled in impact_section

    def test_template_argument_angle_brackets_are_escaped_not_injected(self) -> None:
        """Demangling runs BEFORE html.escape, so a demangled template
        argument's own `<`/`>` must never appear unescaped in the output."""
        from abicheck.checker import Change, DiffResult, Verdict
        from abicheck.checker_policy import ChangeKind

        mangled = "_Z3fooI3BarEvT_"  # void foo<Bar>(Bar)
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=mangled,
            description=f"Function removed: {mangled}",
        )
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=[change],
            verdict=Verdict.BREAKING,
        )
        out = generate_html_report(result)
        assert "foo&lt;Bar&gt;" in out
        assert "<Bar>" not in out
        assert "foo<Bar>" not in out

    def test_non_mangled_symbol_containing_a_valid_mangled_substring_is_untouched(
        self,
    ) -> None:
        """Codex review, fresh evidence: a real, non-mangled export can
        legally contain a substring that is itself a complete, valid
        Itanium mangling (`_Z3foov` demangles to `foo()`) -- e.g. the legal
        C identifier `prefix_Z3foov`. A symbol/value cell must demangle the
        *whole* field, not scan for an embedded token the way free-form
        description prose deliberately still does (Codex review: "retaining
        embedded-token replacement only for prose descriptions"), or this
        renders as the bogus `prefixfoo()` with the real export name gone.
        Scoped to the symbol-cell helper directly, not the whole page,
        since the description field's own prose-scanning behavior is
        unaffected by this fix and out of scope for this assertion."""
        from abicheck.html_report import _abbr_symbol_text

        real_symbol = "prefix_Z3foov"
        out = _abbr_symbol_text(real_symbol)
        assert real_symbol in out
        assert "prefixfoo()" not in out

    def test_prewarms_the_demangle_cache_before_rendering_rows(
        self, monkeypatch
    ) -> None:
        """Codex review: without pre-warming, a report with many distinct
        C++ symbols would call demangle_text (and therefore demangle_batch)
        once per row, paying a fresh c++filt subprocess per row when the
        fast in-process cxxfilt package isn't installed. The first call to
        demangle_batch must already carry every symbol the report will
        render, proving the whole report was batched upfront rather than
        deferred to per-row calls."""
        import abicheck.demangle as demangle_mod
        from abicheck.checker import Change, DiffResult, Verdict
        from abicheck.checker_policy import ChangeKind

        symbols = ["_ZN3FooC1Ev", "_ZN3Bar3runEv", "_ZN3Baz3getEv"]
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol=s,
                description=f"Function removed: {s}",
            )
            for s in symbols
        ]
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=changes,
            verdict=Verdict.BREAKING,
        )

        calls: list[list[str]] = []
        orig = demangle_mod.demangle_batch

        def spy(batch: list[str], **kw) -> dict[str, str]:
            calls.append(list(batch))
            return orig(batch, **kw)

        monkeypatch.setattr(demangle_mod, "demangle_batch", spy)
        generate_html_report(result)
        assert calls, "demangle_batch was never called"
        assert set(symbols) <= set(calls[0]), calls

    def test_not_evaluated_table_demangles_the_symbol(self) -> None:
        """Codex review: the bespoke "Not Evaluated (Contract)" table
        (`_build_sections_html`) rendered `change.symbol` directly,
        bypassing `_changes_table`'s demangling entirely -- both the new
        default and an explicit `--demangle` left those symbols mangled."""
        from abicheck.checker import Change, DiffResult, Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.contract_relevance_types import CompatibilityEvaluationStatus

        mangled = "_ZN3FooC1Ev"
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=mangled,
            description=f"Function removed: {mangled}",
            compatibility_evaluation_status=CompatibilityEvaluationStatus.NOT_EVALUATED,
        )
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            changes=[change],
            verdict=Verdict.NO_CHANGE,
        )
        out = generate_html_report(result)
        assert "Not Evaluated (Contract)" in out
        assert "Foo::Foo()" in out
        assert '<abbr title="_ZN3FooC1Ev">Foo::Foo()</abbr>' in out

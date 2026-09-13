# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A retired report mode is refused at *every* public rendering boundary.

Sibling of `test_view_internal_grammar.py`, which owns the `--view` grammar
a user types. This file owns the Python-side contract underneath it: there
are five separately documented ways into the same documents
(`reporter.to_json`, `report.to_markdown`, `sarif.to_sarif`,
`junit_report.to_junit_xml`, and `service_render.render_envelope`, whose
mode arrives on the envelope rather than as an argument), and a retirement
enforced at some of them is not a retirement.

Separated because that is exactly how this kept going wrong: plan slice 7o
enforced it at one entry point, then three, then four, and each round a
reviewer found another way in. A file whose whole subject is "all of them"
makes the next addition obvious.
"""

from __future__ import annotations

import pytest


class TestEveryPublicRendererRejectsARetiredMode:
    """The retirement is enforced at *every* public rendering entry point,
    not at the ones somebody remembered.

    Centralizing the check (`report/report_modes.py`) was the fix; it did
    not by itself route `sarif.to_sarif` or `junit_report.to_junit_xml`
    through it, and both docstrings still promised that `leaf` "renders as
    full" (Codex review, PR #1284). Stated as a sweep over the entry points
    rather than one call each, so a renderer added later is covered by
    adding its name here and nothing else.
    """

    def _result(self):
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import DiffResult

        return DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )

    def _entry_points(self):
        from abicheck.junit_report import to_junit_xml
        from abicheck.report.dispatch_markdown import to_markdown
        from abicheck.reporter import to_json
        from abicheck.sarif import to_sarif

        return {
            "to_json": to_json,
            "to_markdown": to_markdown,
            "to_sarif": to_sarif,
            "to_junit_xml": to_junit_xml,
        }

    @pytest.mark.parametrize("mode", ["leaf", "not-a-mode", ""])
    def test_each_rejects_a_retired_or_unknown_mode(self, mode):
        from abicheck.errors import ValidationError

        result = self._result()
        for name, fn in self._entry_points().items():
            with pytest.raises(ValidationError):
                fn(result, report_mode=mode)
            assert name  # names the failing entry point in the traceback

    @pytest.mark.parametrize("mode", ["full", "impact", "root-cause"])
    def test_each_still_accepts_every_supported_mode(self, mode):
        """The other half: rejection must not have narrowed what works."""
        result = self._result()
        for name, fn in self._entry_points().items():
            assert fn(result, report_mode=mode) is not None, name

    def test_render_envelope_checks_the_envelope_s_own_mode(self):
        """`render_envelope` is a fifth public entry point, and the mode it
        renders lives on the envelope rather than in an argument.

        Nothing validated it: `build_report_envelope` and a directly
        constructed `ReportEnvelope` both accept any string, and HTML
        ignores the field entirely, so a retired mode rendered a page
        (CodeRabbit review, PR #1284)."""
        from abicheck.errors import ValidationError
        from abicheck.model import AbiSnapshot
        from abicheck.report.build import build_report_envelope
        from abicheck.report.envelope import RenderOptions
        from abicheck.service_render import render_envelope

        result = self._result()
        old, new = (
            AbiSnapshot(library="libfoo.so", version="1.0"),
            AbiSnapshot(library="libfoo.so", version="2.0"),
        )
        for mode in ("leaf", "not-a-mode"):
            envelope = build_report_envelope(
                result, old, new, options=RenderOptions(report_mode=mode)
            )
            for fmt in ("html", "json", "markdown"):
                with pytest.raises(ValidationError):
                    render_envelope(fmt, envelope)

    def test_render_envelope_still_renders_every_supported_mode(self):
        from abicheck.model import AbiSnapshot
        from abicheck.report.build import build_report_envelope
        from abicheck.report.envelope import RenderOptions
        from abicheck.service_render import render_envelope

        result = self._result()
        old, new = (
            AbiSnapshot(library="libfoo.so", version="1.0"),
            AbiSnapshot(library="libfoo.so", version="2.0"),
        )
        for mode in ("full", "impact", "root-cause"):
            envelope = build_report_envelope(
                result, old, new, options=RenderOptions(report_mode=mode)
            )
            assert render_envelope("json", envelope), mode

    def test_the_retired_message_names_the_replacement(self):
        from abicheck.errors import ValidationError
        from abicheck.reporter import to_json

        with pytest.raises(ValidationError, match="root-cause"):
            to_json(self._result(), report_mode="leaf")

    def test_to_junit_xml_multi_is_an_entry_point_too(self):
        """`to_junit_xml_multi` takes its own `report_mode` and reaches
        `_build_testsuite` directly, so validating its single-result sibling
        left it emitting a full document for a retired mode (Codex review,
        PR #1284). Its signature differs from the four above, which is
        precisely why the sweep missed it."""
        from abicheck.errors import ValidationError
        from abicheck.junit_report import to_junit_xml_multi
        from abicheck.model import AbiSnapshot

        pairs = [(self._result(), AbiSnapshot(library="libfoo.so", version="1.0"))]
        for mode in ("leaf", "not-a-mode", ""):
            with pytest.raises(ValidationError):
                to_junit_xml_multi(pairs, report_mode=mode)
        for mode in ("full", "impact", "root-cause"):
            assert to_junit_xml_multi(pairs, report_mode=mode), mode


class TestARenderedDocumentOnStdoutIsNeverPollutedByALedger:
    """A machine document written to stdout must parse, whatever ledgers the
    run discloses.

    Plan slice 7o made the pattern, suppression and public-surface-scope
    ledgers *unconditional*. They are human text and go to stderr, so a real
    shell's `-o json=- > report.json` is unaffected -- but "goes to stderr"
    is a property nothing asserted, and a single misrouted `click.echo`
    turns every one of those redirects into a syntax error at the consumer.
    The bug class is "a disclosure added to the terminal leaks into a
    document", not any one ledger, so this sweeps the ledger-triggering
    configurations against the machine formats rather than pinning the one
    case that surfaced it.
    """

    def _dirs(self, tmp_path):
        import json

        from abicheck.model import (
            AbiSnapshot,
            Function,
            RecordType,
            TypeField,
            Visibility,
        )
        from abicheck.serialization import save_snapshot

        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        def _rec(name, size):
            return RecordType(
                name=name,
                kind="struct",
                size_bits=size,
                fields=[TypeField(name="x", type="int")],
            )

        pub = Function(
            name="api_call",
            mangled="api_call",
            return_type="int",
            visibility=Visibility.PUBLIC,
        )
        gone = Function(
            name="internal_helper",
            mangled="internal_helper",
            return_type="int",
            visibility=Visibility.PUBLIC,
        )
        old = AbiSnapshot(
            library="libfoo.so",
            version="1",
            functions=[pub, gone],
            types=[_rec("Config", 32), _rec("InternalCache", 64)],
        )
        new = AbiSnapshot(
            library="libfoo.so",
            version="2",
            functions=[pub],
            types=[_rec("Config", 32), _rec("InternalCache", 128)],
        )
        for d, snap in ((old_dir, old), (new_dir, new)):
            save_snapshot(snap, d / "libfoo.json")
        assert json.loads((old_dir / "libfoo.json").read_text())
        return old_dir, new_dir

    def _run(self, args):
        from click.testing import CliRunner

        from abicheck.cli import main

        result = CliRunner().invoke(main, args)
        return result

    @pytest.mark.parametrize(
        "extra",
        [
            pytest.param([], id="no-ledger"),
            pytest.param(["--scope-public-headers"], id="scope-ledger"),
        ],
    )
    @pytest.mark.parametrize(
        ("cardinality", "fmt"),
        [
            # Both cardinalities, because they echo through different code:
            # the release fan-out captures each library's ledger and replays
            # it after the parallel futures complete, the scalar path echoes
            # inline. `sarif` is single-pair only by design.
            ("pair", "json"),
            ("pair", "sarif"),
            ("pair", "junit"),
            ("directory", "json"),
            ("directory", "junit"),
        ],
    )
    def test_stdout_parses_for_every_machine_format(
        self, tmp_path, cardinality, fmt, extra
    ):
        import json
        import xml.etree.ElementTree as ET

        old_dir, new_dir = self._dirs(tmp_path)
        if cardinality == "pair":
            operands = [str(old_dir / "libfoo.json"), str(new_dir / "libfoo.json")]
        else:
            operands = [str(old_dir), str(new_dir)]
        result = self._run(["compare", *operands, *extra, "-o", f"{fmt}=-"])
        assert result.exit_code in (0, 2, 4), result.output
        stdout = result.stdout
        assert stdout.strip(), f"{cardinality}/{fmt} wrote nothing to stdout"
        if fmt in ("json", "sarif"):
            json.loads(stdout)  # raises if a ledger was spliced in
        else:
            ET.fromstring(stdout)  # noqa: S314 - our own output

    def test_the_scope_ledger_really_is_disclosed_on_stderr(self, tmp_path):
        """The other half, and the reason this is not just "send it
        nowhere": a test that only asserts stdout parses is satisfied by
        deleting the disclosure, which is the ADR-067 regression the slice
        exists to prevent. So assert the same run *does* carry it, on the
        stream it belongs on."""
        old_dir, new_dir = self._dirs(tmp_path)
        result = self._run(
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--scope-public-headers",
                "-o",
                "json=-",
            ]
        )
        assert "non-public ABI surface" in result.stderr
        assert "non-public ABI surface" not in result.stdout


class TestTheReleaseReportCarriesItsDispositionLedgers:
    """ADR-067, the structured half, at package cardinality.

    A directory/package `compare` captured each library's suppression and
    public-surface-scope ledgers as *text* and echoed them to stderr, so the
    requested JSON artifact carried the counts but named neither the rule
    that fired nor the finding it disposed of -- a passing release report
    could hide every break in it, and a terminal log is not a report (Codex
    review, PR #1284). The fix reuses the scalar path's own two builders, so
    the claim worth pinning is *sameness of shape at both cardinalities*,
    not the presence of some release-flavoured key.
    """

    def _dirs(self, tmp_path):
        from abicheck.model import (
            AbiSnapshot,
            Function,
            RecordType,
            TypeField,
            Visibility,
        )
        from abicheck.serialization import save_snapshot

        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        def _rec(name, size):
            return RecordType(
                name=name,
                kind="struct",
                size_bits=size,
                fields=[TypeField(name="x", type="int")],
            )

        pub = Function(
            name="api_call",
            mangled="api_call",
            return_type="int",
            visibility=Visibility.PUBLIC,
        )
        old = AbiSnapshot(
            library="libfoo.so",
            version="1",
            functions=[pub],
            types=[_rec("Config", 32), _rec("InternalCache", 64)],
        )
        new = AbiSnapshot(
            library="libfoo.so",
            version="2",
            functions=[pub],
            types=[_rec("Config", 32), _rec("InternalCache", 128)],
        )
        for d, snap in ((old_dir, old), (new_dir, new)):
            save_snapshot(snap, d / "libfoo.json")
        return old_dir, new_dir

    def _json(self, args):
        import json

        from click.testing import CliRunner

        from abicheck.cli import main

        result = CliRunner().invoke(main, args)
        assert result.exit_code in (0, 2, 4), result.output
        return json.loads(result.stdout)

    def test_scope_ledger_is_in_the_release_document_not_only_on_stderr(self, tmp_path):
        old_dir, new_dir = self._dirs(tmp_path)
        doc = self._json(
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--scope-public-headers",
                "-o",
                "json=-",
            ]
        )
        lib = doc["libraries"][0]
        scope = lib["surface_scope"]
        assert scope["out_of_surface_count"] >= 1
        # The excluded finding itself, with the reason it was excluded --
        # the part a count alone cannot say.
        excluded = scope["out_of_surface_changes"]
        assert len(excluded) == scope["out_of_surface_count"]
        assert any(e["symbol"] == "InternalCache" for e in excluded)
        assert all(e.get("reason") for e in excluded)

    def test_the_release_block_has_the_same_shape_as_the_scalar_one(self, tmp_path):
        """Same builders, so the keys must agree exactly -- a release-only
        spelling of the same facts is the failure mode this guards."""
        old_dir, new_dir = self._dirs(tmp_path)
        release = self._json(
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--scope-public-headers",
                "-o",
                "json=-",
            ]
        )["libraries"][0]["surface_scope"]
        scalar = self._json(
            [
                "compare",
                str(old_dir / "libfoo.json"),
                str(new_dir / "libfoo.json"),
                "--scope-public-headers",
                "-o",
                "json=-",
            ]
        )["surface_scope"]
        assert set(release) == set(scalar)
        assert set(release["out_of_surface_changes"][0]) == set(
            scalar["out_of_surface_changes"][0]
        )

    def test_absent_when_the_setting_was_not_in_effect(self, tmp_path):
        """The "present only when active" convention the release schema's
        own 1.3 blocks follow, which is what keeps a release document
        produced without these settings unchanged.

        Note the flag has to be turned *off* explicitly: public-surface
        scoping is on by default for a directory/package comparison, so the
        scope block is legitimately present on a default run -- asserting
        otherwise tested the harness's premise rather than the code.
        """
        old_dir, new_dir = self._dirs(tmp_path)
        lib = self._json(
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--no-scope-public-headers",
                "-o",
                "json=-",
            ]
        )["libraries"][0]
        assert "surface_scope" not in lib
        # No `--suppress` document and nothing suppressed.
        assert "suppression" not in lib

    def test_present_on_a_default_release_run_because_scoping_is_the_default(
        self, tmp_path
    ):
        """The complement, stated so the previous test cannot be "fixed" by
        making the block conditional on something it should not be: a plain
        directory comparison scopes by default, so it discloses by default."""
        old_dir, new_dir = self._dirs(tmp_path)
        lib = self._json(["compare", str(old_dir), str(new_dir), "-o", "json=-"])[
            "libraries"
        ][0]
        assert lib["surface_scope"]["out_of_surface_changes"]


class TestASymbolIsNeverCorruptedByDemangling:
    """`demangle_text` scanned for mangled tokens with no *left* boundary, so
    a legitimate C or assembler export merely containing a mangled-looking
    suffix was rewritten into something that does not contain it:
    `my_Z3foov` rendered as `myfoo() [_Z3foov]` (Codex review, PR #1284).

    This slice is what makes it unavoidable rather than latent — retiring
    `--view no-demangle` means every human format applies the transformation
    — so the invariant worth pinning is not "this one input is handled" but
    *the original symbol always survives into the output*.
    """

    def _demangled(self, text):
        from abicheck.demangle import demangle_text

        return demangle_text(text)

    @pytest.mark.parametrize(
        "symbol",
        [
            "my_Z3foov",
            "x_Z3foov",
            "a__ZN3Foo3barEv",
            "prefix_Z3foov_suffix",
            "_my_Z3foov",
            "SOME_Z3foov",
        ],
    )
    def test_a_non_mangled_symbol_survives_verbatim(self, symbol):
        assert symbol in self._demangled(f"Removed: {symbol} from libfoo.so")

    @pytest.mark.parametrize(
        "symbol", ["_Z3foov", "__Z3foov", "_ZN3lib4goneEi", "_ZNK3Foo3barEv"]
    )
    def test_a_real_mangled_symbol_still_demangles_and_is_kept(self, symbol):
        """The other half — a boundary that also suppressed real demangling
        would pass the test above while destroying the feature."""
        out = self._demangled(f"Removed: {symbol} from libfoo.so")
        assert symbol in out, out
        assert out != f"Removed: {symbol} from libfoo.so", (
            f"{symbol} was not demangled at all"
        )

    def test_the_original_text_is_always_recoverable(self):
        """Stated as the general invariant rather than per input: whatever
        the transformation does, every token it consumed is still present."""
        from abicheck.demangle import extract_mangled_tokens

        for text in (
            "my_Z3foov and _Z3foov and a__Z3foov",
            "plain text with no symbols at all",
            "_ZN3lib4goneEi, my_ZN3lib4goneEi",
        ):
            out = self._demangled(text)
            for token in extract_mangled_tokens(text):
                assert token in out, (text, token, out)
            for word in text.replace(",", " ").split():
                assert (
                    word.strip(".,") in out.replace("(", " ").replace(")", " ")
                    or word in out
                ), (text, word, out)


class TestTheDispositionLedgersWarmTheirOwnDemangleCache:
    """Every row of both ledgers resolves a demangled symbol, so an unwarmed
    cache forks one `c++filt` per distinct C++ symbol on a host without the
    in-process `cxxfilt` package. The release fan-out reaches these ledgers
    *before* any `to_json`, whose own prewarm is therefore too late to help
    it (CodeRabbit review, PR #1284).

    Asserted by observing the mechanism rather than the output: a caller that
    quietly stopped warming would produce byte-identical reports.
    """

    def test_building_the_blocks_prewarms_once(self, monkeypatch):
        from abicheck import reporter
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import Change, DiffResult

        calls = []
        monkeypatch.setattr(
            reporter, "prewarm_change_demangling", lambda r: calls.append(r)
        )
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[
                Change(
                    kind=__import__(
                        "abicheck.checker_policy", fromlist=["ChangeKind"]
                    ).ChangeKind.FUNC_REMOVED,
                    symbol="_ZN3lib4goneEi",
                    description="removed",
                )
            ],
            verdict=Verdict.BREAKING,
        )
        reporter.disposition_ledger_blocks(result)
        assert len(calls) == 1, "the ledger builder must warm the cache exactly once"
        assert calls[0] is result


class TestTheDisposedFindingsTableSurvivesHostileCells:
    """Two independent ways a cell can break this table, and escaping the
    cells only covers one of them.

    A free-form suppression reason or a symbol can contain a raw `|`. And a
    symbol that contains none can *acquire* one: `_ZN3FooorERKS_` demangles to
    `Foo::operator|(Foo const&)`, and the release Markdown runs the demangle
    pass over the already-rendered document -- so a delimiter appears in a row
    that was correctly escaped when it was built (CodeRabbit review,
    PR #1284).

    The property is column count, not appearance: whatever the cell holds, a
    reader (and any consumer parsing the table) must still see five columns.
    """

    HEADER = "| Library | Disposition | Kind | Symbol | Rule / reason |"

    def _render(self, symbol, reason):
        from abicheck.cli_compare_release_helpers import _release_md_disposed_findings
        from abicheck.demangle import demangle_text

        lib = {
            "library": "libfoo.so",
            "suppression": {
                "suppressed_changes": [
                    {
                        "kind": "func_removed",
                        "symbol": symbol,
                        "rule": {"reason": reason},
                    }
                ]
            },
        }
        lines = _release_md_disposed_findings([lib])
        # The same whole-document pass `_format_release_markdown` applies.
        return demangle_text("\n".join(lines), escape_table_pipes=True)

    def _data_row(self, rendered):
        rows = [
            ln
            for ln in rendered.splitlines()
            if ln.startswith("|") and "---" not in ln and ln != self.HEADER
        ]
        assert len(rows) == 1, rendered
        return rows[0]

    def _columns(self, row):
        # Split on unescaped pipes only, the way a GFM parser does.
        import re

        inner = row.strip().strip("|")
        return re.split(r"(?<!\\)\|", inner)

    def test_a_baseline_row_has_five_columns(self):
        """Vacuity guard: fix the expected width against a benign row first,
        so the hostile cases below are compared to something real."""
        row = self._data_row(self._render("plain_symbol", "a plain reason"))
        assert len(self._columns(row)) == 5, row

    def test_a_raw_pipe_in_the_reason_does_not_add_a_column(self):
        row = self._data_row(self._render("plain_symbol", "broke | the table"))
        assert len(self._columns(row)) == 5, row

    def test_a_symbol_that_demangles_to_operator_pipe_keeps_its_columns(self):
        """The case escaping-before-rendering cannot catch: the pipe does not
        exist until the demangle pass runs over the finished document."""
        row = self._data_row(self._render("_ZN3FooorERKS_", "a plain reason"))
        assert "operator" in row, row
        assert len(self._columns(row)) == 5, row

    def test_a_newline_in_the_reason_does_not_add_a_row(self):
        rendered = self._render("plain_symbol", "first line\nsecond line")
        self._data_row(rendered)  # asserts exactly one data row


class TestDemanglingIsIdempotentAndReachesEveryHumanPath:
    """Two halves of the same promise, and one nearly broke the other.

    `to_markdown`'s `demangle` default stayed `False` because the CLI applies
    demangling once at the `service_render` boundary; flipping it made the
    native path demangle twice and render `bar() [bar() [_Z3barv]]`, because
    `demangle_text` annotates as `name [tok]` and used to re-annotate its own
    output. So: the function is now idempotent, *and* the two human paths that
    bypass `service_render` pass `demangle=True` explicitly (Codex review,
    PR #1284).
    """

    def test_demangle_text_is_idempotent(self):
        from abicheck.demangle import demangle_text

        for text in (
            "Removed: _Z3barv",
            "Removed: _ZN3FooorERKS_ and _ZN3lib4goneEi",
            "no symbols here",
        ):
            once = demangle_text(text)
            assert demangle_text(once) == once, text

    def test_the_native_markdown_path_demangles_exactly_once(self):
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import Change, DiffResult
        from abicheck.model import AbiSnapshot
        from abicheck.service import render_output

        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[
                Change(
                    kind=__import__(
                        "abicheck.checker_policy", fromlist=["ChangeKind"]
                    ).ChangeKind.FUNC_REMOVED,
                    symbol="_Z3barv",
                    description="removed",
                )
            ],
            verdict=Verdict.BREAKING,
        )
        md = render_output(
            "markdown", result, AbiSnapshot(library="libfoo.so", version="1.0")
        )
        assert "bar() [_Z3barv]" in md
        assert "bar() [bar()" not in md, "demangled twice"

    def test_the_two_bypassing_human_paths_ask_for_demangling(self):
        """`compat/cli.py` and `annotations_step_summary.py` never reach
        `service_render`, so nothing else would apply it for them."""
        import pathlib

        for rel in ("compat/cli.py", "annotations_step_summary.py"):
            src = pathlib.Path("abicheck", rel).read_text(encoding="utf-8")
            assert "demangle=True" in src, rel


class TestThePatternModulationLedgerRendersWhatTheProducerWrites:
    """Built from a real `PatternModulation`, never a hand-made dict.

    That distinction is the whole point of this class. The first version of
    this renderer read `m["rule"]`; the producer writes `rule_id`. A fixture
    invented alongside the renderer agreed with the renderer and passed, so
    every real report would have rendered `?` in the Rule column while the
    test stayed green (Codex review, PR #1284) — the same synthetic-object
    failure this PR already hit once.
    """

    def _modulation(self, **kw):
        from abicheck.pattern_verdicts import PatternModulation

        fields = {
            "symbol": "_ZN3lib4goneEi",
            "original_category": "abi_breaking",
            "new_category": "quality",
            "rule_id": "detail_ns_rule",
            "reason": "internal detail namespace",
            "evidence_tier": "header",
            "edges_matched": ("a->b",),
        }
        fields.update(kw)
        return PatternModulation(**fields)

    def _report(self, modulation):
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change, DiffResult
        from abicheck.report.dispatch_markdown import to_markdown

        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[
                Change(
                    kind=ChangeKind.FUNC_REMOVED,
                    symbol=modulation.symbol,
                    description="removed",
                )
            ],
            verdict=Verdict.BREAKING,
        )
        result.pattern_modulations = [modulation.to_dict()]
        return to_markdown(result, demangle=True)

    def _row(self, md, needle):
        rows = [ln for ln in md.splitlines() if needle in ln and ln.startswith("|")]
        assert len(rows) == 1, md
        return rows[0]

    def test_the_rule_that_fired_is_named(self):
        md = self._report(self._modulation())
        assert "detail_ns_rule" in md, md
        assert "| `?` |" not in md, "the renderer read a key the producer never writes"

    def test_the_reason_is_named(self):
        assert "internal detail namespace" in self._report(self._modulation())

    def test_every_key_the_renderer_reads_exists_on_the_producer(self):
        """States the contract directly, so a renamed producer field fails
        here rather than silently degrading to `?` in a report."""
        produced = self._modulation().to_dict()
        for key in ("symbol", "rule_id", "reason"):
            assert key in produced, (key, sorted(produced))

    def test_an_operator_symbol_keeps_the_table_intact(self):
        """`_ZN3FooorERKS_` demangles to `Foo::operator|(Foo const&)`, and the
        whole-document pass inserts that pipe after the row was built."""
        import re

        md = self._report(self._modulation(symbol="_ZN3FooorERKS_"))
        row = self._row(md, "detail_ns_rule")
        assert "operator" in row, row
        assert len(re.split(r"(?<!\\)\|", row.strip().strip("|"))) == 3, row

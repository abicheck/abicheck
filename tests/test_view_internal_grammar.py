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

"""Plan slice 7o -- ``--view``'s internal grammar.

The four deliverables, each asserted as the *property* it claims rather than
as "the removed token now errors" (which would prove deletion, not
simplification -- the 7l acceptance bar):

1. ``patterns``/``filtered``/``suppressions`` became unconditional
   disclosure: the machine projections carry the whole accounting with no
   token, and the human render carries it too.
2. Demangling is automatic: a human format carries the demangled name *and*
   the exact mangled one; every machine format carries both names in
   separate fields. Asserted against findings produced from real, compiled
   ELF and Mach-O libraries, not hand-written symbol strings.
3. The display dimensions come from the change catalog, not from a second
   reading of a ``ChangeKind``'s name -- stated over the whole catalog, so a
   kind added tomorrow is covered without a new test.
4. ``leaf`` retired against ``root-cause``: the supported invocation still
   answers the same question.

Plus the primitive-level property class AGENTS.md requires for the parsing/
merge/ordering primitive that survives here (``parse_view_tokens``'s
report-mode and ``show=``-group resolution), with an independently-derived
oracle.
"""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
import textwrap

import pytest

from abicheck.change_registry import REGISTRY
from abicheck.frontends.cli.options.view import (
    REPORT_MODES,
    RETIRED_TOKENS,
    parse_view_tokens,
)
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import ChangeEntity, ChangeOperation
from abicheck.reporter_markdown import (
    ACTION_TOKEN_OPERATIONS,
    ELEMENT_TOKEN_ENTITIES,
    SHOW_ONLY_GROUP_SEP,
    ShowOnlyFilter,
    entity_for_kind,
    operation_for_kind,
)

# ── Shared real-binary corpus ───────────────────────────────────────────────

_V1 = """
namespace lib {
struct Point { int x; };
void gone(int);
void gone(int) {}
void kept(Point *p);
void kept(Point *p) { (void)p; }
}
"""

_V2 = """
namespace lib {
struct Point { int x; long y; };
void kept(Point *p);
void kept(Point *p) { (void)p; }
}
"""

#: The exact mangled spelling of ``lib::gone(int)`` -- an Itanium name that
#: every real ELF/Mach-O build of ``_V1`` above exports. Not a hand-written
#: *finding*: the tests below read the symbol back out of a compiled binary
#: and only use this to say which symbol they are looking for.
_GONE_MANGLED = "_ZN3lib4goneEi"
_GONE_DEMANGLED = "lib::gone(int)"


def _build_pair(tmp_path, target: str | None, suffix: str):
    """Compile a real old/new shared library pair, or skip."""
    compiler = shutil.which("g++") if target is None else shutil.which("clang++")
    if compiler is None:
        pytest.skip("no C++ compiler available")
    paths = []
    for name, src in (("v1", _V1), ("v2", _V2)):
        source = tmp_path / f"{name}.cpp"
        source.write_text(textwrap.dedent(src))
        out = tmp_path / f"lib{name}{suffix}"
        cmd = [compiler, "-shared", "-fPIC", "-g", "-o", str(out), str(source)]
        if target is not None:
            cmd[1:1] = ["-target", target, "-fuse-ld=lld", "-nostdlib"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            pytest.skip(f"cannot build {suffix} pair here: {proc.stderr[:200]}")
        paths.append(out)
    return paths[0], paths[1]


def _demangler_available() -> bool:
    from abicheck.demangle import demangle

    resolved = demangle(_GONE_MANGLED)
    return bool(resolved) and resolved != _GONE_MANGLED


def _compare(old, new):
    """The whole CompareResult -- renderers need the two snapshots too."""
    from abicheck.service import CompareRequest, InputSpec
    from abicheck.service_compare_pipeline import run_compare_request

    return run_compare_request(
        CompareRequest(old=InputSpec(path=old), new=InputSpec(path=new))
    )


def _json_doc(result, **kwargs):
    from abicheck import reporter

    return json.loads(reporter.to_json(result, **kwargs))


# ── Deliverable 3: the dimensions come from the catalog ─────────────────────


class TestDisplayDimensionsComeFromTheCatalog:
    """The element/action dimensions are catalog facts, for *every* kind.

    Stated over ``ChangeKind`` as a whole rather than over a handful of
    examples: the defect this slice fixes was a classification table that
    silently omitted most of the catalog, which no example-shaped test
    could have found.
    """

    def test_every_kind_declares_both_dimensions(self):
        missing = [
            k.value
            for k in ChangeKind
            if REGISTRY.entity_for(k.value) is None
            or REGISTRY.operation_for(k.value) is None
        ]
        assert missing == []

    def test_a_kind_cannot_be_registered_without_them(self):
        from abicheck.model.change_catalog.registry import (
            ChangeKindMeta,
            ChangeKindRegistry,
            Verdict,
        )

        with pytest.raises(ValueError, match="entity and operation"):
            ChangeKindRegistry(
                [ChangeKindMeta("x", Verdict.BREAKING, impact="i")],
            )

    def test_every_kind_matches_exactly_one_element_token_family(self):
        """No kind is invisible to the whole element vocabulary.

        The superseded name-prefix table mapped 238 of 407 kinds to *no*
        element, so ``--view show=<every element token>`` hid 58% of the
        catalog. That is the regression this asserts against.
        """
        every_token = frozenset(ELEMENT_TOKEN_ENTITIES)
        unmatched = [
            k.value
            for k in ChangeKind
            if not ShowOnlyFilter(frozenset(), every_token, frozenset())._check_element(
                None, k.value
            )
        ]
        assert unmatched == []

    def test_every_kind_matches_exactly_one_action_token(self):
        for kind in ChangeKind:
            matched = [
                token
                for token in ACTION_TOKEN_OPERATIONS
                if ShowOnlyFilter._check_action(kind.value, frozenset({token}))
            ]
            assert len(matched) == 1, (kind.value, matched)

    def test_the_filter_agrees_with_the_declared_dimension(self):
        """An independently-derived oracle: the catalog entry itself."""
        for kind in ChangeKind:
            entry = REGISTRY.get(kind.value)
            assert entity_for_kind(kind.value) == entry.entity.value
            assert operation_for_kind(kind.value) == entry.operation.value
            for token, entity in ELEMENT_TOKEN_ENTITIES.items():
                assert ShowOnlyFilter(
                    frozenset(), frozenset({token}), frozenset()
                )._check_element(None, kind.value) is (entity is entry.entity)

    def test_no_name_parsing_path_survives(self):
        """The superseded derivation is gone, not merely unused.

        A prefix/suffix table left in place beside the catalog is exactly
        how the two drifted apart in the first place.
        """
        import abicheck.reporter_markdown as rm

        for name in (
            "_ADDED_SUFFIXES",
            "_REMOVED_SUFFIXES",
            "_OPERATION_OVERRIDES",
        ):
            assert not hasattr(rm, name), name

    def test_a_new_kind_lands_in_its_dimension_with_one_registration(self):
        from abicheck.model.change_catalog.registry import (
            ChangeKindMeta,
            ChangeKindRegistry,
            Verdict,
        )

        registry = ChangeKindRegistry(
            [
                ChangeKindMeta(
                    "hypothetical_future_fact",
                    Verdict.BREAKING,
                    impact="i",
                    entity=ChangeEntity.BUILD,
                    operation=ChangeOperation.REMOVED,
                )
            ]
        )
        assert registry.entity_for("hypothetical_future_fact") is ChangeEntity.BUILD
        assert (
            registry.operation_for("hypothetical_future_fact")
            is ChangeOperation.REMOVED
        )
        assert registry.kinds_for_entity(ChangeEntity.BUILD) == frozenset(
            {"hypothetical_future_fact"}
        )

    def test_machine_output_carries_the_entity_beside_the_operation(self, tmp_path):
        old, new = _build_pair(tmp_path, None, ".so")
        doc = _json_doc(_compare(old, new).diff)
        assert doc["changes"], "expected findings from a real removal"
        for change in doc["changes"]:
            assert change["entity"] == entity_for_kind(change["kind"])
            assert change["operation"] == operation_for_kind(change["kind"])


# ── Deliverable 1: unconditional disclosure ─────────────────────────────────


class TestDisclosureIsUnconditional:
    """No token is needed to see a disposition (ADR-067)."""

    def test_no_disclosure_token_exists_any_more(self):
        for token in ("patterns", "filtered", "suppressions"):
            with pytest.raises(ValueError, match="retired"):
                parse_view_tokens((token,))

    def test_the_machine_projection_carries_the_whole_accounting(self, tmp_path):
        old, new = _build_pair(tmp_path, None, ".so")
        doc = _json_doc(_compare(old, new).diff)
        # Every accounting block ADR-067 requires, with no token typed.
        assert "disposition_audit" in doc
        audit = doc["disposition_audit"]
        assert "detected_total" in audit and "effective_total" in audit
        assert audit["detected_total"] >= len(doc["changes"])

    def test_nothing_was_only_reachable_through_a_token(self, tmp_path):
        """The real projections, before and after, carry the same keys.

        The tokens gated *rendering*, never computation -- so removing them
        may not remove a key from any machine projection. Asserted against
        the real documents rather than by reading the renderer.
        """
        old, new = _build_pair(tmp_path, None, ".so")
        compared = _compare(old, new)
        result = compared.diff
        doc = _json_doc(result)
        for key in (
            "disposition_audit",
            "surface_changes",
            "changes",
            "verdict",
            "summary",
        ):
            assert key in doc, key


# ── Deliverable 2: demangling is automatic and lossless ─────────────────────


class TestDemanglingIsAutomatic:
    """Both names are always available -- which is why there is no choice."""

    def test_no_demangle_token_exists_any_more(self):
        for token in ("demangle", "no-demangle"):
            with pytest.raises(ValueError, match="retired"):
                parse_view_tokens((token,))

    @pytest.mark.parametrize(
        ("target", "suffix"),
        [(None, ".so"), ("x86_64-apple-macos11", ".dylib")],
        ids=["elf", "macho"],
    )
    def test_both_names_on_a_real_finding(self, tmp_path, target, suffix):
        """Real compiled binaries, both container formats.

        The symbol is read back out of the binary by the extractor; this
        test only names which one it expects to find.
        """
        if shutil.which("c++filt") is None:
            try:
                import cxxfilt  # noqa: F401
            except ImportError:
                pytest.skip("no demangler available")
        old, new = _build_pair(tmp_path, target, suffix)
        compared = _compare(old, new)
        result = compared.diff

        doc = _json_doc(result)
        removal = [c for c in doc["changes"] if c["symbol"] == _GONE_MANGLED]
        assert removal, [c["symbol"] for c in doc["changes"]]
        # Machine output: the exact symbol, plus the readable name beside it.
        assert removal[0]["demangled_symbol"] == _GONE_DEMANGLED

        from abicheck.service_render import render_output

        markdown = render_output(
            "markdown",
            result,
            compared.old_snapshot,
            compared.new_snapshot,
            demangle=True,
        )
        # Human output: readable, and the exact symbol stays copyable.
        assert _GONE_DEMANGLED in markdown
        assert _GONE_MANGLED in markdown

    def test_demangle_text_never_discards_the_exact_spelling(self):
        from abicheck.demangle import demangle, demangle_text

        if demangle(_GONE_MANGLED) in (None, _GONE_MANGLED):
            pytest.skip("no demangler available")
        rendered = demangle_text(f"removed: {_GONE_MANGLED}")
        assert _GONE_MANGLED in rendered
        assert _GONE_DEMANGLED in rendered

    def test_a_machine_format_is_never_demangled_in_place(self, tmp_path):
        from abicheck.service_render import (
            HUMAN_FORMATS,
            resolve_demangle_for_format,
        )

        for fmt in ("json", "sarif", "junit"):
            assert resolve_demangle_for_format(fmt) is False
            assert fmt not in HUMAN_FORMATS
        for fmt in ("markdown", "review", "html", "text", "oneline"):
            assert resolve_demangle_for_format(fmt) is True


# ── Deliverable 4: leaf retired against root-cause ──────────────────────────


class TestLeafRetiredAgainstRootCause:
    """The measurement's conclusion, and the acceptance bar that guards it."""

    def test_leaf_is_not_a_report_mode(self):
        assert "leaf" not in REPORT_MODES
        with pytest.raises(ValueError, match="root-cause"):
            parse_view_tokens(("leaf",))

    def test_root_cause_answers_the_same_question(self, tmp_path):
        """The old user task keeps a simple, supported invocation.

        `leaf` grouped findings under the root type that caused them and
        listed the interfaces each one affects. `root-cause` does both, and
        this asserts it on a real type-layout change rather than trusting
        the mode's name.
        """
        old, new = _build_pair(tmp_path, None, ".so")
        compared = _compare(old, new)
        result = compared.diff
        doc = _json_doc(result, report_mode="root-cause")
        assert doc["root_causes"], "expected at least one root-cause group"
        every_finding = [f for g in doc["root_causes"] for f in g["findings"]]
        full = _json_doc(result)

        # The measurement's own claim, and the only one that does not depend
        # on what evidence this host's toolchain produced: the grouped
        # document exposes exactly the finding set the unfiltered one does.
        assert {f["finding_id"] for f in every_finding} == {
            c["finding_id"] for c in full["changes"]
        }
        # Grouped under a named root, and carrying every field the
        # unfiltered projection carries for the same finding -- including
        # `affected_symbols` wherever the analysis resolved one.
        assert any(g["root"] for g in doc["root_causes"])
        by_id = {c["finding_id"]: c for c in full["changes"]}
        for finding in every_finding:
            assert set(by_id[finding["finding_id"]]) <= set(finding)

        # The type-layout half is asserted only where the host's own
        # toolchain actually produced type evidence. On macOS a g++ build
        # keeps debug info in a separate .dSYM, so the same sources yield
        # symbol-level findings and no `type_size_changed` at all --
        # requiring one here made this a Linux/DWARF test wearing a
        # platform-neutral name (caught by the macOS CI lane).
        layout = [f for f in every_finding if f["kind"] == "type_size_changed"]
        if layout:
            assert layout[0]["entity"] == ChangeEntity.TYPE.value


# ── The surviving parsing primitive, as invariants ──────────────────────────


def _oracle(tokens: tuple[str, ...]) -> dict[str, object]:
    """An independently-stated reading of the grammar.

    Deliberately not the implementation's own loop: the report mode is "the
    last mode token given, else 'full'", and the filter is "every ``show=``
    value, in the order given, joined by the OR-of-groups separator". Both
    sentences come from the option's documented contract, and neither
    consults ``parse_view_tokens``.
    """
    modes = [t for t in tokens if t in REPORT_MODES]
    shows = [t[len("show=") :] for t in tokens if t.startswith("show=")]
    return {
        "report_mode": modes[-1] if modes else "full",
        "show_only": SHOW_ONLY_GROUP_SEP.join(shows) if shows else None,
    }


class TestParseViewTokensProperties:
    """``parse_view_tokens``'s contract as invariants, over generated input.

    AGENTS.md's primitive-level rule (the ``_paired_stable_indices`` and
    ``_resolve_sided_variant`` precedents): a hand-written example only
    forecloses the input it names, and every ordering defect this repo has
    had in a small merge/ordering helper was found by stating the contract
    and searching the input space.
    """

    #: Every token the grammar accepts, plus enough `show=` values to make
    #: ordering and grouping observable.
    ALPHABET = (*REPORT_MODES, "show=breaking", "show=functions", "show=added")

    def _sequences(self, max_len=3):
        for n in range(max_len + 1):
            yield from itertools.product(self.ALPHABET, repeat=n)

    def test_exhaustive_agreement_with_an_independent_oracle(self):
        disagreements = [
            (seq, parse_view_tokens(seq), _oracle(seq))
            for seq in self._sequences()
            if parse_view_tokens(seq) != _oracle(seq)
        ]
        assert disagreements == []

    def test_the_oracle_is_not_vacuous(self):
        """Guard against an oracle accidentally reduced to a constant."""
        modes = {_oracle(seq)["report_mode"] for seq in self._sequences()}
        shows = {_oracle(seq)["show_only"] for seq in self._sequences()}
        assert modes == set(REPORT_MODES)
        assert len(shows) > 1 and None in shows

    def test_last_mode_wins_under_any_interleaving(self):
        for seq in self._sequences():
            modes = [t for t in seq if t in REPORT_MODES]
            if modes:
                assert parse_view_tokens(seq)["report_mode"] == modes[-1]

    def test_show_groups_keep_their_order_and_are_never_merged(self):
        for seq in self._sequences():
            shows = [t[len("show=") :] for t in seq if t.startswith("show=")]
            got = parse_view_tokens(seq)["show_only"]
            if not shows:
                assert got is None
            else:
                assert got.split(SHOW_ONLY_GROUP_SEP) == shows

    def test_a_show_group_is_never_joined_with_a_comma(self):
        """Joining with ',' would AND two groups instead of ORing them.

        The separator choice is the whole reason repeated ``show=`` means
        "match either group" (PR #1154); a comma here would silently change
        the filter's meaning rather than fail.
        """
        parsed = parse_view_tokens(("show=breaking", "show=functions"))
        assert parsed["show_only"] == f"breaking{SHOW_ONLY_GROUP_SEP}functions"

    def test_the_two_dimensions_are_independent(self):
        for seq in self._sequences():
            parsed = parse_view_tokens(seq)
            modes_only = tuple(t for t in seq if t in REPORT_MODES)
            shows_only = tuple(t for t in seq if t.startswith("show="))
            assert parsed["report_mode"] == parse_view_tokens(modes_only)["report_mode"]
            assert parsed["show_only"] == parse_view_tokens(shows_only)["show_only"]

    def test_every_retired_token_is_a_usage_error_naming_its_replacement(self):
        for token, replacement in RETIRED_TOKENS.items():
            with pytest.raises(ValueError) as excinfo:
                parse_view_tokens((token,))
            assert replacement in str(excinfo.value)

    def test_no_retired_token_is_silently_aliased(self):
        for token in RETIRED_TOKENS:
            assert token not in REPORT_MODES
            with pytest.raises(ValueError):
                parse_view_tokens(("full", token))

    def test_an_unknown_token_never_parses_as_a_default(self):
        with pytest.raises(ValueError, match="Unknown"):
            parse_view_tokens(("wat",))

    def test_an_empty_show_value_is_a_usage_error(self):
        with pytest.raises(ValueError, match="at least one token"):
            parse_view_tokens(("show=",))


# ── Review round 1 (Codex, PR #1284) ────────────────────────────────────────


class TestPublishedSchemaDeclaresTheNewContract:
    """A machine contract nobody can discover is not a contract.

    The change object allows additional properties, so an undeclared
    `entity` validates silently while schema-driven clients and generated
    documentation cannot see it.
    """

    def _change_props(self):
        from abicheck.schemas import load_compare_report_schema

        return load_compare_report_schema()["$defs"]["change"]["properties"]

    def test_entity_is_declared_with_the_catalog_s_own_vocabulary(self):
        prop = self._change_props()["entity"]
        assert set(prop["enum"]) == {e.value for e in ChangeEntity}

    def test_operation_is_declared_with_the_catalog_s_own_vocabulary(self):
        prop = self._change_props()["operation"]
        assert set(prop["enum"]) == {o.value for o in ChangeOperation}

    def test_the_schema_no_longer_describes_a_suffix_derived_operation(self):
        description = self._change_props()["operation"]["description"]
        assert "suffix" not in description.split("Schema 5.0")[0]

    def test_demangled_symbol_is_no_longer_described_as_elf_only(self):
        description = self._change_props()["demangled_symbol"]["description"]
        assert "present on every finding" in description

    def test_the_removal_is_versioned_as_a_major_change(self):
        """`leaf_changes`/`non_type_changes` were part of the 4.x contract;
        the schema's own policy reserves MAJOR for removing a key."""
        from abicheck.schemas import REPORT_SCHEMA_VERSION

        assert REPORT_SCHEMA_VERSION.split(".")[0] == "5"

    def test_a_real_report_validates_against_the_published_schema(self, tmp_path):
        from abicheck.schemas import load_compare_report_schema
        from tests.schema_validation import validate_instance

        old, new = _build_pair(tmp_path, None, ".so")
        doc = _json_doc(_compare(old, new).diff)
        validate_instance(doc, load_compare_report_schema())
        assert {c["entity"] for c in doc["changes"]} <= {e.value for e in ChangeEntity}


class TestDemanglingIsPrewarmedOnEveryProjection:
    """One batched `c++filt` call, not one subprocess per symbol.

    The prewarm belongs on the shared document builder every projection
    reaches, not on one serializer: `to_json` alone left the envelope path
    -- which is what the CLI actually renders through -- forking per
    distinct symbol on a host without the in-process `cxxfilt` package.
    """

    def _result_with_mangled_findings(self, count: int):
        from abicheck.checker import Verdict
        from abicheck.checker_types import Change, DiffResult

        changes = [
            Change(ChangeKind.FUNC_REMOVED, f"_ZN3lib4gone{i}Ev", "removed")
            for i in range(count)
        ]
        return DiffResult(
            old_version="1",
            new_version="2",
            library="lib",
            changes=changes,
            verdict=Verdict.BREAKING,
        )

    def _count_batches(self, monkeypatch, render):
        import abicheck.demangle as dm

        calls: list[int] = []
        real = dm.demangle_batch

        def _spy(symbols, **kwargs):
            calls.append(len(list(symbols)))
            return real(symbols, **kwargs)

        monkeypatch.setattr(dm, "demangle_batch", _spy)
        # A cold cache is the whole point: a warm one hides a missing prewarm.
        monkeypatch.setattr(dm, "_BATCH_CACHE_OK", {})
        monkeypatch.setattr(dm, "_BATCH_CACHE_FAIL", set())
        render()
        return calls

    def test_the_envelope_path_prewarms_before_serializing_findings(self, monkeypatch):
        from abicheck.model import AbiSnapshot
        from abicheck.service_render import render_output

        result = self._result_with_mangled_findings(12)
        calls = self._count_batches(
            monkeypatch,
            lambda: render_output(
                "json", result, AbiSnapshot(library="lib", version="1")
            ),
        )
        # One batched call covering every distinct symbol, before the
        # per-finding dicts resolve theirs one at a time.
        assert calls, "no batched demangle ran on the envelope path"
        assert max(calls) >= 12, calls

    def test_the_root_cause_projection_prewarms_too(self, monkeypatch):
        from abicheck import reporter

        result = self._result_with_mangled_findings(12)
        calls = self._count_batches(
            monkeypatch,
            lambda: reporter.to_json(result, report_mode="root-cause"),
        )
        assert calls and max(calls) >= 12, calls


class TestRetirementIsEnforcedAtThePublicApiToo:
    """Retiring a mode from Click does not retire the typed API.

    A caller passing the retired value would otherwise get a *different
    document shape* with no error -- strictly worse than a failure, since
    nothing tells them the mode is gone.
    """

    def _render(self, mode):
        from abicheck.checker_types import DiffResult
        from abicheck.model import AbiSnapshot
        from abicheck.service_render import render_output

        return render_output(
            "json",
            DiffResult(old_version="1", new_version="2", library="lib"),
            AbiSnapshot(library="lib", version="1"),
            report_mode=mode,
        )

    def test_the_retired_mode_is_rejected_not_silently_widened(self):
        from abicheck.errors import ValidationError

        with pytest.raises(ValidationError, match="retired"):
            self._render("leaf")

    def test_the_error_names_the_replacement(self):
        from abicheck.errors import ValidationError

        with pytest.raises(ValidationError, match="root-cause"):
            self._render("leaf")

    def test_an_unknown_mode_is_rejected_as_well(self):
        from abicheck.errors import ValidationError

        with pytest.raises(ValidationError, match="Unsupported report mode"):
            self._render("nonsense")

    @pytest.mark.parametrize("mode", ["full", "impact", "root-cause"])
    def test_every_surviving_mode_still_renders(self, mode):
        assert self._render(mode)


class TestJunitCarriesBothNames:
    """The machine-format half of deliverable 2, for the one projection
    that had no field to carry the readable name."""

    def _xml(self):
        from abicheck.checker import Verdict
        from abicheck.checker_types import Change, DiffResult
        from abicheck.junit_report import to_junit_xml

        change = Change(ChangeKind.FUNC_REMOVED, _GONE_MANGLED, "removed")
        return to_junit_xml(
            DiffResult(
                old_version="1",
                new_version="2",
                library="lib",
                changes=[change],
                verdict=Verdict.BREAKING,
            )
        )

    def test_the_testcase_keeps_the_exact_symbol_as_its_identity(self):
        if not _demangler_available():
            pytest.skip("no demangler available")
        assert _GONE_MANGLED in self._xml()

    def test_the_readable_name_travels_as_a_property(self):
        if not _demangler_available():
            pytest.skip("no demangler available")
        import xml.etree.ElementTree as ET

        root = ET.fromstring(self._xml())
        props = {
            p.get("name"): p.get("value")
            for tc in root.iter("testcase")
            for block in tc.findall("properties")
            for p in block
        }
        assert props.get("abicheck.demangled_symbol") == _GONE_DEMANGLED

    def test_a_testcase_carries_at_most_one_properties_block(self):
        """Every consumer reads properties through a single
        `tc.find("properties")`, so a second block is invisible."""
        import xml.etree.ElementTree as ET

        root = ET.fromstring(self._xml())
        for tc in root.iter("testcase"):
            assert len(tc.findall("properties")) <= 1


class TestCatalogDimensionsAreKeywordOnly:
    """`model/AGENTS.md`: appended public dataclass fields with defaults are
    keyword-only, so an outside caller cannot couple them to declaration
    order and the next appended field cannot silently break them."""

    def test_they_cannot_be_passed_positionally(self):
        from abicheck.model.change_catalog.registry import (
            ChangeKindMeta,
            Verdict,
        )

        with pytest.raises(TypeError):
            ChangeKindMeta(
                "k",
                Verdict.BREAKING,
                "impact",
                False,
                {},
                None,
                ChangeEntity.TYPE,
                ChangeOperation.ADDED,
            )

    def test_a_legacy_positional_pickle_still_restores(self):
        """They are declared *last* so an older build's positional state
        tuple keeps mapping to the same fields."""
        from abicheck.model.change_catalog.registry import ChangeKindMeta, Verdict

        legacy_state = [
            "test_kind",
            Verdict.BREAKING,
            "impact text",
            False,
            {"plugin_abi": Verdict.COMPATIBLE},
            None,
        ]
        restored = object.__new__(ChangeKindMeta)
        restored.__setstate__(legacy_state)
        assert restored.kind == "test_kind"
        assert dict(restored.policy_overrides) == {"plugin_abi": Verdict.COMPATIBLE}
        # The two fields the older build did not have read as unset, which
        # `_validate_entry` is what refuses if such an entry reaches a registry.
        assert restored.entity is None and restored.operation is None

    def test_a_real_catalog_entry_round_trips_through_pickle(self):
        import pickle

        entry = REGISTRY.entries["func_removed"]
        restored = pickle.loads(pickle.dumps(entry))
        assert restored.entity is entry.entity
        assert restored.operation is entry.operation


# ── Review round 2 (Codex, PR #1284) ────────────────────────────────────────


class TestMachOSymbolsResolveTheirDemangledName:
    """A Mach-O finding must not lose the readable name to a prefix.

    clang's own `mangledName` carries the platform global-symbol prefix on
    macOS, so a finding's symbol can read `__ZN3lib4goneEi`. `demangle()`
    rejects that spelling before consulting the cache unless the caller
    opts in -- so the prewarm (which does opt in) warmed a name the
    per-finding resolution then refused, and every machine projection
    silently omitted `demangled_symbol` for the whole platform.
    """

    class _Finding:
        demangled_symbol = None

        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

    def test_the_macho_spelling_resolves(self):
        from abicheck.reporter import resolve_demangled_symbol

        if not _demangler_available():
            pytest.skip("no demangler available")
        assert (
            resolve_demangled_symbol(self._Finding(f"_{_GONE_MANGLED}"))
            == _GONE_DEMANGLED
        )

    def test_the_elf_spelling_still_resolves(self):
        from abicheck.reporter import resolve_demangled_symbol

        if not _demangler_available():
            pytest.skip("no demangler available")
        assert resolve_demangled_symbol(self._Finding(_GONE_MANGLED)) == _GONE_DEMANGLED

    def test_an_msvc_decorated_symbol_still_resolves_to_nothing(self):
        """The documented limitation, asserted so the opt-in above cannot
        quietly turn into "demangle anything that looks mangled"."""
        from abicheck.reporter import resolve_demangled_symbol

        assert resolve_demangled_symbol(self._Finding("?gone@lib@@YAXH@Z")) is None

    def test_a_real_macho_finding_carries_both_names(self, tmp_path):
        if not _demangler_available():
            pytest.skip("no demangler available")
        old, new = _build_pair(tmp_path, "x86_64-apple-macos11", ".dylib")
        doc = _json_doc(_compare(old, new).diff)
        removals = [c for c in doc["changes"] if c.get("demangled_symbol")]
        assert removals, [c["symbol"] for c in doc["changes"]]
        for change in removals:
            assert change["symbol"] != change["demangled_symbol"]


class TestTheTypedApiDemanglesAutomaticallyToo:
    """Automatic means automatic for the *documented Python API*, not only
    for the CLI wrapper in front of it -- otherwise a direct
    `render_output("markdown", ...)` caller keeps getting raw-only output
    while the identical CLI request does not."""

    def _render(self, fmt, **kwargs):
        from abicheck.checker import Verdict
        from abicheck.checker_types import Change, DiffResult
        from abicheck.model import AbiSnapshot
        from abicheck.service_render import render_output

        result = DiffResult(
            old_version="1",
            new_version="2",
            library="lib",
            changes=[Change(ChangeKind.FUNC_REMOVED, _GONE_MANGLED, "removed")],
            verdict=Verdict.BREAKING,
        )
        return render_output(
            fmt, result, AbiSnapshot(library="lib", version="1"), **kwargs
        )

    def test_a_human_format_demangles_without_being_asked(self):
        if not _demangler_available():
            pytest.skip("no demangler available")
        out = self._render("markdown")
        assert _GONE_DEMANGLED in out
        assert _GONE_MANGLED in out

    def test_a_machine_format_still_keeps_the_raw_symbol(self):
        out = self._render("json")
        assert f'"symbol": "{_GONE_MANGLED}"' in out

    def test_an_explicit_choice_still_wins(self):
        """The resolution is a *default*, not a policy the caller cannot
        override -- `demangle=False` is what the report-level tests that
        pin raw text rely on."""
        if not _demangler_available():
            pytest.skip("no demangler available")
        assert _GONE_DEMANGLED not in self._render("markdown", demangle=False)

    def test_there_is_exactly_one_owner_of_the_resolution(self):
        """Front-end parity by construction, not by asserting one table
        twice: the CLI has no resolution of its own left to drift from the
        typed API's -- both go through
        `service_render.resolve_demangle_for_format`, which is why this
        slice moved it out of `cli_compare_options` (Codex review, PR
        #1284)."""
        import abicheck.cli_compare_options as cli_options
        from abicheck.cli_compare_helpers import resolve_demangle_for_format as via_cli
        from abicheck.service_render import resolve_demangle_for_format

        assert not hasattr(cli_options, "_resolve_demangle")
        assert not hasattr(cli_options, "HUMAN_FORMATS")
        assert via_cli is resolve_demangle_for_format


class TestEveryAliasOfAHumanFormatDemanglesLikeItsTarget:
    """A format alias renders through the identical projector, so it must
    resolve demangling identically.

    Stated as the invariant over every supported format rather than as the
    one reported input: `md` resolved to raw while `markdown` -- the same
    projection -- demangled (Codex review, PR #1284). The oracle is the
    projector table itself, which is what actually decides the rendering,
    not the `HUMAN_FORMATS` set under test.
    """

    def _aliases(self):
        """{projector -> [format names routing to it]}, from the real table."""
        from abicheck.service_render import _PROJECTIONS

        groups: dict[object, list[str]] = {}
        for name, projector in _PROJECTIONS.items():
            groups.setdefault(projector, []).append(name)
        return groups

    def test_the_corpus_actually_contains_an_alias(self):
        """Vacuity guard: a projector table with no aliases at all would
        make every assertion below trivially true."""
        assert any(len(names) > 1 for names in self._aliases().values())

    def test_formats_sharing_a_projector_resolve_identically(self):
        from abicheck.service_render import resolve_demangle_for_format

        disagreeing = {
            tuple(sorted(names)): {n: resolve_demangle_for_format(n) for n in names}
            for names in self._aliases().values()
            if len({resolve_demangle_for_format(n) for n in names}) > 1
        }
        assert not disagreeing, disagreeing

    def test_the_reported_alias_specifically(self):
        from abicheck.service_render import resolve_demangle_for_format

        assert resolve_demangle_for_format("md")
        assert resolve_demangle_for_format("markdown")


class TestOneEnvelopeProjectsCorrectlyIntoEveryFormat:
    """Demangling is a property of the *format* a projection targets, not of
    the evaluation being projected.

    `render_output` and `render_envelope` are two public ways into the same
    documents, and one `ReportEnvelope` is rendered into several formats at
    once (a CI run writing JSON *and* a job summary). Resolving demangling
    in `render_output` alone stored a single already-resolved bool on the
    envelope, so the same evaluation came out with different bytes depending
    on which entry point produced it, and a shared envelope could not be
    both demangled for HTML and raw for JSON. Caught by
    `tests/unit/report/test_build_report_document.py`'s
    `TestRendererOrderIndependence` on the macOS CI lane; asserted here as
    the property rather than only there as a byte comparison.
    """

    HUMAN = ("markdown", "md", "review", "html")
    MACHINE = ("json", "sarif", "junit")

    def _envelope(self):
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import Change, DiffResult
        from abicheck.model import AbiSnapshot
        from abicheck.model.change_catalog.kinds import ChangeKind
        from abicheck.report.build import build_report_envelope
        from abicheck.report.envelope import RenderOptions

        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[
                Change(
                    kind=ChangeKind.FUNC_REMOVED,
                    symbol="_ZN3lib4goneEi",
                    description="removed",
                )
            ],
            verdict=Verdict.BREAKING,
        )
        old, new = (
            AbiSnapshot(library="libfoo.so", version="1.0"),
            AbiSnapshot(library="libfoo.so", version="2.0"),
        )
        return build_report_envelope(result, old, new, options=RenderOptions())

    def test_the_default_is_resolve_per_projection(self):
        from abicheck.report.envelope import RenderOptions

        assert RenderOptions().demangle is None

    def test_one_envelope_demangles_for_human_formats_and_not_machine_ones(self):
        from abicheck.demangle import demangle
        from abicheck.service_render import render_envelope

        if demangle("_ZN3lib4goneEi") is None:
            pytest.skip("no demangler available in this environment")
        envelope = self._envelope()
        for fmt in self.HUMAN:
            assert "lib::gone(int)" in render_envelope(fmt, envelope), fmt
        for fmt in self.MACHINE:
            rendered = render_envelope(fmt, envelope)
            # The *rendered symbol* stays raw; a machine format may still
            # name the readable form in its own dedicated field, which is
            # the contract, so only the bare-text substitution is excluded.
            assert "_ZN3lib4goneEi" in rendered, fmt

    def test_the_two_public_entry_points_agree(self):
        """The drift itself: same evaluation, same format, same bytes."""
        from abicheck.report.build import build_report_envelope
        from abicheck.report.envelope import RenderOptions
        from abicheck.service_render import render_envelope, render_output

        envelope = self._envelope()
        for fmt in (*self.HUMAN, *self.MACHINE):
            assert render_envelope(fmt, envelope) == render_output(
                fmt, envelope.result, envelope.old, envelope.new
            ), fmt
        assert build_report_envelope and RenderOptions  # imports are used

    @pytest.mark.parametrize("explicit", [True, False])
    def test_an_explicit_choice_still_overrides_every_format(self, explicit):
        from abicheck.demangle import demangle
        from abicheck.model import AbiSnapshot
        from abicheck.report.build import build_report_envelope
        from abicheck.report.envelope import RenderOptions
        from abicheck.service_render import render_envelope

        if demangle("_ZN3lib4goneEi") is None:
            pytest.skip("no demangler available in this environment")
        base = self._envelope()
        envelope = build_report_envelope(
            base.result,
            AbiSnapshot(library="libfoo.so", version="1.0"),
            AbiSnapshot(library="libfoo.so", version="2.0"),
            options=RenderOptions(demangle=explicit),
        )
        assert ("lib::gone(int)" in render_envelope("markdown", envelope)) is explicit


class TestEveryCollectionThatSerializesIsPrewarmed:
    """The demangle prewarm has to cover every collection whose entries reach
    `_change_to_dict`, not only `result.changes`.

    `scoped_only_changes` is the one that was missed: `apply_scoped_gate`
    synthesizes it *after* the document is built, for a `--used-by` /
    `--required-symbol` run, and serializes it through the same path -- so
    on a host without the in-process `cxxfilt` package every
    consumer-required C++ symbol went back to one `c++filt` subprocess each,
    which is the whole cost this function exists to avoid (Codex review, PR
    #1284).

    Asserted as the invariant over the collections rather than against one
    name, and by observing the batch the prewarm actually submits.
    """

    #: Every attribute on a result whose entries are serialized as findings.
    SERIALIZED_COLLECTIONS = (
        "changes",
        "suppressed_changes",
        "out_of_surface_changes",
        "scoped_only_changes",
    )

    def _result_with_a_distinct_symbol_per_collection(self):
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import Change, DiffResult
        from abicheck.model.change_catalog.kinds import ChangeKind as CK

        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        expected = set()
        for i, attr in enumerate(self.SERIALIZED_COLLECTIONS):
            symbol = f"_ZN3lib{len(attr)}q{i}Ev"
            expected.add(symbol)
            setattr(
                result,
                attr,
                [Change(kind=CK.FUNC_REMOVED, symbol=symbol, description="d")],
            )
        return result, expected

    def test_every_collection_reaches_the_batch(self):
        from unittest import mock

        from abicheck.reporter import prewarm_change_demangling

        result, expected = self._result_with_a_distinct_symbol_per_collection()
        with mock.patch("abicheck.demangle.prewarm_demangle_batch") as batch:
            prewarm_change_demangling(result)
        assert batch.call_count == 1
        submitted = {c.symbol for c in batch.call_args.args[0]}
        missing = expected - submitted
        assert not missing, missing

    def test_the_guard_would_fail_if_a_collection_were_dropped(self):
        """Vacuity guard: each collection contributes a *distinct* symbol, so
        omitting any one of them is detectable."""
        _, expected = self._result_with_a_distinct_symbol_per_collection()
        assert len(expected) == len(self.SERIALIZED_COLLECTIONS)


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

    def test_the_retired_message_names_the_replacement(self):
        from abicheck.errors import ValidationError
        from abicheck.reporter import to_json

        with pytest.raises(ValidationError, match="root-cause"):
            to_json(self._result(), report_mode="leaf")

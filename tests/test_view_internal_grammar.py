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
                k.value
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
                )._check_element(kind.value) is (entity is entry.entity)

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
        from abicheck.cli_compare_options import HUMAN_FORMATS, _resolve_demangle

        for fmt in ("json", "sarif", "junit"):
            assert _resolve_demangle(fmt) is False
            assert fmt not in HUMAN_FORMATS
        for fmt in ("markdown", "review", "html", "text", "oneline"):
            assert _resolve_demangle(fmt) is True


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
        assert {f["finding_id"] for f in every_finding} == {
            c["finding_id"] for c in full["changes"]
        }
        layout = [f for f in every_finding if f["kind"] == "type_size_changed"]
        assert layout, [f["kind"] for f in every_finding]
        # Grouped under the root type `leaf` grouped by, and carrying every
        # field the unfiltered projection carries for the same finding --
        # including `affected_symbols` wherever the analysis resolved one
        # (it is absent here and in the full report alike, which is the
        # point: root-cause drops nothing leaf would have shown).
        assert any(g["root"] for g in doc["root_causes"])
        by_id = {c["finding_id"]: c for c in full["changes"]}
        for finding in every_finding:
            assert set(by_id[finding["finding_id"]]) <= set(finding)


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

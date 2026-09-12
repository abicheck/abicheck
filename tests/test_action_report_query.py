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

"""``action/report_query.py``: the Action's JSON-report reader.

Two things are tested here, and they are different in kind.

**The extraction is behavior-preserving.** The ~300 lines of query semantics
this module holds were a heredoc inside ``action/run.sh``. Moving them must
not change a single answer, and "I reread both and they look the same" is not
evidence of that — the heredoc was unreachable from pytest for its whole life
precisely because nobody could assert against it. So the answers are pinned
against an independently-stated oracle over generated documents, including
the malformed shapes where Python's own ``==`` disagrees with the string
comparison ``run.sh`` performs (``True``/``"1"``).

**The truthfulness fix.** ``report_validity`` and ``assurance_axis`` are new,
and they exist to close one defect: every axis query deliberately answers
"not gated" for a report it cannot read (ADR-063 Track T8 chose that over
reconstructing an axis from forgeable stderr prose), and the exit-0 dispatch
was reading that as "the axis passed" — publishing ``COMPATIBLE`` for a run
that established nothing. The invariant is stated over *generated* invalid
documents rather than a handful of fixtures, because the reported instance
was one shape (``{}``) out of a class with at least six.

Bug class: ``report.unestablished_result_reads_as_success``
(``tests/regressions/manifest.py``).
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ACTION_DIR = Path(__file__).resolve().parents[1] / "action"
READER = ACTION_DIR / "report_query.py"


def _load_reader():
    spec = importlib.util.spec_from_file_location("_abicheck_report_query", READER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rq = _load_reader()


def _ask(
    tmp_path: Path, document: object, query: str, arg: str = ""
) -> tuple[int, str]:
    """Invoke the reader the way ``run.sh`` does: as a subprocess, by path.

    Deliberately not an in-process ``main()`` call: the contract ``run.sh``
    depends on is the *process* exit code plus stdout, and an in-process call
    cannot observe a traceback leaking to stderr or an interpreter-level
    failure. The unit-level tests below call ``answer``/``classify_document``
    directly where the question is about pure logic.
    """
    report = tmp_path / "report.json"
    if isinstance(document, (bytes, bytearray)):
        report.write_bytes(document)
    elif document is None:
        report.unlink(missing_ok=True)
    else:
        report.write_text(json.dumps(document), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-I", str(READER), str(report), query, arg],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout


#: Every query name ``run.sh`` asks for. Kept as an explicit list rather than
#: scraped from the module, so a query silently disappearing from the reader
#: fails here instead of shrinking the matrix it is checked over.
QUERIES = (
    "report_validity",
    "assurance_axis",
    "no_baseline_audit",
    "coverage_contribution",
    "assurance_contribution",
    "severity_exit",
    "compat_verdict",
    "blocking_categories",
    "coverage_where",
    "scope_contribution",
    "scope_incomplete",
    "scope_where",
    "assurance_notes",
    "run_outcome",
    "annotations",
)


class TestRunShCallSurfaceIsComplete:
    """Every query ``run.sh`` asks for is one the reader answers, and vice versa."""

    def test_every_query_run_sh_asks_is_answered(self) -> None:
        run_sh = (ACTION_DIR / "run.sh").read_text(encoding="utf-8")
        asked = set(re.findall(r'_report_query\s+"\$[^"]*"\s+([a-z_]+)', run_sh))
        assert asked, "found no _report_query call sites to check"
        unanswered = sorted(name for name in asked if name not in QUERIES)
        assert not unanswered, (
            f"run.sh asks for queries absent from QUERIES: {unanswered}"
        )
        # ...and nothing in QUERIES is dead surface this module alone keeps
        # alive: a query no caller asks for is a maintenance cost with no
        # consumer, which the reverse check above cannot see.
        unused = sorted(set(QUERIES) - asked)
        assert not unused, f"QUERIES names queries run.sh never asks for: {unused}"

    @pytest.mark.parametrize("query", QUERIES)
    def test_no_query_is_reported_unknown(self, tmp_path: Path, query: str) -> None:
        # Exit 2 is "no such query" -- a name in QUERIES must never earn it,
        # on any document. A renamed query would otherwise degrade to the
        # silent "cannot tell" path at the bash layer.
        for document in ({}, {"verdict": "COMPATIBLE"}, {"diff": {}}):
            code, _ = _ask(tmp_path, document, query)
            assert code != 2, f"{query} reported unknown for {document}"

    def test_an_unknown_query_is_exit_2(self, tmp_path: Path) -> None:
        code, out = _ask(tmp_path, {"verdict": "COMPATIBLE"}, "no_such_query")
        assert code == 2
        assert out == ""


#: Documents that carry no compatibility result, each a *different* way of
#: carrying none. The reported instance was `{}` alone; the class is "the
#: reader was handed something it cannot draw a verdict from", and a fix
#: tested against one member of that class forecloses one input, not the
#: class. Built as (label, payload) where payload is bytes written verbatim,
#: `None` for "no file at all", or a JSON-serializable object.
INVALID_DOCUMENTS = (
    ("no file", None),
    ("zero bytes", b""),
    ("whitespace only", b"   \n\t\n"),
    ("truncated object", b'{"verdict": "COMPATIBLE"'),
    ("truncated array", b"[1, 2"),
    ("not json at all", b"Traceback (most recent call last):\n  File ...\n"),
    ("html error page", b"<!DOCTYPE html><title>502</title>"),
    ("json array", b"[]"),
    ("json array of objects", b'[{"verdict": "COMPATIBLE"}]'),
    ("bare string", b'"COMPATIBLE"'),
    ("bare number", b"0"),
    ("json null", b"null"),
    ("json true", b"true"),
    ("nul bytes", b"\x00\x00\x00"),
    ("invalid utf-8", b'{"verdict": "\xff\xfe"}'),
    ("bom then garbage", b"\xef\xbb\xbfnot json"),
    ("empty object", b"{}"),
    ("empty object with whitespace", b"  {}  \n"),
)


class TestNoResultNeverReadsAsSuccess:
    """The P0 invariant, over the whole class of unusable documents.

    ``report_validity`` must never answer ``ok`` for a document no verdict
    can be drawn from. This is the one assertion that, had it existed,
    would have failed before the fix for every member here -- including the
    two ``{}`` shapes, which the reader deliberately still *loads* (so the
    axis queries keep answering exactly what they answered before) while
    reporting the emptiness through this query alone.
    """

    @pytest.mark.parametrize(
        "label,payload", INVALID_DOCUMENTS, ids=[d[0] for d in INVALID_DOCUMENTS]
    )
    def test_validity_is_never_ok(
        self, tmp_path: Path, label: str, payload: object
    ) -> None:
        code, out = _ask(tmp_path, payload, "report_validity")
        assert code == 0, f"{label}: report_validity must always answer"
        assert out.strip() != rq.VALIDITY_OK, f"{label}: claimed a readable report"
        assert out.strip(), f"{label}: answered nothing instead of a token"

    @pytest.mark.parametrize(
        "label,payload", INVALID_DOCUMENTS, ids=[d[0] for d in INVALID_DOCUMENTS]
    )
    def test_no_axis_query_claims_a_clean_result(
        self, tmp_path: Path, label: str, payload: object
    ) -> None:
        # The complement of the above, and the reason `report_validity` had to
        # be a separate question: these queries must keep their deliberate
        # "cannot tell" fallback (ADR-063 Track T8) rather than being made to
        # fail closed, so none of them may report a *verdict* either. An
        # unusable document must yield no compatibility label at all.
        for query in ("compat_verdict", "run_outcome", "no_baseline_audit"):
            code, out = _ask(tmp_path, payload, query, "compatibility")
            assert out.strip() in ("", "clean") or code != 0, (
                f"{label}/{query}: produced {out!r} from an unusable document"
            )
            assert "COMPATIBLE" not in out, (
                f"{label}/{query}: stated a compatibility verdict from an unusable document"
            )

    def test_a_real_report_is_still_ok(self, tmp_path: Path) -> None:
        # The guard against a fix that classifies everything invalid: the
        # invariant above is satisfiable by a reader that always answers
        # "unparseable", and such a reader would fail every real run.
        code, out = _ask(
            tmp_path,
            {"report_schema_version": "4.4", "verdict": "COMPATIBLE"},
            "report_validity",
        )
        assert code == 0
        assert out.strip() == rq.VALIDITY_OK

    @pytest.mark.parametrize(
        "label,payload", INVALID_DOCUMENTS, ids=[d[0] for d in INVALID_DOCUMENTS]
    )
    def test_each_token_is_from_the_declared_vocabulary(
        self, tmp_path: Path, label: str, payload: object
    ) -> None:
        # `run.sh` branches on these tokens by exact string. A token the
        # script has never heard of falls through its `!= ok` test as a
        # failure, which is safe -- but a *typo'd* token that happens to
        # equal "ok" is not, and neither is a token carrying a newline into
        # a workflow command. Pin the vocabulary itself.
        vocabulary = {
            rq.VALIDITY_OK,
            rq.VALIDITY_ABSENT,
            rq.VALIDITY_UNREADABLE,
            rq.VALIDITY_UNPARSEABLE,
            rq.VALIDITY_NOT_OBJECT,
            rq.VALIDITY_EMPTY,
        }
        _, out = _ask(tmp_path, payload, "report_validity")
        token = out.strip()
        assert token in vocabulary, f"{label}: undeclared token {token!r}"
        assert out.count("\n") == 1, f"{label}: token was not exactly one line"


class TestAssuranceAxisSupportedVersionHandling:
    """An absent assurance contribution is not a passing assurance check.

    ``_assurance_gated`` in ``run.sh`` asks "is the contribution exactly
    ``1``", so an absent field, a legacy report and a corrupt one all answered
    "not gated" identically. Two of those three are honest; the third -- a
    report claiming a schema whose contract *includes* the field, and omitting
    it -- is an invalid result. This enumerates the whole small domain rather
    than sampling it: every side of the 2.40 boundary, and every shape the
    value can take.
    """

    #: Straddles the introducing version in both directions, and includes the
    #: 2.4-vs-2.40 pair that a float or lexicographic comparison gets wrong.
    VERSIONS = ("1.0", "2.3", "2.4", "2.39", "2.40", "2.41", "3.0", "4.4", "10.0")

    @pytest.mark.parametrize("version", VERSIONS)
    def test_a_broken_pair_is_contradictory_from_2_40_on(
        self, tmp_path: Path, version: str
    ) -> None:
        # `analysis_assurance` present, its contribution sibling absent.
        # `reporter.py` emits the two under one `if`, so from 2.40 on this pair
        # cannot legitimately be half-present.
        code, out = _ask(
            tmp_path,
            {
                "report_schema_version": version,
                "verdict": "COMPATIBLE",
                "analysis_assurance": {"status": "complete"},
            },
            "assurance_axis",
        )
        assert code == 0
        expected = (
            "contradictory"
            if rq.parse_schema_version(version) >= rq.ASSURANCE_CONTRIBUTION_SINCE
            else "absent_legacy_schema"
        )
        assert out.strip() == expected, f"{version}: expected {expected}"

    @pytest.mark.parametrize("version", VERSIONS)
    def test_neither_key_present_is_never_contradictory(
        self, tmp_path: Path, version: str
    ) -> None:
        # The same rule in the direction that breaks working runs rather than
        # failing broken ones: the contribution is owed only when an
        # `analysis_assurance` block was attached, so an ordinary report at ANY
        # version may carry neither key. An earlier draft inferred the
        # contradiction from the version alone and so failed every such report
        # -- i.e. most real green runs.
        _, out = _ask(
            tmp_path,
            {"report_schema_version": version, "verdict": "COMPATIBLE"},
            "assurance_axis",
        )
        assert out.strip() == "absent_legacy_schema", f"{version}"

    def test_a_broken_pair_under_the_nested_shape_is_also_caught(
        self, tmp_path: Path
    ) -> None:
        # The block is reached through the same root-then-`diff` fallback every
        # other query uses, so a nested-shape report must not escape the check
        # by carrying its block one level down.
        _, out = _ask(
            tmp_path,
            {
                "report_schema_version": "4.4",
                "diff": {"analysis_assurance": {"status": "complete"}},
            },
            "assurance_axis",
        )
        assert out.strip() == "contradictory"

    @pytest.mark.parametrize("version", VERSIONS)
    @pytest.mark.parametrize("value,gates", ((0, False), (1, True)))
    def test_a_present_contribution_outranks_the_version(
        self, tmp_path: Path, version: str, value: int, gates: bool
    ) -> None:
        # The version only ever explains an *absence*. A report that states
        # the contribution is answered from the contribution, whatever version
        # it claims -- otherwise a legacy-versioned report carrying the field
        # (a backport, a hand-assembled fixture) would have its real answer
        # discarded.
        code, out = _ask(
            tmp_path,
            {
                "report_schema_version": version,
                "analysis_assurance_exit_contribution": value,
            },
            "assurance_axis",
        )
        assert code == 0
        assert out.strip() == ("gated" if gates else "not_gated")

    def test_no_version_at_all_is_legacy_not_contradictory(
        self, tmp_path: Path
    ) -> None:
        # No report emitted by any version that carries the field omits its
        # own version, so "no version" cannot be the contradictory case -- and
        # calling it one would fail the step for every hand-built or
        # third-party report.
        _, out = _ask(
            tmp_path,
            {"verdict": "COMPATIBLE", "analysis_assurance": {"status": "complete"}},
            "assurance_axis",
        )
        assert out.strip() == "absent_legacy_schema"

    @pytest.mark.parametrize(
        "version", ("", "x", "2.x", "2.40.1a", "v2.40", "2..40", None, 2.40, [2, 40])
    )
    def test_an_unusable_version_is_never_contradictory(
        self, tmp_path: Path, version: object
    ) -> None:
        # An unparseable version is not a claim that the field is required.
        # Reading one as contradictory would turn a malformed *version string*
        # into a step failure on an otherwise fine report.
        _, out = _ask(
            tmp_path,
            {
                "report_schema_version": version,
                "verdict": "X",
                "analysis_assurance": {"status": "complete"},
            },
            "assurance_axis",
        )
        assert out.strip() == "absent_legacy_schema", f"{version!r}"

    @pytest.mark.parametrize(
        "value",
        (1, 0, "1", "0", True, False, None, 2, -1, 1.0, [], {}, "yes", " 1"),
    )
    def test_the_gate_predicate_matches_the_string_comparison_run_sh_performs(
        self, tmp_path: Path, value: object
    ) -> None:
        # The oracle is `run.sh`'s own test -- `[[ "$x" == "1" ]]` against
        # whatever the heredoc printed, i.e. `str(value)` -- stated
        # independently of the reader's implementation. Python's `==`
        # disagrees with it in both directions (`True == 1`; `"1" != 1`), and
        # normalizing the value to 0/1 in the reader would have moved the gate
        # for `True` in the *permissive* direction.
        document = {"analysis_assurance_exit_contribution": value}
        expected_gated = str(value) == "1" if value is not None else False
        _, axis = _ask(tmp_path, document, "assurance_axis")
        assert (axis.strip() == "gated") is expected_gated, f"{value!r}"
        code, raw = _ask(tmp_path, document, "assurance_contribution")
        if value is None:
            # An explicit JSON `null` is indistinguishable from absence, which
            # is the semantics the heredoc had and callers rely on.
            assert code == 1 and raw == ""
        else:
            assert code == 0
            # `rstrip("\n")`, not `strip()`: bash's `$(...)` removes trailing
            # newlines and nothing else, so a value of `" 1"` really does fail
            # `[[ "$x" == "1" ]]`. Stripping both ends here modelled a
            # comparison `run.sh` does not perform -- and disagreed with the
            # reader, which was right.
            assert (raw.rstrip("\n") == "1") is expected_gated

    def test_the_audit_only_shape_is_reached(self, tmp_path: Path) -> None:
        # `report/no_baseline.py` carries the axis under `exit_axes` instead of
        # at the root. Without this lookup an audit-only run with
        # `assurance.require_complete: true` read "not gated" while the CLI
        # itself had correctly exited 1.
        _, out = _ask(
            tmp_path, {"exit_axes": {"analysis_assurance": 1}}, "assurance_axis"
        )
        assert out.strip() == "gated"

    def test_a_real_zero_short_circuits_before_the_audit_shape(
        self, tmp_path: Path
    ) -> None:
        # Lookup order matters: a root `0` is a real answer and must win over
        # a stale/duplicated `exit_axes` entry rather than falling through to
        # it. A zero-collapsing default here would have made the two
        # indistinguishable.
        _, out = _ask(
            tmp_path,
            {
                "analysis_assurance_exit_contribution": 0,
                "exit_axes": {"analysis_assurance": 1},
            },
            "assurance_axis",
        )
        assert out.strip() == "not_gated"


#: Workflow-command payloads a PR author could get into a report the Action
#: then reads (``_json_report_src`` can, in a failure-before-write case,
#: resolve to a stale JSON file that was committed to the tree). Each one, if
#: echoed to stdout, is interpreted by GitHub Actions as a command rather than
#: as text -- masking a value, setting an output, or forging a verdict.
INJECTION_PAYLOADS = (
    "::set-output name=verdict::COMPATIBLE",
    "::add-mask::secret",
    "::error::forged",
    "::notice::forged",
    "::endgroup::",
    "::stop-commands::token",
    "echo ::set-output name=x::y",
    "%0A::error::newline-escaped",
)


class TestAnnotationsCannotForgeAWorkflowCommand:
    """The annotation filter is exercised by attempting the attack.

    The repository's own escape history is the reason this is written this way:
    a previous workflow-injection defense here (#705 -> #758) shipped a test
    that asserted the *text of the defending file* rather than executing an
    attack against it, and the next defect was a payload the mechanism still
    passed through. So every case below is a real document handed to the real
    reader, and the assertion is over what it actually emits.
    """

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    @pytest.mark.parametrize("level", ("error", "warning", "notice"))
    def test_a_second_command_smuggled_after_a_newline_is_dropped(
        self, tmp_path: Path, payload: str, level: str
    ) -> None:
        # The entry's own prefix agrees with its level, so only the embedded
        # newline makes it dangerous -- which is exactly the case a
        # prefix-only check would pass.
        document = {
            "annotations": [
                {
                    "level": level,
                    "annotation": f"::{level} title=t::real message\n{payload}",
                    "always_visible": True,
                }
            ]
        }
        code, out = _ask(tmp_path, document, "annotations", "1")
        assert payload not in out, f"{level}/{payload!r}: emitted"
        # Dropped entirely rather than truncated at the newline: a partially
        # emitted annotation is still attacker-shaped output. Exit 0 with no
        # output, not exit 1 -- "this report has no annotations to emit" is an
        # answer, and it is the one the heredoc gave by looping over zero
        # entries. (This assertion originally demanded exit 1 and was wrong:
        # the security property held throughout.)
        assert out == ""
        assert code == 0

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_a_prefix_disagreeing_with_the_level_is_dropped(
        self, tmp_path: Path, payload: str
    ) -> None:
        # A `level: notice` entry whose text opens `::error ` would otherwise
        # let a report upgrade its own severity in the log.
        document = {
            "annotations": [
                {"level": "notice", "annotation": payload, "always_visible": True}
            ]
        }
        _, out = _ask(tmp_path, document, "annotations", "1")
        assert out == "", f"{payload!r}: emitted"

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_the_same_defenses_apply_under_the_release_shape(
        self, tmp_path: Path, payload: str
    ) -> None:
        # `libraries[].annotations` is flattened by a separate code path from
        # the top-level array, and a defense applied to only one of the two is
        # the shape this repository's own history keeps re-finding.
        document = {
            "libraries": [
                {
                    "annotations": [
                        {
                            "level": "error",
                            "annotation": f"::error title=t::x\n{payload}",
                            "always_visible": True,
                        }
                    ]
                }
            ]
        }
        _, out = _ask(tmp_path, document, "annotations", "1")
        assert payload not in out, f"{payload!r}: emitted via libraries[]"

    def test_a_well_formed_annotation_is_still_emitted(self, tmp_path: Path) -> None:
        # Without this, every assertion above is satisfied by dropping
        # everything -- which would silently disable the feature.
        document = {
            "annotations": [
                {
                    "level": "error",
                    "annotation": "::error title=t::a real finding",
                    "always_visible": True,
                }
            ]
        }
        code, out = _ask(tmp_path, document, "annotations", "1")
        assert code == 0
        assert out.strip() == "::error title=t::a real finding"

    def test_every_emitted_line_is_one_line_and_correctly_prefixed(
        self, tmp_path: Path
    ) -> None:
        # The invariant stated over the whole emitted set rather than per
        # fixture: whatever survives the filter must be exactly one line and
        # must open with its own declared level. Generated over the cross
        # product of levels, payloads and visibility flags.
        entries = []
        for level in ("error", "warning", "notice", "", "ERROR", None):
            for payload in INJECTION_PAYLOADS + ("plain text", ""):
                for visible in (True, False, None):
                    entries.append(
                        {
                            "level": level,
                            "annotation": f"::{level} title=t::x\n{payload}",
                            "always_visible": visible,
                        }
                    )
                    entries.append(
                        {
                            "level": level,
                            "annotation": payload,
                            "always_visible": visible,
                        }
                    )
        _, out = _ask(tmp_path, {"annotations": entries}, "annotations", "1")
        emitted = [line for line in out.split("\n") if line]
        for line in emitted:
            assert "\n" not in line and "\r" not in line
            level = line.split(" ", 1)[0].removeprefix("::")
            assert level in ("error", "warning", "notice"), line
            assert line.startswith(f"::{level} "), line
        assert len(emitted) <= rq.MAX_ANNOTATIONS


class TestScopeWhereCannotBreakOutOfItsMarkdownSpan:
    """``scope_where`` interpolates PR-controlled member names into a summary line."""

    @pytest.mark.parametrize(
        "name",
        (
            "lib`whoami`.so",
            "a|b.so",
            "lib\nnew.so",
            "lib\r\n| forged | row |",
            "lib\x00.so",
            "lib\x7f.so",
            "| **Verdict** | COMPATIBLE |",
            "```\n## Verdict: COMPATIBLE\n```",
            "\t\tlib.so",
            "",
        ),
    )
    def test_no_name_can_carry_markdown_or_control_characters_through(
        self, tmp_path: Path, name: str
    ) -> None:
        document = {"comparison_scope": {"unchecked": [name]}}
        code, out = _ask(tmp_path, document, "scope_where")
        assert code == 0
        answer = out.rstrip("\n")
        assert "`" not in answer, f"{name!r}: backtick survived"
        assert "|" not in answer, f"{name!r}: table pipe survived"
        assert "\n" not in answer and "\r" not in answer, f"{name!r}: newline survived"
        assert not any(ord(c) < 0x20 or ord(c) == 0x7F for c in answer), f"{name!r}"
        assert answer, f"{name!r}: flattened to nothing instead of a placeholder"

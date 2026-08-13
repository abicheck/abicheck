# Copyright 2026 Nikolay Petrov
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

"""Tests for the backend-independent ``canonical_finding_id`` report field
(schema 2.36/1.15) and its matching ``finding_id`` suppression selector —
P1-9 of the abicheck-bazel-lab architecture review: a suppression rule
written against one header backend's report (CastXML) should reliably
match the equivalent finding in another's (Clang), which the pre-existing
``finding_id`` field cannot guarantee (it folds in ``source_location``/
``description``, fields the two backends aren't guaranteed to spell
identically).
"""

from __future__ import annotations

import json

import pytest
import yaml

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change, DiffResult
from abicheck.finding_identity import report_canonical_finding_id, report_finding_id
from abicheck.reporter import to_json
from abicheck.suppression import Suppression, SuppressionList


def _func_removed(
    symbol: str, *, description: str, source_location: str | None
) -> Change:
    return Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol=symbol,
        description=description,
        old_value=symbol,
        new_value=None,
        source_location=source_location,
    )


class TestReportCanonicalFindingId:
    def test_matches_across_differing_description_and_source_location(self):
        # Same underlying finding (same mangled symbol, same kind, same
        # old/new value) as two header backends might report it -- one with
        # a source_location, the other without; slightly different
        # description wording, e.g. from a differently-spelled parameter
        # type embedded in the text.
        a = _func_removed(
            "_Z3fooPKc",
            description="Function removed: foo(char const*)",
            source_location="lib.h:12",
        )
        b = _func_removed(
            "_Z3fooPKc",
            description="Function removed: foo(char const *)",
            source_location=None,
        )
        assert report_canonical_finding_id(a) == report_canonical_finding_id(b)
        # The ordinary finding_id, by contrast, is NOT stable across this --
        # that's the whole reason canonical_finding_id exists.
        assert report_finding_id(a) != report_finding_id(b)

    def test_never_returns_a_raw_x1f_delimited_primary_id(self):
        # Regression: an earlier revision of this function returned
        # resolve_change_identity(c).primary_id verbatim, which embeds a
        # literal \x1f (ASCII unit separator) field delimiter -- legal in a
        # JSON string (json.dumps escapes control characters) but rejected
        # outright by PyYAML's safe_load in a suppression YAML file's
        # finding_id: value. Caught by test_loads_from_yaml below actually
        # exercising the YAML round trip; pinned here directly too.
        change = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        canonical = report_canonical_finding_id(change)
        assert "\x1f" not in canonical
        # And it must actually survive a real YAML round trip, not merely
        # avoid the one known-bad byte.
        yaml.safe_load(f"finding_id: {canonical}\n")

    def test_differs_for_a_genuinely_different_symbol(self):
        a = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        b = _func_removed(
            "_Z3bari", description="Function removed: bar(int)", source_location=None
        )
        assert report_canonical_finding_id(a) != report_canonical_finding_id(b)

    def test_deterministic_across_repeated_calls(self):
        a = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        assert report_canonical_finding_id(a) == report_canonical_finding_id(a)

    def test_degrades_gracefully_for_a_duck_typed_stub_lacking_qualified_name(self):
        # cli_scan_baseline._baseline_finding_dicts documents accepting
        # lightweight fakes/stubs, not just real Change instances -- a stub
        # missing `.qualified_name` (which resolve_change_identity accesses
        # via plain attribute access, unlike report_finding_id's getattr
        # fallbacks) must degrade to report_finding_id rather than raise.
        class _Stub:
            kind = ChangeKind.FUNC_REMOVED
            symbol = "_Z3fooi"
            description = "Function removed: foo(int)"
            old_value = "_Z3fooi"
            new_value = None
            source_location = None

        stub = _Stub()
        assert report_canonical_finding_id(stub) == report_finding_id(stub)


class TestReporterEmitsCanonicalFindingId:
    def test_change_to_dict_includes_canonical_finding_id(self):
        change = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        result = DiffResult(
            old_version="1.0", new_version="2.0", library="lib", changes=[change]
        )
        report = json.loads(to_json(result))
        (entry,) = report["changes"]
        assert entry["canonical_finding_id"]
        assert entry["canonical_finding_id"] == report_canonical_finding_id(change)
        # Joinable with, but distinct from, the pre-existing finding_id.
        assert entry["finding_id"] == report_finding_id(change)


class TestFindingIdSuppressionSelector:
    def test_matches_by_canonical_id_alone(self):
        change = _func_removed(
            "_Z3fooPKc",
            description="Function removed: foo(char const*)",
            source_location="lib.h:12",
        )
        canonical = report_canonical_finding_id(change)
        rule = Suppression(finding_id=canonical, reason="accepted")
        assert rule.matches(change)

    def test_matches_the_same_finding_reported_by_a_different_backend(self):
        # The scenario P1-9 exists for: a suppression rule minted from a
        # CastXML report's canonical_finding_id must still match the same
        # change as reported by Clang (differing description/source_location).
        castxml_side = _func_removed(
            "_Z3fooPKc",
            description="Function removed: foo(char const*)",
            source_location="lib.h:12",
        )
        clang_side = _func_removed(
            "_Z3fooPKc",
            description="Function removed: foo(char const *)",
            source_location=None,
        )
        rule = Suppression(
            finding_id=report_canonical_finding_id(castxml_side), reason="accepted"
        )
        assert rule.matches(clang_side)

    def test_does_not_match_a_different_finding(self):
        change = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        other = _func_removed(
            "_Z3bari", description="Function removed: bar(int)", source_location=None
        )
        rule = Suppression(
            finding_id=report_canonical_finding_id(other), reason="accepted"
        )
        assert not rule.matches(change)

    def test_standalone_selector_satisfies_at_least_one_requirement(self):
        # Must not raise -- finding_id alone is a valid, sufficient selector.
        Suppression(finding_id="0123456789abcdef", reason="ok")

    def test_no_selector_at_all_still_rejected(self):
        with pytest.raises(ValueError, match="finding_id"):
            Suppression(reason="no selector given")

    def test_loads_from_yaml(self, tmp_path):
        change = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        canonical = report_canonical_finding_id(change)
        yaml_path = tmp_path / "suppress.yaml"
        yaml_path.write_text(
            "version: 1\n"
            "suppressions:\n"
            f"  - finding_id: {canonical}\n"
            "    reason: accepted via canonical id\n"
        )
        suppressions = SuppressionList.load(yaml_path)
        assert len(suppressions) == 1
        assert suppressions.is_suppressed(change)

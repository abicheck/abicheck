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

"""Workstream F S1 ("Header-only comparison") end-to-end tests.

Routes bare public headers (no ``SO_PATH``, no ``--sources``/
``--build-info``) through the real, shared typed pipeline
(``DumpRequest``/``ResolvedDumpRequest``/``execute_dump_request`` ->
``compare()``) and checks the six scenarios
``docs/contribute/plans/vision-api-abi-evolution.md``'s "F. Header-only
comparison" section names: unchanged headers, a removed declaration, an
added API, a changed enum/constant value, a signature change, and an
access/qualifier/default-argument change -- plus the report-facing
contract: source-compatibility findings, versioning advice, scope, and the
unsupported-capability rows (``disposition_audit.not_evaluated_detectors``)
that state what binary-level evidence header-only evidence structurally
cannot provide.

Needs a real castxml (or clang) toolchain -- every test here is a real
header-AST parse, not a mocked one, per this repo's own "third-party/
external-tool-boundary tests must exercise the real public entry point"
convention (root ``AGENTS.md``). Marked ``integration`` throughout.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from abicheck import checker
from abicheck.api_types import DumpRequest, InputSpec
from abicheck.checker_policy import COMPATIBLE_KINDS
from abicheck.errors import ValidationError
from abicheck.service_dump_pipeline import run_dump_request

pytestmark = pytest.mark.integration


def _skip_if_no_header_ast_toolchain() -> None:
    if shutil.which("castxml") is None and shutil.which("clang") is None:
        pytest.skip("no castxml or clang on PATH")


def _dump_header_only(header: Path, version: str):
    """Run a binary-less DumpRequest for *header* through the real pipeline."""
    request = DumpRequest(
        input=InputSpec.of(path=None, headers=[header], version=version)
    )
    return run_dump_request(request)


class TestHeaderOnlyDumpBasics:
    """The dump side: a binary-less DumpRequest with only ``-H`` reaches a
    real header-AST parse, not the legacy empty-snapshot source-only path.
    """

    def test_header_only_snapshot_has_real_declarations(self, tmp_path: Path):
        _skip_if_no_header_ast_toolchain()
        header = tmp_path / "api.h"
        header.write_text(
            "int add(int a, int b);\nstruct Point { int x; int y; };\n",
            encoding="utf-8",
        )
        snap = _dump_header_only(header, "1.0")
        assert snap.header_only is True
        assert snap.platform is None
        assert snap.elf is None
        assert snap.pe is None
        assert snap.macho is None
        assert snap.from_headers is True
        assert [f.name for f in snap.functions] == ["add"]
        assert [t.name for t in snap.types] == ["Point"]

    def test_header_only_snapshot_has_real_declarations_on_clang_backend(
        self, tmp_path: Path
    ):
        """Regression (CodeRabbit review, this PR): the clang backend's own
        ``extract.headers.clang.functions.parse_functions`` delegates to a
        module-level function taking bare ``exported_dynamic``/
        ``exported_static`` sets rather than a context object, so the
        ``no_binary_evidence`` fix (which flips the ``visibility()``
        fallback from HIDDEN to PUBLIC for a genuinely binary-less
        comparison) only took effect on the castxml backend until it was
        explicitly threaded through here too -- verified by pinning the
        clang frontend directly rather than relying on ``auto`` (which
        would silently pick castxml on a host where it's installed)."""
        if shutil.which("clang") is None:
            pytest.skip("no clang on PATH")
        header = tmp_path / "api.h"
        header.write_text(
            "int add(int a, int b);\nstruct Point { int x; int y; };\n",
            encoding="utf-8",
        )
        request = DumpRequest(
            input=InputSpec.of(path=None, headers=[header], version="1.0"),
            frontend="clang",
        )
        snap = run_dump_request(request)
        assert snap.header_only is True
        assert [f.name for f in snap.functions] == ["add"]
        # The bug this regression guards: without no_binary_evidence forwarded,
        # every function is misclassified HIDDEN (no export set to match
        # against) and filtered out of the public surface entirely.
        assert snap.functions[0].visibility.value == "public"

    def test_bare_headers_alone_are_valid_dump_request_evidence(self, tmp_path: Path):
        """A binary-less DumpRequest with only headers (no sources/
        build_info/dump_manifest) validates -- the api_types.py widening
        this workstream added."""
        header = tmp_path / "api.h"
        header.write_text("void f(void);\n", encoding="utf-8")
        request = DumpRequest(input=InputSpec.of(path=None, headers=[header]))
        assert request.validation_errors() == []

    def test_no_evidence_at_all_still_rejected(self):
        """A binary-less DumpRequest with no headers/sources/build_info/
        dump_manifest is still a usage error -- this workstream only widens
        what counts as evidence, it doesn't remove the "some evidence
        required" floor."""
        request = DumpRequest(input=InputSpec.of(path=None))
        errors = request.validation_errors()
        assert any("has no path" in e for e in errors)
        with pytest.raises(ValidationError, match="has no path"):
            request.validate()

    def test_public_header_dirs_alone_is_not_valid_evidence(self, tmp_path: Path):
        """Regression (CodeRabbit review, this PR): ``public_header_dirs``
        is a pure declaration-provenance (public-vs-internal) classifier,
        never a source of headers to parse -- a binary-less DumpRequest
        naming only it (no actual header file, no dump_manifest) must be
        rejected here, not silently reach ``execute_source_only_dump_request``
        and fail later with a confusing "needs sources and/or build_info"
        error that never mentions ``public_header_dirs`` at all."""
        request = DumpRequest(
            input=InputSpec.of(path=None, public_header_dirs=[tmp_path])
        )
        errors = request.validation_errors()
        assert any("has no path" in e for e in errors)
        with pytest.raises(ValidationError, match="has no path"):
            request.validate()


@pytest.fixture()
def _pair(tmp_path: Path):
    """Build (old_snapshot, new_snapshot) from two header trees, or skip."""
    _skip_if_no_header_ast_toolchain()

    def _make(old_source: str, new_source: str):
        old_header = tmp_path / "old.h"
        new_header = tmp_path / "new.h"
        old_header.write_text(old_source, encoding="utf-8")
        new_header.write_text(new_source, encoding="utf-8")
        old_snap = _dump_header_only(old_header, "1.0")
        new_snap = _dump_header_only(new_header, "2.0")
        return old_snap, new_snap

    return _make


def _kinds(result):
    return {c.kind.value for c in result.changes}


class TestHeaderOnlyComparisonScenarios:
    """The six scenarios the plan doc names for this slice."""

    def test_unchanged_headers_no_findings(self, _pair):
        source = "int add(int a, int b);\nstruct Point { int x; int y; };\n"
        old, new = _pair(source, source)
        result = checker.compare(old, new)
        assert result.changes == []
        assert result.verdict == checker.Verdict.NO_CHANGE

    def test_removed_declaration(self, _pair):
        old, new = _pair(
            "int add(int a, int b);\nvoid helper(void);\n",
            "int add(int a, int b);\n",
        )
        result = checker.compare(old, new)
        assert "func_removed" in _kinds(result)
        assert result.verdict in (
            checker.Verdict.BREAKING,
            checker.Verdict.API_BREAK,
        )

    def test_added_api(self, _pair):
        old, new = _pair(
            "int add(int a, int b);\n",
            "int add(int a, int b);\nvoid new_function(void);\n",
        )
        result = checker.compare(old, new)
        assert "func_added" in _kinds(result)
        added = next(c for c in result.changes if c.kind.value == "func_added")
        assert added.kind in COMPATIBLE_KINDS

    def test_signature_change(self, _pair):
        old, new = _pair(
            "int add(int a, int b);\n",
            "int add(int a, int b, int c);\n",
        )
        result = checker.compare(old, new)
        assert "func_params_changed" in _kinds(result)
        assert result.verdict == checker.Verdict.BREAKING

    def test_changed_enum_value_reachable_from_public_api(self, _pair):
        """An enum reachable from a public function's own signature is
        in-surface, so a member-value/membership change is a real,
        gating finding -- not scoped out as non-public-type (the
        unreachable-enum case is covered separately below)."""
        old, new = _pair(
            "enum Color { RED, GREEN, BLUE };\nint use_color(enum Color c);\n",
            "enum Color { RED, GREEN, BLUE, YELLOW };\nint use_color(enum Color c);\n",
        )
        result = checker.compare(old, new)
        assert "enum_member_added" in _kinds(result)

    def test_access_qualifier_change(self, _pair):
        """A C++ class member moving from public to private access."""
        old, new = _pair(
            "class Widget { public: int value(); };\n",
            "class Widget { private: int value(); };\n",
        )
        result = checker.compare(old, new)
        assert _kinds(result) & {
            "method_access_changed",
            "access_level_changed",
            "func_removed",
        }


class TestHeaderOnlyReportContract:
    """The report-level requirements S1 names explicitly: source-
    compatibility findings, versioning advice, scope, and unsupported-
    capability rows."""

    def test_unsupported_capability_rows_are_explicit(self, _pair):
        """Binary-level detectors (symbol presence/versioning, ELF layout,
        vtable/RTTI, DWARF) must appear as explicit not_evaluated rows, not
        silently absent -- ADR-067 D3's convention, applied to the new
        binary-less tier.

        ``glibcxx_dual_abi``/``inline_namespace`` are deliberately NOT
        asserted here: they read only ``Function.mangled`` churn, never
        ``AbiSnapshot.elf``, and are ungated in every comparison shape (see
        ``diff_platform._has_elf_on_both_sides``'s own docstring) -- a
        header-only comparison's mangled-name-linkage limitation is
        signalled by the ``elf`` family's own not_evaluated rows below,
        not by gating these two heuristic detectors specifically.
        """
        old, new = _pair("int add(int a, int b);\n", "int add(int a, int b, int c);\n")
        result = checker.compare(old, new)
        not_evaluated = {
            d.name: d.coverage_gap
            for d in result.detector_results
            if getattr(d, "not_evaluated", False)
        }
        # Symbol presence/versioning and ELF layout.
        assert "elf" in not_evaluated
        assert "elf_layout" in not_evaluated
        # vtable/RTTI linkage-level identity.
        assert "vtable_identity" in not_evaluated
        # DWARF-derived layout.
        assert "dwarf" in not_evaluated
        assert "advanced_dwarf" in not_evaluated
        # Every gap must carry a real, non-empty reason -- never a bare flag.
        assert all(reason for reason in not_evaluated.values())

    def test_versioning_advice_present_and_honest_about_missing_binary(self, _pair):
        old, new = _pair("int add(int a, int b);\n", "int add(int a, int b, int c);\n")
        result = checker.compare(old, new)
        # release_recommendation is computed by the reporter; go through it
        # so the test exercises the same path a real report does.
        import json

        from abicheck.reporter import to_json

        doc = json.loads(to_json(result))
        rec = doc["release_recommendation"]
        assert rec["possible_impact"] == "major"
        assert "binary" in rec["rationale"].lower()

    def test_scope_is_stated(self, _pair):
        """The comparison states what was actually compared (declared
        header surface), not silently."""
        old, new = _pair(
            "enum Color { RED, GREEN };\nint add(int a, int b);\n",
            "enum Color { RED, GREEN, BLUE };\nint add(int a, int b);\n",
        )
        result = checker.compare(old, new)
        # The unreferenced enum's own member addition is real, detected
        # evidence -- but out of the public-API-reachable surface, so it is
        # recorded (never dropped) in the out-of-surface ledger with its
        # own reason, exactly the "record before disposing" principle.
        assert result.out_of_surface_count >= 1
        reasons = {c.surface_exclusion_reason for c in result.out_of_surface_changes}
        assert "non-public-type" in reasons


class TestHeaderOnlyBinaryPathUnaffected:
    """AGENTS.md: this workstream must not change L0/L1 semantics for
    binary inputs. Sanity-checks that the new ``header_only``/
    ``no_binary_evidence`` machinery defaults to inert for an ordinary
    snapshot that never went through the new path."""

    def test_default_header_only_is_false(self):
        from abicheck.model import AbiSnapshot

        assert AbiSnapshot(library="x", version="1.0").header_only is False

    def test_schema_round_trip_preserves_header_only(self, tmp_path: Path):
        _skip_if_no_header_ast_toolchain()
        header = tmp_path / "api.h"
        header.write_text("int add(int a, int b);\n", encoding="utf-8")
        snap = _dump_header_only(header, "1.0")
        assert snap.header_only is True

        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        round_tripped = snapshot_from_dict(snapshot_to_dict(snap))
        assert round_tripped.header_only is True

    def test_pre_v44_snapshot_loads_as_not_header_only(self):
        from abicheck.serialization import snapshot_from_dict

        doc = {"library": "libfoo.so", "version": "1.0", "schema_version": 20}
        snap = snapshot_from_dict(doc)
        assert snap.header_only is False

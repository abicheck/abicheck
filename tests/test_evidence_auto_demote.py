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

"""Automatic L4-proven "internal, non-exported" demotion (ADR-028 D3).

The authority-rule false-positive-removal path: corroborating L4 source evidence
may *lower* a source-only finding's verdict when it proves the finding concerns
an internal, non-exported declaration — but it must never guess, never raise,
and never touch an artifact-proven break.
"""

from __future__ import annotations

from abicheck.buildsource.evidence_policy import (
    _AUTO_DEMOTE_VERDICT,
    auto_demote_unexported_source_findings,
)
from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
from abicheck.change_registry_types import Verdict
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change


def _decl(qn, *, mangled="", visibility="unknown") -> SourceEntity:
    return SourceEntity(
        id=qn, kind="function", qualified_name=qn, mangled_name=mangled,
        visibility=visibility,
    )


def _surface(decls, exports) -> SourceAbiSurface:
    return SourceAbiSurface(
        reachable_declarations=list(decls),
        roots={"exported_symbols": list(exports)},
    )


def _finding(symbol) -> Change:
    return Change(
        kind=ChangeKind.INLINE_BODY_CHANGED,
        symbol=symbol,
        description="inline body changed",
    )


def test_demotes_proven_internal_unexported_decl():
    # `helper` is internal (visibility=source) and not in the export table:
    # a source-only finding on it is provably not a public break → demoted.
    surface = _surface(
        [_decl("helper", mangled="_Z6helperv", visibility="source")],
        exports=["_Z3apiv"],
    )
    findings = [_finding("helper")]
    n = auto_demote_unexported_source_findings(findings, surface)
    assert n == 1
    assert findings[0].effective_verdict == _AUTO_DEMOTE_VERDICT
    assert _AUTO_DEMOTE_VERDICT == Verdict.COMPATIBLE_WITH_RISK


def test_keeps_exported_public_decl_untouched():
    # `api` IS in the export table → not demoted (a real finding stands).
    surface = _surface(
        [_decl("api", mangled="_Z3apiv", visibility="public_header")],
        exports=["_Z3apiv"],
    )
    findings = [_finding("api")]
    n = auto_demote_unexported_source_findings(findings, surface)
    assert n == 0
    assert findings[0].effective_verdict is None


def test_unknown_visibility_is_not_proof_of_internal():
    # Not exported, but visibility is `unknown` (could be a mangling gap / wrong
    # checkout) → we never treat that as proof, so no demotion.
    surface = _surface(
        [_decl("mystery", mangled="_Z7mysteryv", visibility="unknown")],
        exports=["_Z3apiv"],
    )
    findings = [_finding("mystery")]
    assert auto_demote_unexported_source_findings(findings, surface) == 0
    assert findings[0].effective_verdict is None


def test_noop_when_no_export_table_plumbed():
    # Without a known export set we cannot prove anything → no-op.
    surface = _surface(
        [_decl("helper", mangled="_Z6helperv", visibility="source")],
        exports=[],
    )
    findings = [_finding("helper")]
    assert auto_demote_unexported_source_findings(findings, surface) == 0
    assert findings[0].effective_verdict is None


def test_noop_when_surface_absent():
    findings = [_finding("helper")]
    assert auto_demote_unexported_source_findings(findings, None) == 0
    assert findings[0].effective_verdict is None


def test_only_lowers_never_raises_an_existing_lower_ceiling():
    # A finding already pinned to COMPATIBLE (e.g. by a user `ignore` knob) must
    # not be raised to COMPATIBLE_WITH_RISK by the auto-demote.
    surface = _surface(
        [_decl("helper", mangled="_Z6helperv", visibility="source")],
        exports=["_Z3apiv"],
    )
    f = _finding("helper")
    f.effective_verdict = Verdict.COMPATIBLE
    assert auto_demote_unexported_source_findings([f], surface) == 0
    assert f.effective_verdict == Verdict.COMPATIBLE


def test_lowers_an_api_break_ceiling():
    surface = _surface(
        [_decl("helper", mangled="_Z6helperv", visibility="source")],
        exports=["_Z3apiv"],
    )
    f = _finding("helper")
    f.effective_verdict = Verdict.API_BREAK
    assert auto_demote_unexported_source_findings([f], surface) == 1
    assert f.effective_verdict == _AUTO_DEMOTE_VERDICT


def test_decl_without_qualified_name_is_skipped():
    # A surface decl carrying no qualified_name cannot be matched to a finding
    # and is skipped without error (the finding is simply not demoted).
    surface = _surface(
        [SourceEntity(id="anon", kind="function", qualified_name="", visibility="source")],
        exports=["_Z3apiv"],
    )
    findings = [_finding("helper")]
    assert auto_demote_unexported_source_findings(findings, surface) == 0
    assert findings[0].effective_verdict is None


def test_shared_qualified_name_with_one_public_occurrence_blocks_demotion():
    # The same qualified name appears on two TUs; one occurrence is public and
    # exported → the conservative merge keeps it in-surface (never demoted).
    surface = _surface(
        [
            _decl("f", mangled="_Z1fv", visibility="source"),
            _decl("f", mangled="_Z1fv", visibility="public_header"),
        ],
        exports=["_Z1fv"],
    )
    findings = [_finding("f")]
    assert auto_demote_unexported_source_findings(findings, surface) == 0
    assert findings[0].effective_verdict is None

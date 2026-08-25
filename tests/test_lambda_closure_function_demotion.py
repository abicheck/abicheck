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

"""``demote_lambda_closure_unexported_findings``: a function-level finding
whose subject is a template instantiated over a local lambda closure type,
and whose reported symbol is confirmed absent from BOTH binaries' real
exported symbol table, is demoted (never removed) via the ADR-025
``effective_verdict``/``modulation_reason``/``modulation_rule`` hook --
mirroring ``diff_versioning.demote_internal_version_node_findings``.

A genuinely-exported symbol of the identical shape, and a castxml-
synthesized ctor/dtor key (never a real export by construction, so absence
there is vacuous, not confirmed), must both stay untouched -- see
AGENTS.md's "Lambda-closure churn survives at the function level" entry for
the investigation this closes.
"""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.diff_templates import demote_lambda_closure_unexported_findings
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
from abicheck.model import AbiSnapshot, Function, Param, Visibility

_OLD_PARAM = "raii_guard<(lambda:task_group.h:100:5)>"
_NEW_PARAM = "raii_guard<(lambda:task_group.h:522:26)>"
# Deliberately not a real Itanium mangling and deliberately not in either
# side's exported symbol table -- see below.
_MANGLED = "_Z7processDoesNotMatchAnyRealDynsymEntry"


def _fn(mangled: str, ptype: str, name: str = "process") -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=[Param(name="g", type=ptype)],
        visibility=Visibility.PUBLIC,
    )


def _elf(*names: str) -> ElfMetadata:
    return ElfMetadata(symbols=[ElfSymbol(name=n, sym_type=SymbolType.FUNC) for n in names])


def _snap(version: str, fn: Function, elf: ElfMetadata | None) -> AbiSnapshot:
    return AbiSnapshot(library="lib.so", version=version, functions=[fn], elf=elf)


class TestDemotesWhenConfirmedAbsentFromBothExportTables:
    def test_primitive_sets_the_modulation_fields(self) -> None:
        # Neither the finding's own `symbol` (the guessed mangling) NOR the
        # function's real display name ("process") appears anywhere in
        # either side's exported symbol table -- genuinely, unambiguously
        # never exported under any accepted spelling.
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
            symbol=_MANGLED,
            description="d",
            old_value=_OLD_PARAM,
            new_value=_NEW_PARAM,
        )
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf("unrelated_symbol"))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("unrelated_symbol"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
        assert change.modulation_rule == "lambda_closure_never_exported"
        assert "task_group.h" not in change.modulation_reason


class TestGenuinelyExportedSymbolStaysBreaking:
    def test_symbol_exported_on_old_side_is_not_demoted(self) -> None:
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
            symbol=_MANGLED,
            description="d",
            old_value=_OLD_PARAM,
            new_value=_NEW_PARAM,
        )
        # The exact mangled symbol IS a real export on the old side -- an
        # already-linked consumer could really have resolved it.
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf(_MANGLED))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("process"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None
        assert change.modulation_rule is None

    def test_no_elf_evidence_at_all_is_not_demoted(self) -> None:
        """Fail closed: without a real dynsym table on BOTH sides, "not
        found" cannot be told apart from "we never checked"."""
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
            symbol=_MANGLED,
            description="d",
            old_value=_OLD_PARAM,
            new_value=_NEW_PARAM,
        )
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), None)
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), None)

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None

    def test_symbol_exported_only_under_its_export_alias_is_not_demoted(self) -> None:
        """Codex review, fresh evidence: `change.symbol` can be a guessed
        mangling from `_public_functions`' own bare-name export fallback --
        the function is retained because its real display name ("process")
        is exported, even though the *dict key*/`change.symbol` itself
        (here, a mangling that matches no real dynsym entry) never is.
        Checking `symbol` alone would wrongly call this "confirmed absent";
        an already-linked consumer could still have resolved the exported
        "process" spelling, so the finding must stay exactly as severe as
        the detector made it.
        """
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
            symbol=_MANGLED,
            description="d",
            old_value=_OLD_PARAM,
            new_value=_NEW_PARAM,
        )
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf("process"))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("process"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None
        assert change.modulation_rule is None

    def test_symbol_exported_only_under_its_export_alias_reported_through_compare(
        self,
    ) -> None:
        """The same scenario as above, exercised through the real detection
        pipeline rather than a hand-built ``Change`` -- proving the fix
        holds at the public ``compare()`` surface, not only against the
        primitive in isolation."""
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf("process"))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("process"))
        result = compare(old, new)

        lambda_kinds = {
            ChangeKind.FUNC_PARAMS_CHANGED,
            ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
        }
        touched = [c for c in result.changes if c.kind in lambda_kinds]
        assert touched, "expected at least one lambda-closure param finding"
        for c in touched:
            assert c.effective_verdict is None
            assert c.modulation_rule is None


class TestSyntheticCtorDtorKeysAreNeverDemoted:
    """A castxml-synthesized ctor/dtor key can never equal a real exported
    symbol by construction, so "absent from the export table" is vacuous
    for it -- demoting on that basis would be an unverified, unsafe
    over-demotion. These must stay exactly as severe as the detector made
    them, regardless of how much ELF evidence says nothing about the real,
    castxml-omitted mangled name."""

    def test_synthetic_dtor_key_with_lambda_marker_is_untouched(self) -> None:
        from abicheck.checker_types import Change

        symbol = f"~raii_guard<{_OLD_PARAM[len('raii_guard'):]}"  # "~raii_guard<(lambda:...)>"
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=symbol,
            description="d",
            old_value="~raii_guard",
        )
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf("process"))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("process"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None
        assert change.modulation_rule is None

    def test_synthetic_ctor_key_with_lambda_marker_is_untouched(self) -> None:
        from abicheck.checker_types import Change

        symbol = f"__abicheck_ctor__raii_guard<{_OLD_PARAM[len('raii_guard<') :]}()"
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=symbol,
            description="d",
            old_value="raii_guard",
        )
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf("process"))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("process"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None
        assert change.modulation_rule is None


class TestNonLambdaFindingsAreNeverTouched:
    def test_ordinary_param_change_with_no_lambda_marker_is_untouched(self) -> None:
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
            symbol=_MANGLED,
            description="d",
            old_value="Widget<int>",
            new_value="Widget<double>",
        )
        old = _snap("1", _fn(_MANGLED, "Widget<int>"), _elf("process"))
        new = _snap("2", _fn(_MANGLED, "Widget<double>"), _elf("process"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None

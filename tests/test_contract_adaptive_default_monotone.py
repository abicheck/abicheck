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

"""ADR-049 Phase 7's default contract must never make a break stop gating.

Bug class ``evidence.silent_degradation_to_clean_verdict``. Two mechanisms
reached it after contract evaluation became the default (#1493):

* ELF version-node findings (``symbol_version_node_removed`` and siblings)
  carry a version *name* (``WIDGET_2.0``) as their subject. No contract
  domain can place a version name, so every mode read ``UNKNOWN_UNRESOLVED``
  and the loader's "version not found" failure scored as a pass.
* The evidence-adaptive ``public`` default consulted the export domain only
  for an explicit header non-commitment, not for a finding the headers could
  not decide at all -- so adding ``-H`` turned a lost ``_ZTV``/``_ZTI``
  export from BREAKING into a passing run.

The oracle for the first is the producers' own source (which kinds they
build with a version-name subject), not the evaluator's kind sets; the CLI
oracle for the second is
``test_export_reconciliation_and_obligations.test_public_object_loss_survives_adding_headers``.
"""

from __future__ import annotations

import ast
import itertools
from pathlib import Path

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.compatibility_evaluation_config import ValueProvenance
from abicheck.contract_evaluation import evaluate_snapshot_pair_contract_relevance
from abicheck.contract_relevance_types import (
    ContractMode,
    ContractRelevance,
    SelectorLayer,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.export_surface import compute_export_surface
from abicheck.model import AbiSnapshot
from abicheck.policy.contract_default_mode import observed_export_fallback_applies
from abicheck.surface import PublicSurface

_ROOT = Path(__file__).resolve().parent.parent / "abicheck"
_VERSIONING_PRODUCERS = (
    "diff_platform_elf_symbols.py",
    "diff_versioning.py",
    "versioned_symbol_scheme.py",
)
_UNKNOWN = {ContractRelevance.UNKNOWN_UNRESOLVED, ContractRelevance.UNKNOWN_UNPROVEN}


def _version_subject_kinds() -> set[str]:
    """Kinds whose producer builds them with a version-node subject: a
    ``symbol=`` that is a bare loop variable over version names (``ver``/
    ``node``) or a ``"<...>"`` whole-library sentinel."""
    found: set[str] = set()
    for name in _VERSIONING_PRODUCERS:
        tree = ast.parse((_ROOT / name).read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            kw = {k.arg: k.value for k in call.keywords if k.arg}
            # `Change(kind=..., symbol=...)` or `make_change(Kind, symbol=...)`.
            kind = kw.get("kind") or (call.args[0] if call.args else None)
            sym = kw.get("symbol")
            if not (isinstance(kind, ast.Attribute) and sym is not None):
                continue
            version_subject = (
                isinstance(sym, ast.Name) and sym.id in {"ver", "node"}
            ) or (
                isinstance(sym, ast.Constant)
                and isinstance(sym.value, str)
                and sym.value.startswith("<")
            )
            if version_subject:
                found.add(kind.attr)
    return found


_KINDS = sorted(_version_subject_kinds())


def test_the_producer_scan_is_not_vacuous() -> None:
    # Removed/added definitions, required added/removed/compat, the
    # dropped-node finding and the scheme sentinel -- seven today.
    assert len(_KINDS) >= 7, _KINDS
    assert "SYMBOL_VERSION_NODE_REMOVED" in _KINDS


def _exports() -> object:
    snap = AbiSnapshot(
        library="libwidget.so.1",
        version="1",
        elf=ElfMetadata(symbols=[ElfSymbol(name="widget_create")]),
    )
    return compute_export_surface(snap)


_SURFACES = {
    "unresolvable": PublicSurface(),
    "resolved_elsewhere": PublicSurface(
        resolvable=True,
        public_symbols=frozenset({"widget_create"}),
        all_symbols=frozenset({"widget_create"}),
    ),
}


@pytest.mark.parametrize("kind_name", _KINDS)
@pytest.mark.parametrize("mode", list(ContractMode), ids=lambda m: m.value)
@pytest.mark.parametrize("surface", sorted(_SURFACES))
@pytest.mark.parametrize("subject", ["WIDGET_2.0", "GLIBC_2.34", "<library>"])
def test_version_node_findings_are_never_left_undecided(
    kind_name: str, mode: ContractMode, surface: str, subject: str
) -> None:
    change = Change(
        kind=getattr(ChangeKind, kind_name), symbol=subject, description="x"
    )
    exp = _exports()
    (decision,) = evaluate_snapshot_pair_contract_relevance(
        [change],
        _SURFACES[surface],
        _SURFACES[surface],
        mode=mode,
        exports_old=exp,
        exports_new=exp,
    )
    assert decision.relevance not in _UNKNOWN, (kind_name, mode, decision)


_ADAPTIVE = ValueProvenance(
    layer=SelectorLayer.BUILT_IN_DEFAULT,
    source_kind="evidence_adaptive",
    reference="public_header_evidence",
)
_STATED = ValueProvenance(
    layer=SelectorLayer.EXPLICIT_CLI, source_kind="cli_flag", reference="--contract"
)


def test_adaptive_public_consults_exports_whenever_headers_did_not_place_it() -> None:
    """Exhaustive over mode x provenance x reason x relevance. The oracle is
    the stated rule ("public headers plus observed exports, never less than
    either"): an adaptive ``public`` default re-asks the export domain for
    every header answer that is not a decision -- an explicit non-commitment
    or either UNKNOWN -- and never for an authoritative one."""
    reasons = (
        "closed_domain_no_commitment",
        "required_evidence_incomplete",
        "terminal_authoritative_exclusion",
        "public_root_membership",
        None,
    )
    for mode, prov, reason, rel in itertools.product(
        ContractMode, (_ADAPTIVE, _STATED, None), reasons, (*ContractRelevance, None)
    ):
        undecided = reason == "closed_domain_no_commitment" or rel in _UNKNOWN
        expected = mode is ContractMode.PUBLIC and prov is _ADAPTIVE and undecided
        got = observed_export_fallback_applies(mode, prov, reason, rel)
        assert got is expected, (mode, prov, reason, rel)

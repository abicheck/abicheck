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

Bug class ``evidence.silent_degradation_to_clean_verdict``. Three mechanisms
reached it after contract evaluation became the default (#1493); #1500 fixed
them, and this module pins the class:

* ELF version-node findings (``symbol_version_node_removed`` and siblings)
  carry a version *name* (``WIDGET_2.0``) as their subject. No contract
  domain can place a version name, so every mode read ``UNKNOWN_UNRESOLVED``
  and the loader's "version not found" failure scored as a pass.
* The evidence-adaptive ``public`` default consulted the export domain only
  for an explicit header non-commitment, not for a finding the headers could
  not decide at all -- so adding ``-H`` turned a lost ``_ZTV``/``_ZTI``
  export from BREAKING into a passing run.
* An ``exports`` default has no universe for ``--sources``/``--build-info``
  (L3-L5) findings, so build-option flips and removed macros/typedefs read
  ``UNKNOWN_UNRESOLVED`` and the run read NO_CHANGE.

The oracle for the first is the producers' own source (which kinds they
build with a version-name subject), not the evaluator's kind sets; the CLI
oracle for the second is
``test_export_reconciliation_and_obligations.test_public_object_loss_survives_adding_headers``;
for the third, the catalog's pre-flip ground truth through the real CLI.
"""

from __future__ import annotations

import ast
import itertools
from pathlib import Path

import pytest

from abicheck.checker_policy import ChangeKind
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
from abicheck.model.change import Change
from abicheck.policy.contract_default_mode import (
    observed_export_fallback_applies,
    unresolved_default_fallback_applies,
)
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


def test_adaptive_public_consults_exports_whenever_headers_did_not_commit() -> None:
    """Exhaustive over mode x provenance x reason. Oracle: ADR-049's revised
    rule -- an adaptive ``public`` default re-asks the export table for every
    header answer that records no commitment (searched-and-silent, domain not
    closed, identity not pinned) and never for a commitment or a stated
    domain."""
    silent = {
        "closed_domain_no_commitment",
        "required_evidence_incomplete",
        "identity_ambiguous",
    }
    reasons = (
        *silent,
        "terminal_authoritative_exclusion",
        "public_root_membership",
        "private_header_exclusion",
        None,
    )
    for mode, prov, reason in itertools.product(
        ContractMode, (_ADAPTIVE, _STATED, None), reasons
    ):
        expected = (
            mode is ContractMode.PUBLIC and prov is _ADAPTIVE and reason in silent
        )
        got = observed_export_fallback_applies(mode, prov, reason)
        assert got is expected, (mode, prov, reason)


# --------------------------------------------------------------------------
# The last resort: what no adaptive domain could place -- including every
# `--sources`/`--build-info` (L3-L5) finding under an `exports` default --
# is scored as `all` scores it.
# --------------------------------------------------------------------------


def test_unresolved_default_fallback_is_exhaustively_monotone() -> None:
    """Exhaustive over mode x provenance x relevance. Oracle: only an
    adaptive, not-already-`all` domain and only an UNKNOWN_UNRESOLVED answer
    are re-judged; a stated domain and every decided answer are left alone,
    so the fallback can only add findings to the verdict."""
    for mode, prov, rel in itertools.product(
        ContractMode, (_ADAPTIVE, _STATED, None), (*ContractRelevance, None)
    ):
        expected = (
            prov is _ADAPTIVE
            and mode is not ContractMode.ALL
            and rel is ContractRelevance.UNKNOWN_UNRESOLVED
        )
        got = unresolved_default_fallback_applies(mode, prov, rel)
        assert got is expected, (mode, prov, rel)


#: Every catalog case whose finding exists only in `--sources`/`--build-info`
#: evidence layered over a stored header-less snapshot pair (the adaptive
#: default picks `exports` there), plus case170 whose finding is a version
#: node. Their ground truth predates the default flip and is the oracle.
_OVERLAY_CASES = (
    "case152",
    "case153",
    "case154",
    "case155",
    "case156",
    "case157",
    "case158",
    "case160",
    "case161",
    "case162",
    "case170",
    "case190",
    "case194",
    "case195",
    "case196",
    "case197",
)


@pytest.mark.integration
def test_overlay_catalog_cases_keep_their_ground_truth_through_the_cli(
    tmp_path: Path,
) -> None:
    """Public-surface proof: the real special-CLI runner (subprocess
    `abicheck compare` per case) reproduces each case's catalog verdict, exit
    code and kinds -- verdict and exit are checked independently by it."""
    import json
    import os
    import subprocess
    import sys

    repo = _ROOT.parent
    script = (
        repo / "skills-src/evaluation/validation/scripts/run_special_cli_examples.py"
    )
    ran = subprocess.run(
        [sys.executable, str(script), *_OVERLAY_CASES, "--json"],
        capture_output=True,
        text=True,
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(repo)},
        timeout=1200,
    )
    assert ran.returncode == 0, (
        f"runner exited {ran.returncode}\nstdout:\n{ran.stdout}\nstderr:\n{ran.stderr}"
    )
    payload = json.loads(ran.stdout)
    statuses = {r["case_id"].split("_")[0]: r["status"] for r in payload["results"]}
    assert set(statuses) == set(_OVERLAY_CASES), statuses
    failed = {
        r["case_id"]: r.get("message")
        for r in payload["results"]
        if r["status"] != "PASS"
    }
    assert not failed, failed

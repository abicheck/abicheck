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

"""The ``--abi3`` stable-ABI audit as a *candidate-side enrichment stage* of a
comparison (ADR-068 D3, plan §3 #15 / §6 Phase 2d).

The audit is meaningful only on the candidate: it asks whether *this* build's
imported CPython C-API surface stays inside a ``Py_LIMITED_API`` floor. There
is nothing to compare it against on the baseline side, so per ADR-068 D3 it
rides the **same** result document as the comparison it enriches, with each
finding marked ``candidate_side_enrichment`` -- never a second result, and
never evaluated on OLD.

Three properties are preserved verbatim from ``scan --abi3``
(``scan_engine._run_abi3_audit``), which keeps its own copy untouched until
Phase 6 deletes the command:

* the findings are the same ``python_stable_abi_violation`` rows produced by
  the same engine (:func:`~abicheck.diff_python.audit_stable_abi_imports`);
* they stay **advisory** -- a ``RISK`` kind, gated only through policy
  (``--policy``/``.abicheck.yml`` ``policy.overrides``), never promoted to a
  hard failure by being migrated;
* the precondition is an *evidence-contract* failure, not a finding: a
  candidate that is not a recognisable CPython extension module cannot be
  audited at all, which ADR-064's ``evidence_contract_error`` axis reports as
  exit ``7`` (``DiffResult.evidence_contract_error``, the axis native
  ``compare`` gained in the plan's P3 -- reused here, not re-invented).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "abi3_precondition_failure",
    "apply_abi3_candidate_audit",
]

# Parsing the ``--abi3 VERSION`` spelling itself is a *front-end* concern and
# lives with the shared Click option (``cli_options.parse_abi3_floor``): this
# layer may not import ``abicheck.stable_abi``, which carries no ADR-061
# classification of its own (``scripts/check_architecture.py``). This module
# takes an already-parsed ``(major, minor)`` floor.


def abi3_precondition_failure(
    snapshot: Any, abi3_floor: tuple[int, int], candidate_name: str
) -> str | None:
    """The precondition message when *snapshot* cannot be abi3-audited, else ``None``.

    Same test ``scan_engine._run_abi3_audit`` applies (a snapshot carrying no
    recognised ``python_ext``, or one recognised as a non-extension), and the
    same shared spelling (``python_ext.abi3_precondition_message``), so the
    two commands describe the identical failure identically.
    """
    py_ext = getattr(snapshot, "python_ext", None)
    if py_ext is not None and py_ext.is_extension:
        return None
    from ..python_ext import abi3_precondition_message

    return abi3_precondition_message(abi3_floor, candidate_name)


def apply_abi3_candidate_audit(
    result: Any,
    candidate: Any,
    abi3_floor: tuple[int, int] | None,
    *,
    candidate_name: str | None = None,
) -> str | None:
    """Fold the candidate-side ``--abi3`` audit into an existing ``DiffResult``.

    No-op (returns ``None``) when *abi3_floor* is ``None`` -- the audit is
    opt-in, so a comparison that did not ask for it is bit-for-bit unchanged.

    On a precondition failure the comparison's own findings are left exactly as
    they were and ``result.evidence_contract_error`` is set, which
    ``resolve_compare_exit_decision_with_abort_axes`` turns into exit ``7``;
    the message is returned so the front end can report it. Otherwise every
    audit finding is appended to ``result.changes``, marked
    ``candidate_side_enrichment`` (ADR-068 D3's "marked as such"), and
    ``None`` is returned.

    The findings are appended *without* recomputing the verdict: they are
    ``RISK``-class rows whose contribution to a gate is policy's business
    (``severity``/``--policy``), and re-deriving a verdict here would let a
    migrated advisory check change a comparison's compatibility answer --
    exactly what ADR-068 D3's "migrating them into compare does not promote
    them" forbids.
    """
    if abi3_floor is None:
        return None
    name = candidate_name or getattr(candidate, "library", None) or "<candidate>"
    failure = abi3_precondition_failure(candidate, abi3_floor, str(name))
    if failure is not None:
        result.evidence_contract_error = True
        return failure
    from ..diff_python import audit_stable_abi_imports

    for finding in audit_stable_abi_imports(candidate.python_ext, abi3_floor):
        finding.candidate_side_enrichment = True
        result.changes.append(finding)
    return None

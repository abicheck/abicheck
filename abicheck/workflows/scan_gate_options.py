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

"""``ScanRequest``'s own gate-configuration resolution (ADR-064, CLI cleanup
phase two's PR G2 "typed-API half of the parity pass").

Before this module existed, ``service_scan.run_scan`` never passed
``sev_config``/``exit_code_scheme`` to ``scan_engine.run_scan_core`` at all
-- a typed ``ScanRequest`` had no way to reach the severity-aware exit-code
scheme ``scan --against``'s own ``--severity-preset`` flag already gives the
CLI. (The manual ``--exit-code-scheme`` selector this docstring used to name
alongside it was deleted in PR G2's own atomic-removal stage -- the
algorithm is purely derived from whether a severity setting is in effect.)
Split into its own ``workflows`` leaf module
(coordinating ``scan`` behavior, per ``abicheck/workflows/AGENTS.md``)
rather than a private helper inside ``service_scan.py``, which sits at its
own architecture/debt.yaml ``no_growth`` baseline.

Reuses :func:`abicheck.policy.release_gate_options.resolve_release_gate_options`
directly with ``pack_application=None`` (a ``ScanRequest`` has no pack field
-- see ``service_scan._scan_request_config``'s own docstring) rather than a
second resolution: that function's ``GateOptions.severity is None`` "no
severity setting is in effect" contract already reads correctly with no
pack, so a typed caller's severity configuration folds through the
*identical* object the directory/package release fan-out's own gate
resolution does, not a parallel implementation that merely happens to agree
today.

Takes a loose object (typed ``Any``, matching ``release_gate_options.py``'s
own ``_GatePackApplication`` Protocol precedent) rather than importing
:class:`~abicheck.service_scan.ScanRequest`: ``service_scan.py`` is a
flat, unclassified ``legacy_root_module`` a ``workflows`` module may
structurally depend on, but importing its concrete type here for a type
hint alone is unnecessary coupling this module's own callers don't need.
"""

from __future__ import annotations

from typing import Any


def resolve_scan_gate_options(req: Any) -> Any:
    """Resolve *req* (a :class:`~abicheck.service_scan.ScanRequest`)'s
    ``severity_preset`` field into one
    :class:`~abicheck.policy.release_gate_options.GateOptions`.

    The only field ``scan --against`` itself exposes as a CLI flag -- unlike
    the directory/package release fan-out, neither `scan` nor single-pair
    `compare` has per-category `--severity-<category>` flags (those reach a
    run only via `.abicheck.yml`, which a typed caller has no equivalent
    of), so `ScanRequest` carries no per-category fields either; this always
    passes the four per-category kwargs as ``None``. No ``exit_code_scheme``
    to pass at all any more (CLI cleanup phase two PR G2) -- the algorithm
    is purely derived from whether a severity setting is in effect.

    ``GateOptions.severity is None`` means "no severity setting in effect"
    (the caller then keeps ``run_scan_core``'s own ``sev_config=None``/
    ``exit_code_scheme="legacy"`` defaults); otherwise the caller passes
    ``gate.severity``/``"severity"`` through unchanged.
    """
    from ..policy.release_gate_options import resolve_release_gate_options

    return resolve_release_gate_options(
        None,
        severity_preset=req.severity_preset,
        severity_abi_breaking=None,
        severity_potential_breaking=None,
        severity_quality_issues=None,
        severity_addition=None,
    )

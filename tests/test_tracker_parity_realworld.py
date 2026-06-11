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

"""Regression guards for the *interesting* cases of the live ABICC-oracle scan.

These pin — offline, with the real finding shapes from the scan documented in
``validation/realworld-tracker-parity-2026-06.md`` — how the parity harness
classifies each non-matching pair, so a future change to the scoring engine
cannot silently reclassify a documented boundary (turning an expected,
explained divergence into a hidden false positive/negative or vice-versa).

Every case below is a pair where abicheck disagreed with the abi-laboratory
(ABICC) verdict and, on investigation, abicheck was **correct by its
binary-strict policy** — the divergence is a difference in *scope* (binary vs
public-header) or *evidence* (DWARF coverage), never an abicheck defect:

* nettle 3.6->3.7  — signature changes on ``*_INTERNAL_*`` version-node symbols
* readline 6.2->6.3 / xz 5.2.2->5.2.3 — type churn on author-internal types
* openssl 1.1.1a->1.1.1b — type-only ABICC break, partial DWARF in the binary
* hdf5 1.8.x — a real C++ vtable break in a sibling .so the C-only oracle skips

No network, conda, or abicheck binary is exercised — only the pure classifier
helpers (``conda_harness.scope_sensitive_breaking_only`` / ``load_results_map``
and ``validate._is_evidence_limited`` / ``_is_scope_divergence``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_CONDA = Path("validation/scripts/conda_harness.py")
_VALIDATE = Path("validation/scripts/validate.py")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _harness():
    return _load("conda_harness", _CONDA)


def _validate():
    return _load("validate", _VALIDATE)


# ---------------------------------------------------------------------------
# nettle 3.6 -> 3.7 — ABICC COMPATIBLE, abicheck BREAKING (scored STRICTER).
# Every finding is on a symbol bound to an ``*_INTERNAL_*`` ELF version node
# (HOGWEED_INTERNAL_6_x / NETTLE_INTERNAL_8_x). The pair is NOT auto-excused as a
# scope divergence because it carries func_params_changed, which is inferred from
# DWARF on a still-present symbol and so must stay a scored disagreement
# (Codex review #349). This locks that conservatism on the real exemplar.
# ---------------------------------------------------------------------------
def test_nettle_internal_versionnode_param_change_stays_scored() -> None:
    mod = _harness()
    data = {
        "verdict": "BREAKING",
        "changes": [
            {
                "kind": "symbol_version_node_removed",
                "symbol": "HOGWEED_INTERNAL_6_0",
                "severity": "breaking",
            },
            {
                "kind": "func_removed",
                "symbol": "_nettle_cnd_swap",
                "severity": "breaking",
            },
            {
                "kind": "func_params_changed",
                "symbol": "_nettle_ecc_mod",
                "severity": "breaking",
            },
            {
                "kind": "func_params_changed",
                "symbol": "_nettle_ecc_mod_sqr",
                "severity": "breaking",
            },
        ],
    }
    # func_params_changed present -> not auto-excused as scope divergence.
    assert mod.scope_sensitive_breaking_only(data) is False


def test_nettle_internal_versionnode_symbol_only_is_scope_sensitive() -> None:
    # The libnettle side alone (node removal + internal data-table size growth,
    # all hard symbol-table facts) IS purely scope-sensitive.
    mod = _harness()
    data = {
        "verdict": "BREAKING",
        "changes": [
            {
                "kind": "symbol_version_node_removed",
                "symbol": "NETTLE_INTERNAL_8_0",
                "severity": "breaking",
            },
            {
                "kind": "symbol_size_changed_internal",
                "symbol": "_nettle_hashes",
                "severity": "breaking",
            },
            {
                "kind": "symbol_size_changed_internal",
                "symbol": "_nettle_macs",
                "severity": "breaking",
            },
        ],
    }
    assert mod.scope_sensitive_breaking_only(data) is True


# ---------------------------------------------------------------------------
# readline 6.2 -> 6.3 and xz 5.2.2 -> 5.2.3 — ABICC COMPATIBLE, abicheck BREAKING.
# Findings are DWARF type-layout changes on author-internal types
# (``__rl_search_context`` reserved name; ``lzma_coder`` opaque-in-public-header).
# Type-level kinds are deliberately NOT scope-sensitive: a layout break must stay
# a scored disagreement, never blanket-excused by a symbol-removal counter. These
# guard that the conservative type-level rule holds on the real exemplars.
# ---------------------------------------------------------------------------
def test_readline_internal_type_layout_change_stays_scored() -> None:
    mod = _harness()
    data = {
        "verdict": "BREAKING",
        "changes": [
            {
                "kind": "type_size_changed",
                "symbol": "__rl_search_context",
                "severity": "breaking",
            },
            {
                "kind": "type_field_offset_changed",
                "symbol": "__rl_search_context",
                "severity": "breaking",
            },
            {"kind": "func_removed", "symbol": "_rl_trace", "severity": "breaking"},
        ],
    }
    assert mod.scope_sensitive_breaking_only(data) is False


def test_xz_opaque_internal_type_change_stays_scored() -> None:
    mod = _harness()
    data = {
        "verdict": "BREAKING",
        "changes": [
            {"kind": "type_removed", "symbol": "lzma_coder_s", "severity": "breaking"},
            {
                "kind": "typedef_base_changed",
                "symbol": "lzma_coder",
                "severity": "breaking",
            },
        ],
    }
    assert mod.scope_sensitive_breaking_only(data) is False


# ---------------------------------------------------------------------------
# hdf5 1.8.x — ABICC COMPATIBLE (tracks the C ``libhdf5`` soname only), abicheck
# BREAKING because a sibling C++ .so (``libhdf5_hl_cpp``) carries a REAL vtable
# break (``_ZTV14FL_PacketTable`` grew). The harness aggregates the most-breaking
# verdict across a package's shared objects, so a real break in any one .so must
# survive a COMPATIBLE sibling — never be masked.
# ---------------------------------------------------------------------------
def test_hdf5_multilib_most_breaking_wins() -> None:
    mod = _harness()
    records = [
        {"pair": "hdf5_1.8.16_to_1.8.17", "verdict": "COMPATIBLE"},  # libhdf5_hl (C)
        {
            "pair": "hdf5_1.8.16_to_1.8.17",
            "verdict": "BREAKING",
        },  # libhdf5_hl_cpp vtable
    ]
    assert mod.load_results_map(records)["hdf5_1.8.16_to_1.8.17"] == "BREAKING"


def test_hdf5_vtable_break_is_not_scope_sensitive() -> None:
    # A vtable-slot-count change is a genuine layout break: it must NOT be
    # auto-excused even though the oracle (scoped to the C soname) saw nothing.
    mod = _harness()
    data = {
        "verdict": "BREAKING",
        "changes": [
            {
                "kind": "vtable_slot_count_changed",
                "symbol": "_ZTV14FL_PacketTable",
                "severity": "breaking",
            },
        ],
    }
    assert mod.scope_sensitive_breaking_only(data) is False


# ---------------------------------------------------------------------------
# openssl 1.1.1a -> 1.1.1b — ABICC BREAKING (99.91%, removed_symbols=0: a
# type-only change), abicheck COMPATIBLE. The conda libcrypto carries a
# ``.debug_info`` section but only sparse DWARF (4 subprograms vs ~4200 exported
# functions), so it does not cover the changed interface. The harness's evidence
# probe (``has_type_evidence``) now distinguishes a debug *section* from actual
# *coverage*: a type-only oracle break the binary cannot substantiate is excused
# as evidence-limited, not scored as a false negative. The gate keys on
# ``has_type_evidence``, so a binary with real DWARF coverage still stays scored.
# ---------------------------------------------------------------------------
def test_openssl_partial_dwarf_typeonly_break_is_evidence_limited() -> None:
    mod = _validate()
    pair = {"expected_verdict": "BREAKING", "removed_symbols": 0}
    # Real DWARF coverage of the surface -> abicheck could see the types, so a
    # miss WOULD be a real FN; the pair stays scored, not excused.
    assert (
        mod._is_evidence_limited(pair, "COMPATIBLE", {"has_type_evidence": True})
        is False
    )
    # Partial/absent type evidence (openssl's sparse libcrypto DWARF) -> the
    # change is unobservable, so the pair is excused as evidence-limited.
    assert (
        mod._is_evidence_limited(pair, "COMPATIBLE", {"has_type_evidence": False})
        is True
    )

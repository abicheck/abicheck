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

"""Exported OBJECT-size change policy (ISSUE-45/54/55/56).

A change to the size of an exported data (``OBJECT``/``TLS``) symbol can affect
copy-relocation or direct-data consumers. abicheck splits this by whether the
symbol looks like part of the intended public ABI:

* A **public-looking** data symbol (e.g. ``jpeg_std_message_table``) keeps the
  hard-breaking ``symbol_size_changed`` classification.
* An **internal-looking** one — reserved/underscore-prefixed, the convention for
  private exported state (``_XkeyTable``, ``_pcre2_ucd_records_8``,
  ``_UCD_accessors``, ``_rl_*``) — is reported as ``symbol_size_changed_internal``
  at ``COMPATIBLE_WITH_RISK`` rather than a hard break, because libabigail
  typically reports no ABI change for these and public copy-reloc consumers are
  unlikely.

Either classification can be overridden via a ``--policy-file``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_policy import BREAKING_KINDS, RISK_KINDS
from abicheck.checker_types import Change
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot, Variable
from abicheck.policy_file import PolicyFile


def _snap_with_object(name: str, size: int, *, variable: Variable | None = None) -> AbiSnapshot:
    s = AbiSnapshot(library="libX11.so.6", version="1")
    s.elf = ElfMetadata(  # type: ignore[attr-defined]
        soname="libX11.so.6",
        symbols=[ElfSymbol(
            name=name, binding=SymbolBinding.GLOBAL,
            sym_type=SymbolType.OBJECT, size=size,
        )],
    )
    if variable is not None:
        s.variables.append(variable)
    return s


def _const_string_var(name: str) -> Variable:
    return Variable(
        name=name,
        mangled=name,
        type="char const []",
        is_const=True,
    )


def test_partition_kinds():
    assert ChangeKind.SYMBOL_SIZE_CHANGED in BREAKING_KINDS
    assert ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL in RISK_KINDS
    assert ChangeKind.SYMBOL_SIZE_CHANGED_CONST_OBJECT in BREAKING_KINDS


def test_internal_data_symbol_size_change_is_risk_by_default():
    # _XkeyTable is internal-looking (reserved leading underscore).
    r = compare(_snap_with_object("_XkeyTable", 47318),
                _snap_with_object("_XkeyTable", 48459))
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL in kinds
    assert ChangeKind.SYMBOL_SIZE_CHANGED not in kinds
    assert r.verdict == Verdict.COMPATIBLE_WITH_RISK


def test_public_data_symbol_size_change_is_still_breaking():
    # No leading underscore -> public-looking -> hard break preserved.
    r = compare(_snap_with_object("jpeg_std_message_table", 1032),
                _snap_with_object("jpeg_std_message_table", 1040))
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.SYMBOL_SIZE_CHANGED in kinds
    assert ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL not in kinds
    assert r.verdict == Verdict.BREAKING


def test_public_const_unbounded_string_size_change_preserves_copy_reloc_break():
    # PROJ exposes pj_release as: extern char const pj_release[].
    # Even without a fixed header bound, old non-PIE consumers can still carry
    # copy relocations sized from the old DSO symbol.
    r = compare(
        _snap_with_object("pj_release", 31, variable=_const_string_var("pj_release")),
        _snap_with_object("pj_release", 29, variable=_const_string_var("pj_release")),
    )
    kinds = {c.kind for c in r.changes}
    assert ChangeKind.SYMBOL_SIZE_CHANGED_CONST_OBJECT in kinds
    assert ChangeKind.SYMBOL_SIZE_CHANGED not in kinds
    assert r.verdict == Verdict.BREAKING


def test_policy_override_can_escalate_internal_size_change():
    # A user who relies on a private exported object can force it back to break.
    pf = PolicyFile(
        base_policy="strict_abi",
        overrides={ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL: Verdict.BREAKING},
    )
    c = Change(kind=ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL, symbol="_XkeyTable",
               description="size 47318 -> 48459")
    assert pf.compute_verdict([c]) == Verdict.BREAKING


def test_policy_override_can_downgrade_public_size_change():
    pf = PolicyFile(
        base_policy="strict_abi",
        overrides={ChangeKind.SYMBOL_SIZE_CHANGED: Verdict.COMPATIBLE_WITH_RISK},
    )
    c = Change(kind=ChangeKind.SYMBOL_SIZE_CHANGED, symbol="jpeg_std_message_table",
               description="size grew")
    assert pf.compute_verdict([c]) == Verdict.COMPATIBLE_WITH_RISK


def test_policy_file_escalates_internal_size_change_end_to_end(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(textwrap.dedent("""
        base_policy: strict_abi
        overrides:
          symbol_size_changed_internal: break
    """).strip(), encoding="utf-8")
    pf = PolicyFile.load(policy)

    r = compare(
        _snap_with_object("_XkeyTable", 47318),
        _snap_with_object("_XkeyTable", 48459),
        policy_file=pf,
    )
    assert ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL in {c.kind for c in r.changes}
    assert r.verdict == Verdict.BREAKING

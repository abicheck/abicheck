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

A change to the size of an exported data (``OBJECT``/``TLS``) symbol under a
stable SONAME is reported BREAKING by default, because copy-relocation or
direct-data consumers can read truncated/oversized data. For internal-looking
exported data symbols (e.g. ``_XkeyTable``, ``_pcre2_ucd_records_*``) libabigail
sometimes reports no ABI change, so a project may wish to *downgrade* this
finding.

The safe mechanism is a per-kind policy override (no false negatives by
default): ``symbol_size_changed`` keeps its BREAKING default but can be
downgraded via a ``--policy-file``. These tests pin both the default and the
override path (in-process and via a YAML policy file).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_policy import BREAKING_KINDS
from abicheck.checker_types import Change
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot
from abicheck.policy_file import PolicyFile


def _snap_with_object(name: str, size: int) -> AbiSnapshot:
    s = AbiSnapshot(library="libX11.so.6", version="1")
    s.elf = ElfMetadata(  # type: ignore[attr-defined]
        soname="libX11.so.6",
        symbols=[ElfSymbol(
            name=name, binding=SymbolBinding.GLOBAL,
            sym_type=SymbolType.OBJECT, size=size,
        )],
    )
    return s


def test_symbol_size_changed_is_breaking_by_default():
    assert ChangeKind.SYMBOL_SIZE_CHANGED in BREAKING_KINDS
    r = compare(_snap_with_object("_XkeyTable", 47318),
                _snap_with_object("_XkeyTable", 48459))
    assert ChangeKind.SYMBOL_SIZE_CHANGED in {c.kind for c in r.changes}
    assert r.verdict == Verdict.BREAKING


def test_policy_override_downgrades_symbol_size_changed():
    pf = PolicyFile(
        base_policy="strict_abi",
        overrides={ChangeKind.SYMBOL_SIZE_CHANGED: Verdict.COMPATIBLE_WITH_RISK},
    )
    c = Change(kind=ChangeKind.SYMBOL_SIZE_CHANGED, symbol="_XkeyTable",
               description="size 47318 -> 48459")
    assert pf.compute_verdict([c]) == Verdict.COMPATIBLE_WITH_RISK


def test_policy_file_downgrades_object_size_change_end_to_end(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(textwrap.dedent("""
        base_policy: strict_abi
        overrides:
          symbol_size_changed: risk
    """).strip(), encoding="utf-8")
    pf = PolicyFile.load(policy)

    r = compare(
        _snap_with_object("_XkeyTable", 47318),
        _snap_with_object("_XkeyTable", 48459),
        policy_file=pf,
    )
    # The finding is still surfaced (not hidden) ...
    assert ChangeKind.SYMBOL_SIZE_CHANGED in {c.kind for c in r.changes}
    # ... but the verdict is downgraded out of hard-breaking.
    assert r.verdict == Verdict.COMPATIBLE_WITH_RISK


def test_policy_override_can_also_ignore_object_size_change():
    pf = PolicyFile(
        base_policy="strict_abi",
        overrides={ChangeKind.SYMBOL_SIZE_CHANGED: Verdict.COMPATIBLE},
    )
    c = Change(kind=ChangeKind.SYMBOL_SIZE_CHANGED, symbol="_pcre2_ucd_records_8",
               description="size grew")
    assert pf.compute_verdict([c]) == Verdict.COMPATIBLE

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

"""Regenerate the pre-node-id-unification (``contract_evidence`` schema 1)
fixture used by ``tests/test_contract_replay_node_ids.py``.

**Run only against a checkout that predates that change** (the fixture was
recorded from ``origin/main`` at ``a5c9cf2``: ``git worktree add <dir>
a5c9cf2`` and run ``PYTHONPATH=<dir> python <dir>/tests/fixtures/
contract_context_v1/generate.py`` after copying this file there). Its whole
point is to be a report the *old* code wrote, plus the decisions the *old*
code re-evaluated from it; regenerating it with current code would make the
backward-compatibility test compare the new reader with itself.

Writes ``old.json``/``new.json`` (the snapshot pair), ``report_<mode>.json``
(``abicheck compare --contract <mode> -o json=...``, through the real CLI)
and ``decisions_<mode>.json`` (``{finding_key: {"replayed": ...,
<re-evaluated mode>: relevance}}``).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from abicheck.checker import compare
from abicheck.contract_context import finding_key
from abicheck.contract_context_io import persisted_context_from_dict
from abicheck.contract_relevance_types import ContractMode
from abicheck.contract_replay import (
    reevaluate_from_evidence,
    replay_original_decisions,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.finding_identity import report_finding_id
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.serialization import load_snapshot, snapshot_to_json

HERE = Path(__file__).parent
PUB = {"visibility": Visibility.PUBLIC, "origin": ScopeOrigin.PUBLIC_HEADER}
PRIV = {"visibility": Visibility.HIDDEN, "origin": ScopeOrigin.PRIVATE_HEADER}
MODES = ("public", "exports")


def _record(name: str, bits: int, origin: ScopeOrigin, **kw: object) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=bits,
        fields=[TypeField(name="x", type="int")],
        origin=origin,
        **kw,  # type: ignore[arg-type]
    )


def build_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """Every identity shape the replay graph keys: a mangled C++ root, a
    C-linkage root, two unmangled overloads (public/private), an ODR
    duplicate record, a C tag beside an unrelated same-named typedef, a
    ``typedef struct Tag Tag``, a qualified enum, a variable, and a removal."""

    def snap(version: str, bits: int, modes: list[tuple[str, int]]) -> AbiSnapshot:
        functions = [
            Function(
                name="ns::api",
                mangled="_ZN2ns3apiEPNS_6WidgetE",
                return_type="int",
                params=[Param(name="w", type="ns::Widget *")],
                **PUB,  # type: ignore[arg-type]
            ),
            Function(name="over", mangled="", return_type="int", **PUB),  # type: ignore[arg-type]
            Function(
                name="over",
                mangled="",
                return_type="int",
                params=[Param(name="s", type="Secret *")],
                **PRIV,  # type: ignore[arg-type]
            ),
            Function(
                name="foo",
                mangled="foo",
                return_type="int",
                params=[Param(name="d", type="Dup *")],
                **PUB,  # type: ignore[arg-type]
            ),
            Function(
                name="ns::foo", mangled="_ZN2ns3fooEv", return_type="stat", **PRIV
            ),  # type: ignore[arg-type]
            Function(name="mode_of", mangled="mode_of", return_type="ns::Mode", **PUB),  # type: ignore[arg-type]
            Function(
                name="use_tag",
                mangled="use_tag",
                return_type="int",
                params=[Param(name="t", type="Tag *")],
                **PUB,  # type: ignore[arg-type]
            ),
        ]
        if version == "1":
            functions.append(
                Function(name="gone", mangled="gone", return_type="int", **PUB)
            )  # type: ignore[arg-type]
        return AbiSnapshot(
            library="libdemo.so.1",
            version=version,
            functions=functions,
            variables=[Variable(name="g_count", mangled="g_count", type="int", **PUB)],  # type: ignore[arg-type]
            types=[
                _record(
                    "Widget",
                    bits,
                    ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="ns::Widget",
                ),
                _record("Secret", bits, ScopeOrigin.PRIVATE_HEADER),
                _record("Dup", 64, ScopeOrigin.PUBLIC_HEADER),
                _record("Dup", bits, ScopeOrigin.PRIVATE_HEADER),
                _record("stat", bits, ScopeOrigin.PUBLIC_HEADER),
                _record("Tag", bits, ScopeOrigin.PUBLIC_HEADER),
            ],
            enums=[
                EnumType(
                    name="Mode",
                    qualified_name="ns::Mode",
                    members=[EnumMember(name=n, value=v) for n, v in modes],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            typedefs={"stat": "int", "Tag": "struct Tag"},
            elf=ElfMetadata(
                symbols=[
                    ElfSymbol(name=s)
                    for s in (
                        "_ZN2ns3apiEPNS_6WidgetE",
                        "foo",
                        "mode_of",
                        "use_tag",
                        "g_count",
                        "gone",
                    )
                ]
            ),
        )

    return snap("1", 64, [("A", 0)]), snap("2", 128, [("A", 0), ("B", 1)])


def recorded_decisions(ctx, changes) -> dict[str, dict[str, str]]:  # type: ignore[no-untyped-def]
    """``{finding_key: {"replayed": ..., mode: relevance}}`` -- the shape the
    test compares the current reader's answers against."""
    replayed = replay_original_decisions(ctx)
    assert replayed, "the receipt recorded no decision"
    out: dict[str, dict[str, str]] = {}
    for mode in ContractMode:
        decisions = reevaluate_from_evidence(
            ctx, changes, mode=mode, finding_id=report_finding_id
        )
        for change in changes:
            key = finding_key(change, report_finding_id)
            row = out.setdefault(key, {})
            if key in replayed:
                row["replayed"] = replayed[key].value
            row[mode.value] = decisions[key].relevance.value
    return out


def main() -> int:
    old, new = build_pair()
    (HERE / "old.json").write_text(snapshot_to_json(old))
    (HERE / "new.json").write_text(snapshot_to_json(new))
    old, new = load_snapshot(HERE / "old.json"), load_snapshot(HERE / "new.json")
    for mode in MODES:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck",
                "compare",
                str(HERE / "old.json"),
                str(HERE / "new.json"),
                "--contract",
                mode,
                "-o",
                f"json={HERE / f'report_{mode}.json'}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode in (0, 2, 4), proc.stderr
        report = json.loads((HERE / f"report_{mode}.json").read_text())
        ctx = persisted_context_from_dict(report["contract_context"])
        result = compare(old, new, contract_evaluation=True, contract_mode=mode)
        changes = list(result.changes) + list(result.out_of_surface_changes)
        (HERE / f"decisions_{mode}.json").write_text(
            json.dumps(recorded_decisions(ctx, changes), indent=2, sort_keys=True)
            + "\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

"""Evidence-entity-model gap A4: an unread release member is not a member
that exports nothing.

``bundle_export_index.member_export_names`` read a member whose platform
block was never parsed (a default or parse-failed block: no entries, no
header fields) as a complete, empty table. The release reconciliation then
concluded every obligation only that member provides was a *missing* export,
instead of recording it *unresolved* (AGENTS.md 5b: "an unread member makes
the reconciliation incomplete"). The oracle below is a hand-written table.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.compare.bundle_export_index import build_bundle_export_index
from abicheck.model import AbiSnapshot
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
from abicheck.model.macho_facts import MachoExport, MachoMetadata
from abicheck.model.pe_facts import PeExport, PeMetadata
from abicheck.model.release_surface import PublicObligation, ReleasePublicSurface
from abicheck.policy.release_contract_reconciliation import reconcile_side
from abicheck.workflows.bundle_symbol_status import build_bundle_signature_evidence

# How the second member was read.
STATES = ("read_with", "parsed_empty", "unparsed", "no_block")
# Where the obligation "api_b" lands, by hand.
EXPECT = {
    "read_with": "satisfied",
    "parsed_empty": "missing",
    "unparsed": "unresolved",
    "no_block": "unresolved",
}


def _block(platform: str, state: str) -> dict[str, object]:
    if state == "no_block":
        return {}
    names = ["api_b"] if state == "read_with" else []
    parsed = state != "unparsed"
    if platform == "elf":
        meta: object = ElfMetadata(
            symbols=[ElfSymbol(name=n) for n in names],
            machine="EM_X86_64" if parsed else "",
        )
    elif platform == "pe":
        meta = PeMetadata(
            exports=[PeExport(name=n, ordinal=1) for n in names],
            machine="IMAGE_FILE_MACHINE_AMD64" if parsed else "",
        )
    else:
        meta = MachoMetadata(
            exports=[MachoExport(name=n) for n in names],
            filetype="MH_DYLIB" if parsed else "",
        )
    return {platform: meta}


def _members(platform: str, state: str, compact: bool) -> dict[str, object]:
    a = AbiSnapshot(library="liba", version="1", **_block(platform, "read_with"))
    a_meta = getattr(a, platform)
    getattr(a_meta, "symbols" if platform == "elf" else "exports")[0].name = "api_a"
    b = AbiSnapshot(library="libb", version="1", **_block(platform, state))
    members: dict[str, object] = {"liba": a, "libb": b}
    if compact:
        members = {k: build_bundle_signature_evidence(v) for k, v in members.items()}
    return members


def test_oracle_is_not_constant() -> None:
    assert len(set(EXPECT.values())) == 3


@pytest.mark.parametrize(
    ("platform", "state", "compact"),
    [
        pytest.param(
            p,
            st,
            c,
            marks=pytest.mark.xfail(strict=True, reason="gap A4")
            if st == "unparsed"
            else (),
        )
        for p, st, c in itertools.product(("elf", "pe", "macho"), STATES, (False, True))
    ],
)
def test_unread_member_leaves_obligations_unresolved(
    platform: str, state: str, compact: bool
) -> None:
    surface = ReleasePublicSurface(
        acquisition_key="k",
        side="new",
        obligations=tuple(
            PublicObligation(symbol=s, name=s, entity="function")
            for s in ("api_a", "api_b")
        ),
        declared_symbols=frozenset({"api_a", "api_b"}),
    )
    index = build_bundle_export_index("new", _members(platform, state, compact))
    side = reconcile_side(surface, index)
    where = (
        "satisfied"
        if "api_b" in side.satisfied
        else "missing"
        if any(o.symbol == "api_b" for o in side.missing)
        else "unresolved"
        if any(o.symbol == "api_b" for o in side.unresolved)
        else "nowhere"
    )
    assert where == EXPECT[state], (platform, state, compact, where)
    assert side.coverage_complete is (EXPECT[state] != "unresolved")
    # The member that was read is never demoted by its neighbour.
    assert "api_a" in side.satisfied

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

"""Evidence-entity-model gap A1: an export fact is decided from the read.

``binary_exported_fact`` used to be ``PRESENT(False)`` for every declaration
an export lookup missed whenever a binary was supplied -- including when the
table came back empty because the container had none or its parse failed
(``parse_pe_metadata``/``parse_macho_metadata`` return empty metadata on
error). ``is_export_confirmed_absent`` readers then acted on a table nobody
read.

The oracle below is a hand-written truth table over the five read states,
checked for every producer (castxml, clang, DWARF), independently of the
``ExportTableState`` helpers the implementation uses.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.extract.export_table_read import reconcile_snapshot_export_absence
from abicheck.extract.surface_fact_producers import (
    debug_info_surface_facts,
    header_ast_surface_facts,
)
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.availability import FactStatus
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
from abicheck.model.export_index import ExportMatch
from abicheck.model.macho_facts import MachoExport, MachoMetadata
from abicheck.model.pe_facts import PeExport, PeMetadata
from abicheck.model.surface_facts import is_export_confirmed_absent
from abicheck.serialization import save_snapshot, snapshot_from_dict, snapshot_to_dict

# (status, value) the fact must carry -- written out by hand, not derived.
# "empty_unparsed" is a default/parse-failed block (no entries, no header
# fields: what `parse_pe_metadata`/`parse_macho_metadata` return on error);
# "empty_parsed" a parsed binary that genuinely exports nothing.
READ_STATES = (
    "binary_absent",
    "empty_unparsed",
    "empty_parsed",
    "read_without",
    "read_with",
)
TRUTH: dict[str, tuple[FactStatus, bool | None]] = {
    "binary_absent": (FactStatus.NOT_COLLECTED, None),
    "empty_unparsed": (FactStatus.FAILED, None),
    "empty_parsed": (FactStatus.PRESENT, False),
    "read_without": (FactStatus.PRESENT, False),
    "read_with": (FactStatus.PRESENT, True),
}
PRODUCERS = ("castxml", "clang", "dwarf")
PLATFORMS = ("elf", "pe", "macho")


def _block(platform: str, read: str) -> dict[str, object]:
    """The platform block a dump of that read state records."""
    parsed = read != "empty_unparsed"
    names = {"read_with": ["_Z1fv", "g"], "read_without": ["g"]}.get(read, [])
    if platform == "elf":
        meta: object = ElfMetadata(
            symbols=[ElfSymbol(name=n) for n in names],
            machine="EM_X86_64" if parsed else "",
        )
    elif platform == "pe":
        meta = PeMetadata(
            exports=[PeExport(name=n, ordinal=i) for i, n in enumerate(names, 1)],
            machine="IMAGE_FILE_MACHINE_AMD64" if parsed else "",
        )
    else:
        meta = MachoMetadata(
            exports=[MachoExport(name=n) for n in names],
            filetype="MH_DYLIB" if parsed else "",
        )
    return {platform: meta, "platform": platform}


def _produce(producer: str, platform: str, read: str) -> Function:
    """What each producer records, then the builder's shared tail."""
    snap = _unreconciled(producer, platform, read)
    reconcile_snapshot_export_absence(snap)
    return snap.functions[0]


def test_oracle_is_not_constant() -> None:
    assert len(set(TRUTH.values())) >= 4


# DWARF never runs without a binary, so it has no "binary_absent" row.
_CASES = [
    (p, plat, r)
    for p, plat, r in itertools.product(PRODUCERS, PLATFORMS, READ_STATES)
    if not (p == "dwarf" and r == "binary_absent")
]


@pytest.mark.parametrize(("producer", "platform", "read"), _CASES)
def test_export_fact_follows_the_read(producer: str, platform: str, read: str) -> None:
    fn = _produce(producer, platform, read)
    fact = fn.binary_exported_fact
    want_status, want_value = TRUTH[read]
    assert fact is not None
    assert fact.status is want_status, (producer, platform, read, fact)
    if want_value is not None:
        assert fact.value is want_value, (producer, platform, read, fact)
    # A confirmed absence exists only when a table was actually read.
    assert is_export_confirmed_absent(fn) is (read in ("empty_parsed", "read_without"))


def _unreconciled(producer: str, platform: str, read: str) -> AbiSnapshot:
    """The snapshot a build before the fix persisted: the producer's raw
    answer, with no read reconciliation."""
    match = ExportMatch.DYNAMIC if read == "read_with" else ExportMatch.ABSENT
    if producer == "dwarf":
        facts = debug_info_surface_facts(exported=match)
    else:
        facts = header_ast_surface_facts(
            exported=None if read == "binary_absent" else match, producer=producer
        )
    fn = Function(name="f", mangled="_Z1fv", return_type="void", **facts)
    block = {} if read == "binary_absent" else _block(platform, read)
    return AbiSnapshot(library="libx", version="1", functions=[fn], **block)


@pytest.mark.parametrize(("producer", "platform", "read"), _CASES)
def test_stored_baseline_agrees_with_live(
    producer: str, platform: str, read: str
) -> None:
    """A baseline written before the fix loads to the live dump's answer."""
    stored = _unreconciled(producer, platform, read)
    loaded = snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(stored))))
    live = _produce(producer, platform, read)
    assert is_export_confirmed_absent(loaded.functions[0]) is (
        is_export_confirmed_absent(live)
    ), (producer, platform, read)


def _absent_fn(*, legacy: bool) -> Function:
    if legacy:
        return Function(
            name="f", mangled="_Z1fv", return_type="void", visibility=Visibility.HIDDEN
        )
    facts = header_ast_surface_facts(exported=ExportMatch.ABSENT, producer="castxml")
    return Function(name="f", mangled="_Z1fv", return_type="void", **facts)


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("stored", [False, True], ids=["live", "stored"])
def test_pre_v46_hidden_follows_the_read(platform: str, stored: bool) -> None:
    """A pre-v46 record carries no fact; its ``Visibility.HIDDEN`` is kept as
    a confirmed absence only against a table that was read."""
    for read, expect_absent in (
        ("empty_unparsed", False),
        ("empty_parsed", True),
        ("read_without", True),
    ):
        snap = AbiSnapshot(
            library="libx", version="1", functions=[_absent_fn(legacy=True)],
            **_block(platform, read),
        )  # fmt: skip
        if stored:
            snap = snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snap))))
        else:
            reconcile_snapshot_export_absence(snap)
        assert is_export_confirmed_absent(snap.functions[0]) is expect_absent, (
            platform,
            read,
            stored,
        )


def test_no_platform_block_is_left_alone() -> None:
    snap = AbiSnapshot(library="h", version="1", functions=[_absent_fn(legacy=False)])
    assert reconcile_snapshot_export_absence(snap) == 0
    assert is_export_confirmed_absent(snap.functions[0])


def test_cli_failed_read_never_reports_a_removal(tmp_path: Path) -> None:
    """Through the CLI: a NEW binary whose export table came back empty (a
    PE that failed to parse) must not turn a still-declared function into a
    removal/visibility finding, while a real read still does."""
    old_fn = Function(
        name="f",
        mangled="f",
        return_type="void",
        **header_ast_surface_facts(exported=ExportMatch.DYNAMIC, producer="castxml"),
    )
    old = AbiSnapshot(
        library="x.dll",
        version="1",
        functions=[old_fn],
        pe=PeMetadata(exports=[PeExport(name="f", ordinal=1)]),
    )
    new_fn = Function(
        name="f",
        mangled="f",
        return_type="void",
        **header_ast_surface_facts(exported=ExportMatch.ABSENT, producer="castxml"),
    )
    old_p, unread_p, read_p = (
        tmp_path / n for n in ("old.json", "unread.json", "read.json")
    )
    save_snapshot(old, old_p)
    save_snapshot(
        AbiSnapshot(library="x.dll", version="2", functions=[new_fn], pe=PeMetadata()),
        unread_p,
    )
    save_snapshot(
        AbiSnapshot(
            library="x.dll",
            version="2",
            functions=[new_fn],
            pe=PeMetadata(exports=[PeExport(name="g", ordinal=1)]),
        ),
        read_p,
    )

    def kinds(new: Path) -> set[str]:
        res = CliRunner().invoke(
            main, ["compare", str(old_p), str(new), "-o", "json=-"]
        )
        assert res.exit_code in (0, 1, 2, 4), res.output
        doc = json.loads(res.output)
        return {
            c["kind"] for c in doc["changes"] if "f" in (c.get("symbol"), c.get("name"))
        }

    removal = {"func_removed", "func_visibility_changed", "func_removed_elf_only"}
    assert not kinds(unread_p) & removal
    assert kinds(read_p) & removal

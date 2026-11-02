# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A bundle member with a blocked section compares like the single-file
snapshot it was loaded from (ADR-062 D14/D15), through the real CLI.

Exhaustive over section kind x cause x side x bundle form (G38 document,
G40 archive, and both written from the ``--bundle-facts-out`` spool). The
oracle is the single-file compare of the same two snapshots: the member's
blocked detectors, findings and exit code must be the same, never a false
clean from a section read as empty.

Bug class ``storage.wire_extension_dropped_or_refused``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.compare.section_readers import DETECTOR_SECTIONS
from abicheck.model.bundle_facts import BundleFacts
from abicheck.serialization import load_snapshot, save_bundle_facts
from abicheck.storage.bundle_facts_codec import save_spooled_bundle_facts
from abicheck.storage.bundle_facts_spool import BlobSpool
from tests.test_package_section_version_blocks_section import (
    _CAUSES,
    _SECTIONS,
    _file,
    _snap,
)

_FORMS = ("json", "archive", "spool-json", "spool-archive")
_KEY = "libm.so.1"


def _bundle(tmp_path: Path, name: str, snap_path: Path, form: str) -> Path:
    snap = load_snapshot(snap_path)
    fmt = form.removeprefix("spool-")
    suffix = ".bundlefacts.json" if fmt == "json" else ".abibundle"
    path = tmp_path / f"{name}-{form}{suffix}"
    if form.startswith("spool-"):
        with BlobSpool() as spool:
            members = {_KEY: spool.put_snapshot(snap)}
            save_spooled_bundle_facts(
                BundleFacts(variant_fingerprint="fp"), members, spool, path, format=fmt
            )
    else:
        facts = BundleFacts(
            variant_fingerprint="fp", per_library_snapshots={_KEY: snap}
        )
        save_bundle_facts(facts, path, format=fmt)
    return path


def _report(tmp_path: Path, old: Path, new: Path) -> tuple[int, dict]:
    out = tmp_path / f"{old.name}-{new.name}.r.json"
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", f"json={out}"]
    )
    assert out.exists(), result.output
    return result.exit_code, json.loads(out.read_text())


def _member_view(report: dict) -> tuple:
    """The member's blocked detectors and findings, wherever the report
    nests them (single-file report or release member entry)."""
    if "libraries" in report:
        (entry,) = report["libraries"]
        changes = entry.get("findings", [])
    else:
        entry, changes = report, report["changes"]
    gaps = sorted(w for w in entry["coverage_warnings"] if "not comparable" in w)
    return (
        entry["verdict"],
        gaps,
        sorted((c["kind"], c.get("symbol")) for c in changes),
    )


@pytest.mark.parametrize("form", _FORMS)
@pytest.mark.parametrize("side", ["old", "new"])
@pytest.mark.parametrize("cause", _CAUSES)
@pytest.mark.parametrize("section", _SECTIONS)
def test_bundle_member_blocks_like_the_single_file(
    tmp_path: Path, section: str, cause: str, side: str, form: str
) -> None:
    old = _file(
        tmp_path,
        "old",
        _snap("1", ["f", "g"]),
        section if side == "old" else None,
        cause,
    )
    new = _file(
        tmp_path, "new", _snap("2", ["f"]), section if side == "new" else None, cause
    )
    single_code, single = _report(tmp_path, old, new)
    bundle_code, bundled = _report(
        tmp_path,
        _bundle(tmp_path, "old", old, form),
        _bundle(tmp_path, "new", new, form),
    )
    # The oracle blocks a detector exactly when one reads the section.
    assert bool(_member_view(single)[1]) == any(
        section in reads for reads in DETECTOR_SECTIONS.values()
    )
    assert _member_view(bundled) == _member_view(single)
    assert bundle_code == single_code

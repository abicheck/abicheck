"""ADR-062 D2: a stored package's extractor/resolver generation drift is
reported by the run that loads it -- never refused, never an exit-code change.

Bug class: a reader that never states its own generations makes
``check_reader_compatibility``'s ``semantics_differ`` unreachable, so a
package produced under older resolution semantics read as today's answer.
The oracle is the literal notice text each {axis} x {state} must produce,
written out here rather than derived from the implementation's helpers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.storage import versioning
from abicheck.storage.versioning import StorageVersions

READER = 5

#: state -> (stated package generation, whether a notice is expected)
STATES: dict[str, tuple[int, bool]] = {
    "same": (READER, False),
    "older": (READER - 1, True),
    "newer": (READER + 1, True),
    "unrecorded": (0, False),
}


def _expected_reason(axis: str, stated: int) -> str:
    return (
        f"package was produced under different {axis} semantics "
        f"({axis} {stated} vs this build's {READER}); "
        "derived results may differ from the original producer's"
    )


@pytest.fixture
def reader_at_five(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(versioning, "EXTRACTOR_GENERATION", READER)
    monkeypatch.setattr(versioning, "RESOLVER_GENERATION", READER)


@pytest.mark.parametrize("axis", ["extractor", "resolver"])
@pytest.mark.parametrize("state", sorted(STATES))
def test_reader_reports_drift_per_axis_and_state(
    reader_at_five: None, axis: str, state: str
) -> None:
    stated, drifts = STATES[state]
    other = "resolver" if axis == "extractor" else "extractor"
    versions = StorageVersions(
        **{f"{axis}_generation": stated, f"{other}_generation": READER}
    )

    result = versioning.reader_generation_compatibility(versions)

    assert result.readable
    assert result.semantics_differ is drifts
    assert result.reason == (_expected_reason(axis, stated) if drifts else "")


# --- end to end: writer stamps, reader loads, report carries the notice -------


def _write_package(root: Path, *, ret: str) -> Path:
    from abicheck.model import AbiSnapshot, Function, Visibility
    from abicheck.project_snapshot_legacy import write_legacy_snapshot_package
    from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict

    snap = AbiSnapshot(
        library="libone.so",
        version="1",
        functions=[
            Function(
                name="f", mangled="_Z1fv", return_type=ret, visibility=Visibility.PUBLIC
            )
        ],
    )
    write_legacy_snapshot_package(
        snapshot_to_dict(snap),
        root,
        artifact_id="libone.so",
        max_known_schema_version=SCHEMA_VERSION,
    )
    return root


def _set_generation(root: Path, axis: str, value: int | None) -> None:
    manifest = root / "manifest.json"
    data = json.loads(manifest.read_text())
    if value is None:
        data["versions"].pop(f"{axis}_generation", None)
    else:
        data["versions"][f"{axis}_generation"] = value
    manifest.write_text(json.dumps(data))


def _compare(old: Path, new: Path) -> tuple[int, dict]:
    from abicheck.cli import main

    out = old.parent / "report.json"
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", f"json={out}"]
    )
    return result.exit_code, json.loads(out.read_text())


def test_writer_stamps_this_builds_generations(tmp_path: Path) -> None:
    root = _write_package(tmp_path / "pkg", ret="int")
    stored = json.loads((root / "manifest.json").read_text())["versions"]
    assert stored["extractor_generation"] == versioning.EXTRACTOR_GENERATION >= 1
    assert stored["resolver_generation"] == versioning.RESOLVER_GENERATION >= 1


@pytest.mark.parametrize("axis", ["extractor", "resolver"])
@pytest.mark.parametrize("state", ["same", "older", "newer", "unrecorded"])
@pytest.mark.parametrize("ret", ["int", "long"])  # clean and breaking pair
def test_compare_report_carries_the_notice_without_moving_the_exit_code(
    tmp_path: Path, axis: str, state: str, ret: str
) -> None:
    current = getattr(versioning, f"{axis.upper()}_GENERATION")
    old = _write_package(tmp_path / "old", ret="int")
    new = _write_package(tmp_path / "new", ret=ret)
    baseline_exit, baseline = _compare(old, new)
    stated, reader = current, current
    with pytest.MonkeyPatch.context() as mp:
        if state == "older":
            # The current generation may be 1, so "older" raises the reader
            # rather than lowering the package to the unstated 0.
            reader = current + 1
            mp.setattr(versioning, f"{axis.upper()}_GENERATION", reader)
            _set_generation(new, axis, reader)  # only the old side drifts
        elif state == "newer":
            stated = current + 1
            _set_generation(old, axis, stated)
        elif state == "unrecorded":
            _set_generation(old, axis, None)
        exit_code, report = _compare(old, new)

    notices = [
        w for w in report.get("coverage_warnings", []) if axis in w and "semantics" in w
    ]
    if state in ("same", "unrecorded"):
        assert notices == []
    else:
        assert notices == [
            f"old side: stored package '{old}': package was produced under "
            f"different {axis} semantics ({axis} {stated} vs this build's "
            f"{reader}); derived results may differ from the original producer's"
        ]
    # Informational only: verdict and exit code are the no-notice run's.
    assert exit_code == baseline_exit
    assert report["verdict"] == baseline["verdict"]

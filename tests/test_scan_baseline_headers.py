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

"""`scan --baseline-header`/`--baseline-include` (the old-side header fix).

`scan` has a single ``-H`` built for the candidate; a native ``--baseline``
library was therefore parsed with the *new* headers — wrong when the public
headers changed between versions. These guard the new opt-in old-side headers
plus the loud warning that replaces the old silent reuse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck import cli_scan
from abicheck.cli_scan import scan_cmd


@pytest.mark.parametrize(
    "name,expected",
    [
        ("libfoo.so.2.4.0", True),
        ("libfoo.so", True),
        ("libfoo.dll", True),
        ("libfoo.dylib", True),
        # snapshots / dumps are not re-parsed → not "native"
        ("libfoo.abi.json", False),
        ("baseline.json", False),
        ("old_dump.dump", False),
        ("old.tar.gz", False),
        ("desc.xml", False),
    ],
)
def test_baseline_is_native_library(name: str, expected: bool) -> None:
    # Non-existent paths can't be magic-sniffed → the name heuristic decides.
    assert cli_scan._baseline_is_native_library(Path("dir") / name) is expected


def test_baseline_is_native_library_sniffs_extensionless_elf(tmp_path: Path) -> None:
    # An extensionless ELF (or .pyd/.node/Mach-O framework binary) has no .so in
    # its name, so the suffix heuristic alone would miss it; magic-byte detection
    # must still flag it native so the wrong-headers warning fires (Codex review).
    elf = tmp_path / "libfoo"  # no .so / .dll / .dylib in the name
    elf.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 56)
    assert cli_scan._baseline_is_native_library(elf) is True


def test_baseline_is_native_library_real_json_is_not_native(tmp_path: Path) -> None:
    snap = tmp_path / "baseline.json"
    snap.write_text('{"format": "abicheck-snapshot"}', encoding="utf-8")
    assert cli_scan._baseline_is_native_library(snap) is False


def test_scan_exposes_baseline_header_options() -> None:
    dests = {p.name for p in scan_cmd.params}
    assert {"baseline_header", "baseline_include"} <= dests


class _FakeVerdict:
    value = "NO_CHANGE"


class _FakeDiff:
    verdict = _FakeVerdict()
    breaking: list[object] = []
    source_breaks: list[object] = []
    risk: list[object] = []
    compatible: list[object] = []


class _FakeSnap:
    build_source = None


@pytest.fixture
def _patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Stub the heavy load/compare calls; capture which headers the old side used."""
    import abicheck.cli_buildsource as cbs
    import abicheck.service as service

    captured: dict[str, object] = {}

    def fake_resolve_input(path, headers, includes, **kw):  # type: ignore[no-untyped-def]
        captured["headers"] = list(headers)
        captured["includes"] = list(includes)
        captured["public_headers"] = list(kw.get("public_headers") or [])
        captured["public_header_dirs"] = list(kw.get("public_header_dirs") or [])
        return _FakeSnap()

    monkeypatch.setattr(service, "resolve_input", fake_resolve_input)
    monkeypatch.setattr(service, "compare_snapshots", lambda *a, **k: _FakeDiff())
    monkeypatch.setattr(
        cbs,
        "prepare_embedded_build_source",
        lambda old, new, cm, extra, *rest: (list(extra), [], {}, None),
    )
    return captured


def _run(**kw: object) -> None:
    base = dict(
        baseline=Path("old/libfoo.so.2"),
        new_snap=_FakeSnap(),
        extra_changes=[],
        lang="c++",
        collect_mode="off",
        headers=[Path("new/include")],
        includes=[Path("new/include")],
        public_headers=[Path("new/include")],
        public_header_dirs=[Path("new/include")],
    )
    base.update(kw)
    cli_scan._run_baseline_compare(**base)  # type: ignore[arg-type]


def test_native_baseline_without_baseline_header_warns_and_reuses_candidate(
    _patched: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    _run()
    err = capsys.readouterr().err
    assert "--baseline-header" in err and "native library" in err
    # Fell back to the candidate -H (the documented, now-warned behavior).
    assert _patched["headers"] == [Path("new/include")]


def test_baseline_header_overrides_old_side_and_silences_warning(
    _patched: dict[str, object],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # Real dirs so the `is_dir()` provenance filter is meaningfully exercised:
    # this is precisely what distinguishes the fix from the old buggy
    # `[...] or public_header_dirs`, which would have leaked the NEW dir.
    old_inc = tmp_path / "old" / "include"
    new_inc = tmp_path / "new" / "include"
    old_inc.mkdir(parents=True)
    new_inc.mkdir(parents=True)
    _run(
        headers=[new_inc],
        includes=[new_inc],
        public_headers=[new_inc],
        public_header_dirs=[new_inc],
        baseline_headers=[old_inc],
        baseline_includes=[old_inc],
    )
    err = capsys.readouterr().err
    assert "--baseline-header" not in err
    # Old side parsed with ITS OWN headers, not the new ones.
    assert _patched["headers"] == [old_inc]
    assert _patched["includes"] == [old_inc]
    assert _patched["public_headers"] == [old_inc]
    # The old-side public boundary is derived ONLY from --baseline-header dirs;
    # the candidate's new/include must NOT leak in (Codex review). A relative
    # dir like `include/` would otherwise re-mark old private headers PUBLIC.
    assert _patched["public_header_dirs"] == [old_inc]
    assert new_inc not in _patched["public_header_dirs"]


def test_snapshot_baseline_does_not_warn(
    _patched: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    # A .json snapshot has headers baked in → reuse is harmless → no warning.
    _run(baseline=Path("old/libfoo.abi.json"))
    assert "--baseline-header" not in capsys.readouterr().err

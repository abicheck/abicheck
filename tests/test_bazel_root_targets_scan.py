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

"""P0.2 Bazel root-target scoping -- ``scan`` side (lab report follow-up).

``tests/test_bazel_root_targets.py`` covers ``dump --build-target`` reaching
``embed_build_source``, but ``scan``'s own identical ``embed_build_source``
call (``scan_engine._build_new_snapshot``) never threaded ``build_targets``
through at all -- a `scan --build-target //:math --against
dump-produced-baseline.json` silently ran an UNSCOPED workspace-wide query
even when the `dump` baseline it's compared against was itself
target-scoped, capturing unrelated fixture/test targets and diverging from
the baseline's own evidence. Split into its own sibling file (not added to
``test_bazel_root_targets.py`` directly) purely to keep that file under the
AI-readiness soft-limit -- same ``_extra``-style convention used elsewhere
in this suite.

Covers, bottom-up:

* The CLI -- ``scan --build-target`` (single-binary and ``--artifact-set``)
  reaching ``embed_build_source`` via ``scan_engine._build_new_snapshot``.
* The typed API -- ``ScanRequest.build_targets`` reaching the same call,
  through both ``run_scan`` and ``run_scan_set`` (``--artifact-set``'s own
  Python-API entry point).
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck import cli_buildsource
from abicheck.cli import main
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, AccessLevel, Function, ScopeOrigin, Visibility
from abicheck.serialization import snapshot_to_json
from abicheck.service_scan import ScanRequest, run_scan, run_scan_set


def _bypass_discovery_validation(monkeypatch, *binaries: Path) -> None:
    """Patch discover_artifact_set() to a trivial passthrough -- avoids
    needing real ELF fixtures for tests exercising run_scan_set()'s
    per-member scanning, not the bundle-audit layer itself. Same pattern as
    test_scan_artifact_set.py's identical helper."""
    import abicheck.bundle as bundle_mod

    def _fake_discover(paths, *, explicit):
        return {p.name: p for p in binaries}

    monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)


def _elf(*names: str) -> ElfMetadata:
    return ElfMetadata(symbols=[ElfSymbol(name=n) for n in names])


def _func(name: str, mangled: str) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        access=AccessLevel.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def _write_snapshot(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _artifact(tmp_path: Path, name: str = "artifact") -> Path:
    snap = AbiSnapshot(
        library=f"lib{name}.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov"),
    )
    return _write_snapshot(tmp_path / f"{name}.abi.json", snap)


def _sources(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.cpp").write_text("int f() { return 0; }\n", encoding="utf-8")
    return src


# ── CLI: `scan --build-target` (single-binary) ────────────────────────────


def test_scan_cli_build_target_flag_reaches_embed_build_source(
    monkeypatch, tmp_path: Path
):
    captured: dict = {}

    def _fake_embed(snap, build_info, sources, *, build_targets=(), **kwargs):
        captured["build_targets"] = build_targets

    monkeypatch.setattr(cli_buildsource, "embed_build_source", _fake_embed)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "scan",
            str(_artifact(tmp_path)),
            "--sources",
            str(_sources(tmp_path)),
            "--build-target",
            "//:math",
            "--build-target",
            "//:util",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["build_targets"] == ("//:math", "//:util")


def test_scan_cli_no_build_target_leaves_it_empty(monkeypatch, tmp_path: Path):
    captured: dict = {}

    def _fake_embed(snap, build_info, sources, *, build_targets=(), **kwargs):
        captured["build_targets"] = build_targets

    monkeypatch.setattr(cli_buildsource, "embed_build_source", _fake_embed)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["scan", str(_artifact(tmp_path)), "--sources", str(_sources(tmp_path))],
    )
    assert result.exit_code == 0, result.output
    assert captured["build_targets"] == ()


# ── CLI: `scan --artifact-set --build-target` ──────────────────────────────


def test_scan_cli_artifact_set_build_target_reaches_embed_build_source(
    monkeypatch, tmp_path: Path
):
    captured: dict = {}

    def _fake_embed(snap, build_info, sources, *, build_targets=(), **kwargs):
        captured.setdefault("build_targets", []).append(build_targets)

    monkeypatch.setattr(cli_buildsource, "embed_build_source", _fake_embed)

    a = _artifact(tmp_path, "a")
    b = _artifact(tmp_path, "b")
    _bypass_discovery_validation(monkeypatch, a, b)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "scan",
            "--artifact-set",
            f"{a},{b}",
            "--sources",
            str(_sources(tmp_path)),
            "--build-target",
            "//:math",
        ],
    )
    # The snapshot-JSON members aren't real ELF, so the separate bundle-audit
    # pass (build_bundle_snapshot, unaffected by the discovery bypass above)
    # reads BUNDLE_INCOMPLETE -- exit 1, not 0 -- same as
    # test_scan_artifact_set.py's own use of this bypass; what this test
    # checks is that per-member scanning (which the bypass does cover)
    # still reached embed_build_source with build_targets.
    assert result.exit_code in (0, 1), result.output
    assert captured["build_targets"] == [("//:math",), ("//:math",)]


# ── Typed API: `ScanRequest.build_targets` ─────────────────────────────────


def test_scan_request_build_targets_reaches_embed_build_source_via_run_scan(
    monkeypatch, tmp_path: Path
):
    captured: dict = {}

    def _fake_embed(snap, build_info, sources, *, build_targets=(), **kwargs):
        captured["build_targets"] = build_targets

    monkeypatch.setattr(cli_buildsource, "embed_build_source", _fake_embed)

    req = ScanRequest(
        binaries=[_artifact(tmp_path)],
        sources=_sources(tmp_path),
        build_targets=("//:math",),
    )
    result = run_scan(req)
    assert result.verdict not in ("BUDGET_OVERFLOW", "EVIDENCE_CONTRACT_ERROR")
    assert captured["build_targets"] == ("//:math",)


def test_scan_request_build_targets_reaches_embed_build_source_via_run_scan_set(
    monkeypatch, tmp_path: Path
):
    captured: dict = {}

    def _fake_embed(snap, build_info, sources, *, build_targets=(), **kwargs):
        captured.setdefault("build_targets", []).append(build_targets)

    monkeypatch.setattr(cli_buildsource, "embed_build_source", _fake_embed)

    a = _artifact(tmp_path, "a")
    b = _artifact(tmp_path, "b")
    _bypass_discovery_validation(monkeypatch, a, b)

    req = ScanRequest(
        binaries=[a, b],
        sources=_sources(tmp_path),
        build_targets=("//:math",),
    )
    run_scan_set(req)
    assert captured["build_targets"] == [("//:math",), ("//:math",)]


def test_scan_request_default_build_targets_is_empty_tuple():
    """Additive default -- an existing ScanRequest() caller is unaffected."""
    assert ScanRequest().build_targets == ()

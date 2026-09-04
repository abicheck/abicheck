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

"""`scan`'s old-side header handling (the old-side header fix).

`scan` used to build a single ``-H`` for the candidate only; a native
``--baseline``/``--against`` library was therefore parsed with the *new*
headers — wrong when the public headers changed between versions. The
opt-in old-side headers are now reached via the side-aware ``--header
old=PATH``/``--include old=PATH`` spelling (ADR-040), replacing the old
standalone ``--baseline-header``/``--baseline-include`` options. These tests
guard the old-side-header codepath (``cli_scan._run_baseline_compare``, still
addressed by its own ``baseline_headers``/``baseline_includes`` keyword
arguments) plus the loud warning that replaces the old silent reuse.
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


def test_scan_exposes_against_config_surface_options() -> None:
    # ADR-049 Phase 5 §6.4: `--against` gets the same config surface as
    # `compare` (`--policy`/`--suppress`/`--scope-public-headers`/
    # `--pattern-verdicts`/`--env-matrix`), not a hardcoded
    # strict_abi/unsuppressed/scoped-True baseline comparison. The parity is
    # stated over the *flags both commands still have*: the suppression-strict
    # and public-symbol families were removed from compare and scan alike, so
    # they moved to the config-surface assertion below rather than out of the
    # parity claim.
    dests = {p.name for p in scan_cmd.params}
    assert {
        "suppress",
        "policy",
        "scope_public_headers",
        "pattern_verdicts",
        "env_matrix_path",
        # ADR-049 Phase 5 §6.4's contract-relevance half of the same parity.
        "contract_mode",
    } <= dests
    # The removed half of the same parity: neither command carries these as
    # CLI destinations any more, and `.abicheck.yml` is their only source --
    # so scan reaching them through --config is what keeps §6.4 true.
    assert not dests & {
        "policy_file_path",
        "strict_suppressions",
        "public_symbols",
        "public_symbols_list",
    }
    assert "build_config" in dests


def test_scan_exposes_side_aware_header_options() -> None:
    # The standalone --baseline-header/--baseline-include options are gone;
    # -H/--header and -I/--include are now side-aware (ADR-040), so
    # `--header old=X` is the new spelling for what used to be
    # `--baseline-header X`. Confirm the CLI param destinations reflect that
    # (and that the old, removed dests are really gone).
    dests = {p.name for p in scan_cmd.params}
    assert {"header_pairs", "include_pairs"} <= dests
    assert not ({"baseline_header", "baseline_include"} & dests)


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
        if "headers" not in captured:
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
        lambda old, new, cm, extra, *rest, **kw: (list(extra), [], {}, None),
    )
    return captured


def _run(**kw: object) -> None:
    base = dict(
        baseline=Path("old/libfoo.so.2"),
        binary=Path("new/libfoo.so.3"),
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
    assert "-H old=" in err and "native library" in err
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
    assert "-H old=" not in err
    # Old side parsed with ITS OWN headers, not the new ones.
    assert _patched["headers"] == [old_inc]
    assert _patched["includes"] == [old_inc]
    # `old_inc` is a directory, so it belongs in public_header_dirs ONLY --
    # not also in public_headers as a raw directory "path" (Codex review,
    # PR #624 follow-up: the file-vs-dir split now mirrors the candidate
    # side's `_public_provenance_set`, which never double-counts a directory
    # this way; the old, asymmetric `bl_public_headers = bl_headers` fed the
    # ADR-050 comparability gate an old side whose declared scope was
    # represented differently from the new side's, a false
    # scope_fingerprint mismatch on an ordinary --against comparison).
    assert _patched["public_headers"] == []
    # The old-side public boundary is derived ONLY from -H old= dirs;
    # the candidate's new/include must NOT leak in (Codex review). A relative
    # dir like `include/` would otherwise re-mark old private headers PUBLIC.
    assert _patched["public_header_dirs"] == [old_inc]
    assert new_inc not in _patched["public_header_dirs"]


def test_baseline_header_file_entry_still_counts_as_public_header(
    _patched: dict[str, object],
    tmp_path: Path,
) -> None:
    # Mirrors the candidate side's `_public_provenance_set` file-vs-dir split
    # exactly: a `-H old=<file>` FILE entry (unlike the dir case above) is a
    # real public header path in its own right and must still reach
    # public_headers -- the fix must not degenerate into dropping every
    # baseline_headers entry from public_headers.
    old_file = tmp_path / "old" / "api.h"
    old_file.parent.mkdir(parents=True)
    old_file.write_text("int f(void);\n", encoding="utf-8")
    old_dir = tmp_path / "old" / "detail"
    old_dir.mkdir()
    _run(baseline_headers=[old_file, old_dir], baseline_includes=[old_dir])
    assert _patched["public_headers"] == [old_file]
    assert _patched["public_header_dirs"] == [old_dir]


def test_snapshot_baseline_does_not_warn(
    _patched: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    # A .json snapshot has headers baked in → reuse is harmless → no warning.
    _run(baseline=Path("old/libfoo.abi.json"))
    assert "-H old=" not in capsys.readouterr().err


def test_run_baseline_compare_threads_policy_and_scope_to_compare_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ADR-049 Phase 5 §6.4: `scan --against` reuses the same
    # suppression/policy/scope config surface as direct `compare` --
    # `_run_baseline_compare` must forward its own suppression/policy/
    # policy_file/scope_to_public_surface params into `compare_snapshots`
    # rather than the previously-hardcoded strict_abi/None/True.
    import abicheck.cli_buildsource as cbs
    import abicheck.service as service

    captured: dict[str, object] = {}

    def fake_resolve_input(path, headers, includes, **kw):  # type: ignore[no-untyped-def]
        return _FakeSnap()

    def fake_compare_snapshots(
        old,
        new,
        suppression=None,
        *,
        policy="strict_abi",
        policy_file=None,
        extra_changes,
        scope_to_public_surface,
        force_public_symbols=None,
        pattern_verdicts=False,
        env_matrix=None,
        collapse_versioned_symbols=False,
        contract_evaluation=False,
        contract_mode=None,
    ):
        captured["suppression"] = suppression
        captured["policy"] = policy
        captured["policy_file"] = policy_file
        captured["scope_to_public_surface"] = scope_to_public_surface
        captured["force_public_symbols"] = force_public_symbols
        captured["pattern_verdicts"] = pattern_verdicts
        captured["env_matrix"] = env_matrix
        captured["contract_evaluation"] = contract_evaluation
        captured["contract_mode"] = contract_mode
        return _FakeDiff()

    monkeypatch.setattr(service, "resolve_input", fake_resolve_input)
    monkeypatch.setattr(service, "compare_snapshots", fake_compare_snapshots)
    monkeypatch.setattr(
        cbs,
        "prepare_embedded_build_source",
        lambda old, new, cm, extra, *rest, **kw: (list(extra), [], {}, None),
    )

    sentinel_suppression = object()
    sentinel_policy_file = object()
    sentinel_env_matrix = object()
    _run(
        suppression=sentinel_suppression,
        policy="sdk_vendor",
        policy_file=sentinel_policy_file,
        scope_to_public_surface=False,
        force_public_symbols={"foo"},
        pattern_verdicts=True,
        env_matrix=sentinel_env_matrix,
        contract_evaluation=True,
        contract_mode="exports",
    )

    assert captured["suppression"] is sentinel_suppression
    assert captured["policy"] == "sdk_vendor"
    assert captured["policy_file"] is sentinel_policy_file
    assert captured["scope_to_public_surface"] is False
    assert captured["force_public_symbols"] == {"foo"}
    assert captured["pattern_verdicts"] is True
    assert captured["env_matrix"] is sentinel_env_matrix
    # ADR-049 Phase 5 §6.4: the shadow contract evaluator's own two knobs
    # reach `compare_snapshots` the same way, so a `scan --against` finding
    # can carry the contract relevance/reason/evidence fields the Gate wants
    # compared against `compare`'s.
    assert captured["contract_evaluation"] is True
    assert captured["contract_mode"] == "exports"


def test_run_baseline_compare_forwards_policy_file_to_embedded_build_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex P1 review on PR #657: `--policy`'s evidence_policy knobs
    # (require_evidence / evidence-verdict overrides, ADR-033 D7) are applied
    # by `prepare_embedded_build_source`, not `compare_snapshots` -- passing
    # `policy_file` only to the latter silently drops mandatory-evidence
    # enforcement for `scan --against`. Assert `_run_baseline_compare` forwards
    # its own `policy_file` into the `prepare_embedded_build_source` call too.
    import abicheck.cli_buildsource as cbs
    import abicheck.service as service

    captured: dict[str, object] = {}

    def fake_resolve_input(path, headers, includes, **kw):  # type: ignore[no-untyped-def]
        return _FakeSnap()

    def fake_prepare_embedded_build_source(old, new, cm, extra, *rest, **kw):  # type: ignore[no-untyped-def]
        captured["policy_file"] = kw.get("policy_file")
        return list(extra), [], {}, None

    monkeypatch.setattr(service, "resolve_input", fake_resolve_input)
    monkeypatch.setattr(service, "compare_snapshots", lambda *a, **k: _FakeDiff())
    monkeypatch.setattr(
        cbs, "prepare_embedded_build_source", fake_prepare_embedded_build_source
    )

    sentinel_policy_file = object()
    _run(policy_file=sentinel_policy_file)

    assert captured["policy_file"] is sentinel_policy_file


def test_scan_against_bad_env_matrix_is_usage_error(tmp_path: Path) -> None:
    # Codex P2 review on PR #657: a malformed --env-matrix file must surface
    # as a repository-wide usage error (exit 64), matching `compare`'s own
    # AbicheckError -> click.UsageError handling -- not an uncaught traceback.
    # --against is required here so this actually reaches load_env_matrix's
    # own try/except, rather than the (separately tested) "comparison-only
    # flags need --against" guard short-circuiting first.
    from click.testing import CliRunner

    from abicheck.cli import main

    bad_matrix = tmp_path / "env.yaml"
    bad_matrix.write_text("runtime_floors: [unclosed\n  GLIBC: {")
    snap = tmp_path / "artifact.so"
    snap.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 56)
    baseline = tmp_path / "baseline.abi.json"
    baseline.write_text('{"format": "abicheck-snapshot"}', encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "scan",
            str(snap),
            "--against",
            str(baseline),
            "--env-matrix",
            str(bad_matrix),
        ],
    )

    assert result.exit_code == 64, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--policy", "sdk_vendor"],
        ["--pattern-verdicts"],
        ["--severity-preset", "info-only"],
        # --exit-code-scheme no longer exists at all (CLI cleanup phase two
        # PR G2) -- it fails with "No such option" before this comparison-
        # only-flag guard would even run, which is covered elsewhere
        # (test_compare_dispatch.py's test_exit_code_scheme_flag_no_longer_
        # exists), not here.
        ["--max-findings", "5"],
        ["--require-complete-analysis"],
    ],
)
def test_scan_rejects_comparison_only_flags_without_against(
    tmp_path: Path, extra_args: list[str]
) -> None:
    # Codex P2 review on PR #657: without --against, run_scan_core never
    # calls _run_baseline_compare, so these flags would otherwise be
    # silently parsed and discarded -- including a --policy
    # require_evidence setting the user actually needed. Must be a loud
    # usage error (exit 64), not a silent no-op.
    from click.testing import CliRunner

    from abicheck.cli import main

    snap = tmp_path / "artifact.so"
    snap.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 56)

    result = CliRunner().invoke(main, ["scan", str(snap), *extra_args])

    assert result.exit_code == 64, result.output
    assert "only take effect with --against" in result.output


def test_scan_without_against_and_without_comparison_flags_is_unaffected(
    tmp_path: Path,
) -> None:
    # The new usage-error guard must not fire for a plain one-build audit
    # that never touches any of the comparison-only flags.
    from click.testing import CliRunner

    from abicheck.cli import main

    snap = tmp_path / "artifact.so"
    snap.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 56)

    result = CliRunner().invoke(main, ["scan", str(snap)])

    assert "only take effect with --against" not in result.output

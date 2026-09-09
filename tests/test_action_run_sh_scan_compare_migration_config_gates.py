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

"""Project-config-driven gate conditions for ``action/run.sh``'s ``mode:
scan`` -> ``abicheck compare`` migration (ADR-068 Phase 4 item 1,
``docs/contribute/plans/one-comparison-product.md``).

Split out of ``test_action_run_sh_scan_compare_migration.py`` once that file
grew past the architecture gate's 1200-line test-file cap (the same reason
``test_mutation_run_scoping.py``/``test_mutation_per_module_scoping.py``
were split out of ``test_mutation_score_gate.py`` -- see ``tests/CLAUDE.md``).
Covers every gate condition keyed off an ``.abicheck.yml``/``.abicheck.yaml``
project config (explicit ``build-config``, a config discovered from
``--sources``' own tree, or the cwd-upward walk) rather than a dedicated
Action input or ``extra-args`` -- the sibling module keeps the dispatch-gate
basics, capability/extra-args conditions, and the native-baseline
header/include reuse mechanics.

Shares the identical harness (parse the real ``run.sh`` mode-branch region,
run it with a scripted ``INPUT_*`` environment, capture the resulting ``CMD``
array) as the parent module -- duplicated rather than imported, matching the
"parse the real file, don't hand-copy it" discipline every sibling scan-mode
test module (``test_action_run_sh_build_target.py``,
``test_action_run_sh_artifact_set.py``, ...) already follows independently.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_END_MARKER = 'if [[ "${INPUT_VERBOSE:-false}" == "true" ]]; then'


def _mode_branches_region() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    return text[: text.index(_END_MARKER)]


def _bash_executable() -> str:
    if os.name != "nt":
        return "bash"
    for candidate in (
        os.environ.get("GIT_BASH_PATH"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return "bash"


def _run_cmd(env_extra: dict[str, str], cwd: Path | None = None) -> list[str]:
    script = _mode_branches_region() + "\nprintf '%s\\x1f' ${CMD[@]+\"${CMD[@]}\"}\n"
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    # Ambient INPUT_* variables are dropped, not just overlaid: every
    # assertion in this module reads the gate decision from `cmd[1]`
    # ("compare" vs "scan"), and a stray ambient INPUT_BUDGET/INPUT_DEPTH/
    # INPUT_FORMAT (e.g. leaked from the calling process's own environment)
    # would silently flip that decision. The sibling module
    # (test_action_run_sh_scan_cross_source_fallback.py) already filters
    # these out for the identical reason (CodeRabbit review).
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(env_extra)
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=None if cwd is None else str(cwd),
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        raise AssertionError(
            f"harness script failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return [item for item in result.stdout.split("\x1f") if item]


def _base_env(**extra: str) -> dict[str, str]:
    return {
        "INPUT_MODE": "scan",
        "INPUT_NEW_LIBRARY": "lib.so",
        "INPUT_AGAINST": "baseline.json",
        "INPUT_FORMAT": "json",
        **extra,
    }


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanStaysOnLegacyCliForSourceMethodConfig:
    """Fourth Codex review round, P1 (fresh evidence): an auto-discovered
    ``.abicheck.yml``/``.abicheck.yaml`` stating an explicit ``source:
    {method: auto}`` hits the identical auto-depth-resolution mismatch class
    as ``since``/``changed-path`` and ``build-info``-without-``sources``,
    but via project config rather than any Action input -- `compare`'s own
    auto-resolution has no equivalent for this value and raises a usage
    error outright (`scan --dry-run` resolves it to the PR preset's
    `source-target`).

    A fifth review round found the original ``method: auto``-only check too
    narrow: a NON-``auto`` value (e.g. ``s1``) diverges too, just
    differently -- ``scan``'s own risk-scored preset still resolves
    `source-target` regardless of the pinned method, while `compare`
    genuinely honors the pinned value and resolves `build` instead, for the
    identical inputs. Widened to match ANY `source.method` setting, not
    just the one value known to hard-fail; parametrized across both to
    prove the widened check covers the class, not just the originally
    reported value.

    A sixth review round (two Codex passes plus a CodeRabbit pass) found two
    more gaps in the same check: it only ever looked at
    `$PWD/.abicheck.yml`/`.abicheck.yaml`, missing a `source.method` living
    in a `build-config`-named config file elsewhere (`--config FILE`
    selects a TRUSTED project config explicitly, per `scan --help-all`; cwd
    auto-discovery applies only when it's omitted) -- and it required
    `method` to start a line, missing YAML's flow-style spelling
    (`source: {method: auto}`, on one line).

    An eighth review round found one more spelling gap: valid YAML permits
    quoting any mapping key (`source: {"method": auto}`, or an indented
    `"method": auto`), which the project-config loader accepts and `scan`
    resolves identically to the unquoted spelling, but the check's own
    pattern required a bare `method` immediately after its prefix.
    """

    def _run_cmd_in(self, cwd: Path, env_extra: dict[str, str]) -> list[str]:
        # CodeRabbit review, sixth round: reuses `_run_cmd`'s own `cwd`
        # parameter instead of duplicating its script build/temp-file/
        # environment-filtering/failure-assertion logic a second time here.
        return _run_cmd(env_extra, cwd=cwd)

    @pytest.mark.parametrize("method", ["auto", "s1"])
    def test_config_with_source_method_stays_on_scan(
        self, tmp_path: Path, method: str
    ) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            f"source:\n  method: {method}\n", encoding="utf-8"
        )
        cmd = self._run_cmd_in(tmp_path, _base_env())
        assert cmd[1] == "scan", cmd

    def test_config_without_source_method_auto_still_migrates(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "scope:\n  public: true\n", encoding="utf-8"
        )
        cmd = self._run_cmd_in(tmp_path, _base_env())
        assert cmd[1] == "compare", cmd

    @pytest.mark.parametrize("method", ["auto", "s1"])
    def test_flow_style_source_method_stays_on_scan(
        self, tmp_path: Path, method: str
    ) -> None:
        # CodeRabbit review, sixth round, fresh evidence: YAML permits a
        # flow-style mapping on one line (`source: {method: auto}`, this
        # function's own docstring example) in addition to the block style
        # already covered above -- the original pattern required `method`
        # to start a line, so this spelling fell through unmatched and
        # silently migrated to `compare`, hitting the exact usage error
        # this gate exists to prevent.
        (tmp_path / ".abicheck.yml").write_text(
            f"source: {{method: {method}}}\n", encoding="utf-8"
        )
        cmd = self._run_cmd_in(tmp_path, _base_env())
        assert cmd[1] == "scan", cmd

    @pytest.mark.parametrize(
        "config_text",
        [
            'source: {"method": auto}\n',
            "source: {'method': auto}\n",
            'source:\n  "method": auto\n',
            "source:\n  'method': s1\n",
        ],
    )
    def test_quoted_source_method_key_stays_on_scan(
        self, tmp_path: Path, config_text: str
    ) -> None:
        # Eighth Codex review round, P1, fresh evidence: valid YAML permits
        # quoting any mapping key -- the project-config loader accepts it
        # and `scan` resolves it identically to the unquoted spelling, but
        # the original pattern required a bare `method` immediately after
        # its prefix.
        (tmp_path / ".abicheck.yml").write_text(config_text, encoding="utf-8")
        cmd = self._run_cmd_in(tmp_path, _base_env())
        assert cmd[1] == "scan", cmd

    def test_no_config_still_migrates(self, tmp_path: Path) -> None:
        cmd = self._run_cmd_in(tmp_path, _base_env())
        assert cmd[1] == "compare", cmd

    def test_pinned_depth_with_auto_method_config_still_stays_on_scan(
        self, tmp_path: Path
    ) -> None:
        # Overlaps with the pinned-depth condition already covered
        # elsewhere -- proves the new config check doesn't need a pinned
        # depth to fire, and a pinned depth alone (unrelated to config)
        # already keeps this on scan regardless.
        (tmp_path / ".abicheck.yml").write_text(
            "source:\n  method: auto\n", encoding="utf-8"
        )
        cmd = self._run_cmd_in(tmp_path, _base_env(INPUT_DEPTH="source"))
        assert cmd[1] == "scan", cmd

    def test_build_config_source_method_stays_on_scan(self, tmp_path: Path) -> None:
        # Second Codex review round, P1 (fresh evidence): `build-config`/
        # `--config FILE` selects a TRUSTED project config explicitly
        # (`scan --help-all`) -- cwd auto-discovery applies only when it's
        # omitted. The earlier fix only ever looked at `$PWD/.abicheck.yml`,
        # so a `source.method` living in a `build-config`-named file
        # elsewhere (not at the checkout root, and not named
        # `.abicheck.yml`/`.abicheck.yaml` at all) went undetected.
        config_dir = tmp_path / "config_elsewhere"
        config_dir.mkdir()
        config_file = config_dir / "my-abicheck-config.yml"
        config_file.write_text("source:\n  method: auto\n", encoding="utf-8")
        cmd = self._run_cmd_in(tmp_path, _base_env(INPUT_BUILD_CONFIG=str(config_file)))
        assert cmd[1] == "scan", cmd

    def test_build_config_overrides_unrelated_cwd_config(self, tmp_path: Path) -> None:
        # An explicit --config means abicheck never falls back to cwd
        # auto-discovery at all -- a `.abicheck.yml` that happens to also
        # sit at $PWD (with no source.method of its own) must not be
        # consulted once `build-config` names a different file.
        (tmp_path / ".abicheck.yml").write_text(
            "scope:\n  public: true\n", encoding="utf-8"
        )
        config_file = tmp_path / "explicit-config.yml"
        config_file.write_text("scope:\n  public: true\n", encoding="utf-8")
        cmd = self._run_cmd_in(tmp_path, _base_env(INPUT_BUILD_CONFIG=str(config_file)))
        assert cmd[1] == "compare", cmd

    def test_missing_build_config_file_does_not_crash(self, tmp_path: Path) -> None:
        # A build-config path that doesn't exist (a real usage error the
        # actual CLI invocation will itself surface) must not crash this
        # bash-side heuristic -- it simply finds no source.method to detect.
        cmd = self._run_cmd_in(
            tmp_path,
            _base_env(INPUT_BUILD_CONFIG=str(tmp_path / "does-not-exist.yml")),
        )
        assert cmd[1] == "compare", cmd

    def test_sources_tree_config_stays_on_scan(self, tmp_path: Path) -> None:
        # Ninth Codex review round, P1, fresh evidence: `scan` resolves its
        # own project config via `discover_build_config(sources)` when
        # `--sources` is given and no `--build-config` was -- checking the
        # `--sources` tree's OWN root, never `$PWD`. The previous version of
        # this check covered only an explicit `build-config` and `$PWD`
        # auto-discovery, entirely missing a `source.method` living in a
        # config discovered from `--sources`' own tree instead.
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        (sources_dir / ".abicheck.yml").write_text(
            "source:\n  method: auto\n", encoding="utf-8"
        )
        cmd = self._run_cmd_in(tmp_path, _base_env(INPUT_SOURCES=str(sources_dir)))
        assert cmd[1] == "scan", cmd

    def test_sources_tree_without_config_still_migrates(self, tmp_path: Path) -> None:
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        cmd = self._run_cmd_in(tmp_path, _base_env(INPUT_SOURCES=str(sources_dir)))
        assert cmd[1] == "compare", cmd

    def test_build_config_wins_over_sources_tree_config(self, tmp_path: Path) -> None:
        # An explicit build-config outranks --sources' own tree, exactly as
        # it outranks $PWD auto-discovery -- matching cli_scan.py's own
        # `_discover_scan_project_config` precedence (explicit config wins
        # outright over `discover_build_config(sources)`).
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        (sources_dir / ".abicheck.yml").write_text(
            "source:\n  method: auto\n", encoding="utf-8"
        )
        explicit_config = tmp_path / "explicit-config.yml"
        explicit_config.write_text("scope:\n  public: true\n", encoding="utf-8")
        cmd = self._run_cmd_in(
            tmp_path,
            _base_env(
                INPUT_SOURCES=str(sources_dir),
                INPUT_BUILD_CONFIG=str(explicit_config),
            ),
        )
        assert cmd[1] == "compare", cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestMigratedCompareForwardsSourcesTreeConfig:
    """Ninth Codex review round, P1, fresh evidence: the migrated ``compare``
    invocation now explicitly forwards ``--config`` when ``--sources`` has
    its own discovered project config -- otherwise ALL of its settings
    (severity, scope, suppression, gate) silently disappeared under a
    migrated run, not just ``source.method`` (covered separately above).
    Reproduced directly: an old library exporting ``existing``, a new
    library exporting ``existing``+``added``, ``severity.addition: error``
    in the sources tree's own config -- ``scan`` exits 1 for the addition,
    the un-fixed migrated ``compare`` request exited 0 since its own
    default config resolution never looked inside ``--sources`` at all.
    """

    def test_config_forwarded_when_sources_has_its_own(self, tmp_path: Path) -> None:
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        config_file = sources_dir / ".abicheck.yml"
        config_file.write_text("scope:\n  public: true\n", encoding="utf-8")
        cmd = _run_cmd(_base_env(INPUT_SOURCES=str(sources_dir)))
        assert cmd[1] == "compare", cmd
        assert "--config" in cmd, cmd
        assert str(config_file) in cmd, cmd

    def test_no_config_forwarded_when_sources_has_none(self, tmp_path: Path) -> None:
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        # CodeRabbit review, fresh evidence: without `cwd=tmp_path`,
        # `_resolve_scan_effective_config_path`'s own upward-walk fallback
        # (once `--sources`' own tree has nothing) runs from wherever the
        # test process's real cwd happens to be, which could spuriously
        # discover a real `.abicheck.yml` above it and flip this assertion --
        # isolate the same way the sibling sources-tree-config tests already
        # do.
        cmd = _run_cmd(_base_env(INPUT_SOURCES=str(sources_dir)), cwd=tmp_path)
        assert cmd[1] == "compare", cmd
        assert "--config" not in cmd, cmd

    def test_dedicated_build_config_input_still_wins(self, tmp_path: Path) -> None:
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        (sources_dir / ".abicheck.yml").write_text(
            "scope:\n  public: true\n", encoding="utf-8"
        )
        explicit_config = tmp_path / "explicit-config.yml"
        explicit_config.write_text("scope:\n  public: true\n", encoding="utf-8")
        cmd = _run_cmd(
            _base_env(
                INPUT_SOURCES=str(sources_dir),
                INPUT_BUILD_CONFIG=str(explicit_config),
            )
        )
        assert cmd[1] == "compare", cmd
        assert "--config" in cmd, cmd
        assert str(explicit_config) in cmd, cmd
        assert str(sources_dir / ".abicheck.yml") not in cmd, cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanStaysOnLegacyCliForAbi3FloorConfig:
    """Ninth Codex review round, P1, fresh evidence: ``scan`` enables its
    stable-ABI audit ONLY from an explicit ``--abi3`` CLI value, never from
    project config, but ``compare`` ALSO enables it from a project config's
    ``python: {abi3_floor: ...}`` block -- a migrated invocation with no
    ``--abi3`` given at all could still run this audit under ``compare``
    and fail its own precondition (a non-CPython-extension pair) with exit
    7, where ``scan`` itself would simply never have looked at that key and
    exited 0. No ``python_stable_abi_violation`` finding is even produced
    in that failure, so the cross-source/pattern-verdict fallback's own
    after-the-fact detection can't catch it either -- this needs its own
    dedicated gate condition.
    """

    def test_cwd_config_with_abi3_floor_stays_on_scan(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            'python:\n  abi3_floor: "3.8"\n', encoding="utf-8"
        )
        cmd = _run_cmd(_base_env(), cwd=tmp_path)
        assert cmd[1] == "scan", cmd

    def test_sources_tree_config_with_abi3_floor_stays_on_scan(
        self, tmp_path: Path
    ) -> None:
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        (sources_dir / ".abicheck.yml").write_text(
            'python:\n  abi3_floor: "3.8"\n', encoding="utf-8"
        )
        cmd = _run_cmd(_base_env(INPUT_SOURCES=str(sources_dir)), cwd=tmp_path)
        assert cmd[1] == "scan", cmd

    def test_config_without_abi3_floor_still_migrates(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "scope:\n  public: true\n", encoding="utf-8"
        )
        cmd = _run_cmd(_base_env(), cwd=tmp_path)
        assert cmd[1] == "compare", cmd

    def test_no_config_still_migrates(self, tmp_path: Path) -> None:
        cmd = _run_cmd(_base_env(), cwd=tmp_path)
        assert cmd[1] == "compare", cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanStaysOnLegacyCliForDebugOptionsConfig:
    """Eleventh Codex review round, P1, fresh evidence: a project config's
    ``debug:`` namespace (``dwarf_only``/``format``/``debuginfod``/
    ``debuginfod_url``) is resolved and applied by ``compare``'s operand
    extraction, but ``scan``'s own baseline resolver never reads any of
    these four keys at all -- reproduced directly: a stripped ELF pair with
    headers and a config ``debug: {dwarf_only: true}``, ``scan`` reports
    ``COMPATIBLE`` reading the header AST (never even seeing the DWARF-only
    request), while the migrated ``compare`` invocation honors the config
    and reports ``COMPATIBLE_WITH_RISK`` from a completely different
    evidence source -- with real DWARF available, ``compare`` instead
    honors ``dwarf_only`` and ignores the supplied headers entirely, the
    opposite direction of divergence.
    """

    @pytest.mark.parametrize(
        "config_yaml",
        [
            "debug:\n  dwarf_only: true\n",
            "debug:\n  format: btf\n",
            "debug:\n  debuginfod: true\n",
            'debug:\n  debuginfod_url: "https://example.test"\n',
        ],
    )
    def test_cwd_config_with_debug_option_stays_on_scan(
        self, tmp_path: Path, config_yaml: str
    ) -> None:
        (tmp_path / ".abicheck.yml").write_text(config_yaml, encoding="utf-8")
        cmd = _run_cmd(_base_env(), cwd=tmp_path)
        assert cmd[1] == "scan", cmd

    def test_sources_tree_config_with_debug_option_stays_on_scan(
        self, tmp_path: Path
    ) -> None:
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        (sources_dir / ".abicheck.yml").write_text(
            "debug:\n  dwarf_only: true\n", encoding="utf-8"
        )
        cmd = _run_cmd(_base_env(INPUT_SOURCES=str(sources_dir)), cwd=tmp_path)
        assert cmd[1] == "scan", cmd

    def test_config_without_debug_options_still_migrates(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "scope:\n  public: true\n", encoding="utf-8"
        )
        cmd = _run_cmd(_base_env(), cwd=tmp_path)
        assert cmd[1] == "compare", cmd

    def test_no_config_still_migrates(self, tmp_path: Path) -> None:
        cmd = _run_cmd(_base_env(), cwd=tmp_path)
        assert cmd[1] == "compare", cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanStaysOnLegacyCliForMalformedAutoDiscoveredConfig:
    """Sixteenth Codex review round, P1, fresh evidence:
    ``cli_scan._discover_scan_project_config``'s own ``require_parseable``
    split -- an auto-discovered config (no explicit ``build-config``) that
    fails to parse is best-effort on ``scan`` (a warning, the config
    cleared, the run continues on CLI settings alone), but ``compare``
    (``cli_compare_helpers.run_compare``) raises ``click.UsageError``
    unconditionally on any parse failure, with no auto-discovered/explicit
    distinction of its own. Verified directly: ``.abicheck.yml`` containing
    ``scope: [`` (malformed YAML), ``scan --against`` completes the
    comparison, migrated `compare` exits 64 -- an existing, previously-
    passing scan silently becoming a hard ``ERROR``.
    """

    def test_cwd_malformed_config_stays_on_scan(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text("scope: [\n", encoding="utf-8")
        cmd = _run_cmd(_base_env(), cwd=tmp_path)
        assert cmd[1] == "scan", cmd

    def test_sources_tree_malformed_config_stays_on_scan(self, tmp_path: Path) -> None:
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        (sources_dir / ".abicheck.yml").write_text("scope: [\n", encoding="utf-8")
        cmd = _run_cmd(_base_env(INPUT_SOURCES=str(sources_dir)), cwd=tmp_path)
        assert cmd[1] == "scan", cmd

    def test_explicit_malformed_build_config_still_migrates(
        self, tmp_path: Path
    ) -> None:
        # An EXPLICIT build-config that fails to parse is a hard usage error
        # on `scan` too (`explicit_config or require_parseable` in
        # `_discover_scan_project_config`'s own branch) -- both commands
        # already agree here, so this must NOT force the legacy CLI; forcing
        # it would just delay an error the user should see either way.
        bad_config = tmp_path / "custom.yml"
        bad_config.write_text("scope: [\n", encoding="utf-8")
        cmd = _run_cmd(_base_env(INPUT_BUILD_CONFIG=str(bad_config)), cwd=tmp_path)
        assert cmd[1] == "compare", cmd

    def test_valid_cwd_config_still_migrates(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "scope:\n  public: true\n", encoding="utf-8"
        )
        cmd = _run_cmd(_base_env(), cwd=tmp_path)
        assert cmd[1] == "compare", cmd

    def test_no_config_still_migrates(self, tmp_path: Path) -> None:
        cmd = _run_cmd(_base_env(), cwd=tmp_path)
        assert cmd[1] == "compare", cmd

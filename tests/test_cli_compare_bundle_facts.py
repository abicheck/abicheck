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

"""Tests for ``compare --old-bundle-facts`` (frontends/cli/commands/compare_bundle_facts.py).

Exercises the real CLI entry point end to end -- unlike
``TestCompareReleaseAgainstBundleFacts`` in ``test_bundle_side_input.py``,
which calls ``compare_release_against_bundle_facts`` directly as a Python
API and never touches Click parsing/dispatch at all. This closes the gap
that function's own docstring names: "reachable from a real Python caller
today; it is not reachable from ``abicheck compare ...`` yet."
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.bundle_facts import capture_bundle_facts
from abicheck.cli import main
from abicheck.cli_resolve import _resolve_input
from abicheck.serialization import save_bundle_facts


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


def _build_so(tmp_path: Path, name: str, body: str) -> Path:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc is not available")
    src = tmp_path / f"{name}.c"
    src.write_text(body)
    out = tmp_path / name
    res = subprocess.run(
        [
            gcc,
            "-shared",
            "-fPIC",
            "-g",
            "-O0",
            str(src),
            "-o",
            str(out),
            f"-Wl,-soname,{name}",
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        pytest.fail(f"gcc failed: {res.stderr}")
    return out


def _write_old_facts(tmp_path: Path, old_dir: Path, so_path: Path, key: str) -> Path:
    old_snapshot = _resolve_input(
        so_path, [], [], "old", "c++", include_dependencies=True
    )
    facts = capture_bundle_facts({key: old_snapshot})
    facts_path = tmp_path / "old.bundlefacts.json"
    save_bundle_facts(facts, facts_path)
    return facts_path


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="Uses the GNU ld flag -Wl,-soname; ELF/Linux-only bundle analysis "
    "(matches TestCompareReleaseAgainstBundleFacts's identical guard).",
)
@pytest.mark.integration
class TestCompareOldBundleFacts:
    def test_breaking_change_detected_through_the_cli(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _build_so(old_dir, "libreal.so", "int add(int a, int b) { return a + b; }\n")
        _build_so(
            new_dir,
            "libreal.so",
            "int add(int a, int b, int c) { return a + b + c; }\n",
        )
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--format",
            "json",
        )

        assert code == 4, out
        payload = json.loads(out)
        assert payload["mode"] == "bundle_facts"
        assert payload["verdict"] == "BREAKING"
        lib = payload["libraries"]["libreal.so"]
        assert lib["verdict"] == "BREAKING"
        assert any(c["kind"] == "func_params_changed" for c in lib["changes"])

    def test_unchanged_library_is_no_change(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--format",
            "json",
        )

        assert code == 0, out
        payload = json.loads(out)
        assert payload["verdict"] == "NO_CHANGE"

    def test_markdown_format_renders(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--format",
            "markdown",
        )

        assert code == 0, out
        assert "Bundle-facts comparison" in out
        assert "libreal.so" in out

    def test_suppress_flows_through_to_the_per_library_diff(
        self, tmp_path: Path
    ) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _build_so(old_dir, "libreal.so", "int add(int a, int b) { return a + b; }\n")
        _build_so(
            new_dir,
            "libreal.so",
            "int add(int a, int b, int c) { return a + b + c; }\n",
        )
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )
        suppress_path = tmp_path / "suppress.yaml"
        suppress_path.write_text(
            "version: 1\n"
            "suppressions:\n"
            "  - change_kind: func_params_changed\n"
            "    symbol: add\n"
            '    reason: "test suppression"\n'
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--include-system-declarations",
            "--suppress",
            str(suppress_path),
            "--format",
            "json",
        )

        assert code == 0, out
        payload = json.loads(out)
        assert payload["verdict"] == "NO_CHANGE"
        lib = payload["libraries"]["libreal.so"]
        assert lib["suppression"]["suppressed_count"] == 1

    def test_max_json_object_nodes_override_is_honored(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--max-json-object-nodes",
            "1",
            "--format",
            "json",
        )

        # A real facts document has more than one JSON container; a budget
        # of 1 must reject it with a clean CLI error, not a raw traceback.
        assert code != 0
        assert "JSON containers" in out
        assert "Traceback" not in out

    def test_format_sarif_is_rejected(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--format",
            "sarif",
        )

        assert code == 64
        assert "not available" in out

    def test_fail_on_removed_library_is_rejected(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--fail-on-removed-library",
            "--format",
            "json",
        )

        assert code == 64
        assert "--fail-on-removed-library" in out

    def test_bundle_facts_out_together_with_old_bundle_facts_is_rejected(
        self, tmp_path: Path
    ) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        _build_so(old_dir, "libreal.so", body)
        _build_so(new_dir, "libreal.so", body)
        facts_path = _write_old_facts(
            tmp_path, old_dir, old_dir / "libreal.so", "libreal.so"
        )

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--bundle-facts-out",
            str(tmp_path / "out.json"),
            "--format",
            "json",
        )

        assert code == 64
        assert "--bundle-facts-out" in out


class TestCompareOldBundleFactsEarlyRejections:
    """The four Codex-review fixes: checks that must fire before any real
    facts loading/comparison happens, so none of them need a real gcc-built
    bundle -- a placeholder OLD_INPUT/NEW_INPUT pair is enough to prove the
    checks run early, unlike the ``TestCompareOldBundleFacts`` class above.
    """

    def test_dry_run_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--dry-run",
            "--format",
            "json",
        )

        assert code == 64
        assert "--dry-run" in out

    def test_contract_is_rejected(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--contract",
            "public",
            "--format",
            "json",
        )

        assert code == 64
        assert "--contract" in out

    def test_malformed_old_facts_json_is_a_clean_error(self, tmp_path: Path) -> None:
        facts_path = tmp_path / "malformed.json"
        facts_path.write_text("not json{")
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        code, out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--format",
            "json",
        )

        assert code == 1
        assert "Traceback" not in out

    def test_includes_from_config_are_merged_and_forwarded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex review: dispatch() used to re-derive `includes` from the raw
        # CLI kwargs instead of the compile-context-resolved, config-merged
        # list -- so a `.abicheck.yml` `compile.include_dirs` entry was
        # silently dropped for --old-bundle-facts even though it reached
        # every other compare dispatch path. Proven directly by monkeypatching
        # the real dispatch() out and capturing what compare_cmd forwards to
        # it, rather than indirectly through a full gcc-built bundle: what
        # matters here is the kwargs handoff in compare.py, not
        # compare_release_against_bundle_facts's own behavior (already
        # covered by TestCompareOldBundleFacts above).
        facts_path = tmp_path / "old.bundlefacts.json"
        facts_path.write_text("{}")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        config_path = tmp_path / ".abicheck.yml"
        extra_include = tmp_path / "extra_include"
        extra_include.mkdir()
        config_path.write_text(f"compile:\n  include_dirs:\n    - {extra_include}\n")

        from abicheck.frontends.cli.commands import compare_bundle_facts

        captured: dict[str, object] = {}

        def _fake_dispatch(*, compile_context: object, **kwargs: object) -> None:
            captured["includes"] = kwargs.get("includes")

        monkeypatch.setattr(compare_bundle_facts, "dispatch", _fake_dispatch)

        code, _out = _invoke(
            "compare",
            str(facts_path),
            str(new_dir),
            "--old-bundle-facts",
            "--config",
            str(config_path),
            "--format",
            "json",
        )

        assert code == 0
        forwarded_includes = captured.get("includes")
        assert forwarded_includes is not None
        assert extra_include in forwarded_includes  # type: ignore[operator]

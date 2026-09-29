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

"""The `npx abicheck-skills` package (ADR-058 publication step).

Three layers: the Python build script's own contract (manifest shape,
version sync), the Node installer's unit tests, and one end-to-end run of
the real publication path -- stage, `npm pack`, `npx` from the tarball into
a scratch project -- asserting the installed tree is byte-identical to what
`gen_agent_skills.render_all` renders. That last oracle is the renderer, not
the build script: a staging bug that dropped, renamed or rewrote a file
would otherwise pass a test that compared the build script with itself.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_npm_skill_package as build  # noqa: E402
import gen_agent_skills as gen  # noqa: E402

PACKAGE_DIR = ROOT / "packages" / "abicheck-skills"

_NODE = shutil.which("node")
_NPM = shutil.which("npm")
_NPX = shutil.which("npx")


class TestManifest:
    def test_manifest_lists_exactly_the_rendered_files(self):
        rendered = gen.render_all()
        manifest = build.build_manifest(rendered, "9.9.9")
        listed = {
            f"{skill['name']}/{rel}"
            for skill in manifest["skills"]
            for rel in skill["files"]
        }
        assert listed == set(rendered)
        assert manifest["package_version"] == "9.9.9"
        for skill in manifest["skills"]:
            assert "SKILL.md" in skill["files"]
            assert skill["abicheck_version_range"].startswith(">=")

    def test_a_skill_without_a_version_range_is_rejected(self):
        rendered = {"demo/SKILL.md": "---\nname: demo\n---\nbody\n"}
        with pytest.raises(gen.SkillGenerationError, match="abicheck-version-range"):
            build.build_manifest(rendered, "1.0.0")

    def test_a_skill_without_skill_md_is_rejected(self):
        with pytest.raises(gen.SkillGenerationError, match="SKILL.md"):
            build.build_manifest({"demo/references/a.md": "x"}, "1.0.0")

    def test_stage_writes_rendered_content_and_manifest(self, tmp_path):
        rendered = gen.render_all()
        build.stage(rendered, tmp_path / "skills", "1.0.0")
        for key, content in rendered.items():
            assert (tmp_path / "skills" / key).read_text(encoding="utf-8") == content
        manifest = json.loads(
            (tmp_path / "skills" / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["package_version"] == "1.0.0"


class TestVersionSync:
    def test_committed_package_version_matches_pyproject(self):
        assert build.package_version_problem() is None

    def test_check_mode_passes_on_the_committed_tree(self):
        assert build.main(["--check"]) == 0

    def test_drift_is_reported(self, monkeypatch):
        monkeypatch.setattr(build, "abicheck_version", lambda: "0.0.1")
        problem = build.package_version_problem()
        assert problem is not None and "0.0.1" in problem

    def test_package_json_ships_only_installer_and_skills(self):
        pkg = json.loads((PACKAGE_DIR / "package.json").read_text(encoding="utf-8"))
        assert pkg["name"] == "abicheck-skills"
        assert pkg["bin"] == {"abicheck-skills": "bin/abicheck-skills.mjs"}
        assert set(pkg["files"]) == {"bin/", "skills/", "README.md", "LICENSE"}
        assert not pkg.get("dependencies"), (
            "the npx installer must stay dependency-free"
        )


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_node_installer_unit_tests():
    tests = sorted(str(p) for p in (PACKAGE_DIR / "test").glob("*.test.mjs"))
    assert tests
    proc = subprocess.run(
        [_NODE, "--test", *tests], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(_NPM is None or _NPX is None, reason="npm/npx not installed")
def test_npx_install_from_packed_tarball_matches_the_renderer(tmp_path):
    package = tmp_path / "pkg"
    shutil.copytree(
        PACKAGE_DIR,
        package,
        ignore=shutil.ignore_patterns("skills", "*.tgz", "node_modules", "LICENSE"),
    )
    rendered = gen.render_all()
    build.stage(rendered, package / "skills", build.abicheck_version())
    shutil.copyfile(ROOT / "LICENSE", package / "LICENSE")

    packed = subprocess.run(
        [_NPM, "pack", "--silent", "--pack-destination", str(tmp_path)],
        cwd=package,
        env={**os.environ, "npm_config_offline": "true"},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert packed.returncode == 0, packed.stderr
    tarball = tmp_path / packed.stdout.strip().splitlines()[-1]
    assert tarball.is_file()

    project = tmp_path / "project"
    project.mkdir()
    env = {
        **os.environ,
        "npm_config_cache": str(tmp_path / "npm-cache"),
        "npm_config_update_notifier": "false",
        # The tarball has no dependencies, so nothing needs the registry;
        # offline mode makes that a property of the test, not an accident.
        "npm_config_offline": "true",
    }
    ran = subprocess.run(
        [
            _NPX,
            "--yes",
            f"--package={tarball}",
            "abicheck-skills",
            "install",
            "--agent",
            "all",
            "--dry-run",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert list(project.iterdir()) == []

    ran = subprocess.run(
        [_NPX, "--yes", f"--package={tarball}", "abicheck-skills", "--agent", "all"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr

    for tree in (".claude/skills", ".agents/skills", ".gemini/skills"):
        root = project / tree
        installed = {
            p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
            for p in root.rglob("*")
            if p.is_file() and p.name != ".abicheck-skill.json"
        }
        assert installed == rendered, tree
        owners = list(root.glob("*/.abicheck-skill.json"))
        assert owners and all(
            json.loads(o.read_text(encoding="utf-8"))["version"]
            == build.abicheck_version()
            for o in owners
        )

    removed = subprocess.run(
        [
            _NPX,
            "--yes",
            f"--package={tarball}",
            "abicheck-skills",
            "uninstall",
            "--agent",
            "all",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert removed.returncode == 0, removed.stderr
    for tree in (".claude/skills", ".agents/skills", ".gemini/skills"):
        assert list((project / tree).iterdir()) == []

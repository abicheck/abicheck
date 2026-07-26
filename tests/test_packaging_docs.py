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

"""Packaging/installation-docs contract: pip is the lightweight/core
distribution, conda-forge is the recommended full-scanner installation, and
neither README.md nor getting-started.md ever tells a reader to
``pip install castxml`` (that installs the unmaintained legacy PyPI
distribution abicheck's own version gate rejects by default -- see
castxml_policy.py). Also checks pyproject.toml's core dependency list
directly, as a fast belt-and-suspenders companion to the built-wheel check
in scripts/check_distribution_metadata.py (which needs `build`/`twine`
installed and dist/ populated, so it can't run in the default fast lane).
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent

_FENCED_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)


def _read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def _fenced_code_blocks(text: str) -> list[str]:
    """Extract fenced code-block bodies -- the only place a reader would
    actually copy-paste a command from, as opposed to inline-code mentioned
    inside a "don't do this" warning sentence."""
    return _FENCED_CODE_BLOCK_RE.findall(text)


def test_pyproject_core_dependencies_have_no_castxml() -> None:
    with (ROOT / "pyproject.toml").open("rb") as f:
        project = tomllib.load(f)["project"]
    core_deps = [d.lower() for d in project["dependencies"]]
    assert not any("castxml" in d for d in core_deps), core_deps


def test_pyproject_optional_extras_have_no_castxml() -> None:
    with (ROOT / "pyproject.toml").open("rb") as f:
        project = tomllib.load(f)["project"]
    extras = project.get("optional-dependencies", {})
    for extra_name, deps in extras.items():
        lowered = [d.lower() for d in deps]
        assert not any("castxml" in d for d in lowered), (extra_name, lowered)


def test_readme_never_recommends_pip_install_castxml() -> None:
    """`pip install castxml` may be *mentioned* inline as something to warn
    against, but must never appear as a runnable command in a fenced code
    block -- that would be an actual recommendation to run it."""
    text = _read("README.md")
    for block in _fenced_code_blocks(text):
        assert "pip install castxml" not in block.lower()


def test_getting_started_never_recommends_pip_install_castxml() -> None:
    text = _read("docs/start/getting-started.md")
    for block in _fenced_code_blocks(text):
        assert "pip install castxml" not in block.lower()


def test_readme_presents_conda_forge_before_pip_lightweight_install() -> None:
    """The Installation section must lead with the conda-forge full-scanner
    install and frame pip as the lightweight/core alternative -- not the
    other way around (regression: README used to list bare `pip install
    abicheck` first, with conda-forge as an afterthought `# or` line)."""
    text = _read("README.md")
    conda_idx = text.find("conda create -n abicheck -c conda-forge")
    pip_idx = text.find("pip install abicheck")
    assert conda_idx != -1, "README is missing the conda-forge full-install command"
    assert pip_idx != -1, "README is missing the pip install command"
    assert conda_idx < pip_idx, (
        "README must present the conda-forge full installation before the "
        "pip lightweight/core installation"
    )
    assert "lightweight" in text.lower() or "core" in text.lower()


def test_install_page_presents_conda_forge_before_pip_lightweight_install() -> None:
    """Install content lives on docs/start/install.md, not getting-started.md
    -- getting-started.md was trimmed to a thin hub linking to install.md,
    first-check.md, and first-report.md (docs/AGENTS.md's "Layout" section)."""
    text = _read("docs/start/install.md")
    conda_idx = text.find("conda create -n abicheck -c conda-forge")
    pip_idx = text.find("pip install abicheck")
    assert conda_idx != -1, "install.md is missing the conda-forge full-install command"
    assert pip_idx != -1, "install.md is missing the pip install command"
    assert conda_idx < pip_idx, (
        "install.md must present the conda-forge full installation "
        "before the pip lightweight/core installation"
    )

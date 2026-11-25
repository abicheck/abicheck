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

"""Packaging/installation-docs contract: pip installs the maintained official
CastXML wheel by default, and conda-forge remains the recommended full-toolchain
installation. Also checks pyproject.toml's core dependency list
directly, as a fast belt-and-suspenders companion to the built-wheel check
in scripts/check_distribution_metadata.py (which needs `build`/`twine`
installed and dist/ populated, so it can't run in the default fast lane).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def test_pyproject_default_dependencies_pin_castxml() -> None:
    with (ROOT / "pyproject.toml").open("rb") as f:
        project = tomllib.load(f)["project"]
    core_deps = [d.lower() for d in project["dependencies"]]
    assert "castxml==0.8.0" in core_deps


def test_castxml_is_not_an_optional_extra() -> None:
    with (ROOT / "pyproject.toml").open("rb") as f:
        project = tomllib.load(f)["project"]
    extras = project.get("optional-dependencies", {})
    for extra_name, deps in extras.items():
        assert not any("castxml" in d.lower() for d in deps), (extra_name, deps)


def test_readme_documents_default_castxml_install() -> None:
    text = _read("README.md")
    assert "pip install abicheck" in text
    assert "includes the official" in text


def test_install_page_documents_default_castxml_install() -> None:
    assert "pip install abicheck" in _read("docs/start/install.md")
    assert "includes the maintained CastXML 0.8.0" in _read("docs/start/install.md")


def test_readme_presents_conda_forge_before_pip_install() -> None:
    """The Installation section must lead with the conda-forge full-scanner
    install and frame pip as the simpler PyPI alternative -- not the
    other way around (regression: README used to list bare `pip install
    abicheck` first, with conda-forge as an afterthought `# or` line)."""
    text = _read("README.md")
    conda_idx = text.find("conda create -n abicheck -c conda-forge")
    pip_idx = text.find("pip install abicheck")
    assert conda_idx != -1, "README is missing the conda-forge full-install command"
    assert pip_idx != -1, "README is missing the pip install command"
    assert conda_idx < pip_idx, (
        "README must present the conda-forge full installation before the "
        "PyPI installation"
    )
    assert "CastXML 0.8.0" in text


def test_install_page_presents_conda_forge_before_pip_install() -> None:
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
        "before the PyPI installation"
    )

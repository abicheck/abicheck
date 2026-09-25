"""``scope.dependency_evidence`` (ADR-075 D6): only ``full`` is accepted, and
the project config's ownership keys reach the ownership request resolved
against the project root, not the working directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.workflows.extraction import load_build_config
from abicheck.workflows.ownership_request import (
    ownership_request_from_config,
    with_target_roots,
)


def _load(tmp_path: Path, text: str):
    path = tmp_path / ".abicheck.yml"
    path.write_text(text)
    return load_build_config(path)


def test_full_is_accepted(tmp_path: Path) -> None:
    cfg = _load(tmp_path, "scope:\n  dependency_evidence: full\n")
    assert cfg.ownership.dependency_evidence == "full"


def test_absent_key_means_full(tmp_path: Path) -> None:
    cfg = _load(tmp_path, "scope:\n  public_header_dirs: [include]\n")
    assert cfg.ownership.dependency_evidence == "full"


def test_referenced_is_rejected_with_its_own_message(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'referenced' is not supported yet"):
        _load(tmp_path, "scope:\n  dependency_evidence: referenced\n")


@pytest.mark.parametrize("value", ["partial", "", "FULL", 1, True])
def test_anything_else_is_rejected(tmp_path: Path, value: object) -> None:
    with pytest.raises(ValueError, match="dependency_evidence"):
        _load(tmp_path, f"scope:\n  dependency_evidence: {value!r}\n")


def test_config_roots_resolve_against_the_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    cfg = _load(
        project,
        "scope:\n  public_header_dirs: [include]\n  dependencies:\n"
        "    - name: fmt\n      header_roots: [third/fmt]\n",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    request = ownership_request_from_config(cfg, project)
    assert request is not None
    assert request.project_root == str(project.resolve())
    assert request.rules.target_roots == (str(project.resolve() / "include"),)
    assert request.rules.dependencies[0].header_roots == (
        str(project.resolve() / "third" / "fmt"),
    )


def test_no_config_means_no_request(tmp_path: Path) -> None:
    assert ownership_request_from_config(None, tmp_path) is None


def test_target_roots_are_header_dirs_and_public_header_dirs_only(
    tmp_path: Path,
) -> None:
    hdir = tmp_path / "include"
    hdir.mkdir()
    hfile = hdir / "api.h"
    hfile.write_text("")
    pdir = tmp_path / "pub"
    request = with_target_roots(None, [hdir, hfile], [pdir])
    # A -H *file* is not a root; a -H directory and public_header_dirs are.
    assert request.rules.target_roots == (str(hdir.resolve()), str(pdir.resolve()))

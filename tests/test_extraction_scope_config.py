"""``scope.dependency_evidence`` (ADR-075 D6): only ``full`` is accepted, and
the project config's ownership keys reach the ownership request resolved
against the project root, not the working directory."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from abicheck.buildsource.build_config_scope import dependency_evidence_findings
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


@pytest.mark.parametrize(
    "value", ["partial", "", "FULL", 1, True, ["full"], {"a": 1}, [], None]
)
def test_anything_else_is_rejected(tmp_path: Path, value: object) -> None:
    with pytest.raises(ValueError, match="dependency_evidence"):
        _load(tmp_path, f"scope:\n  dependency_evidence: {value!r}\n")


_YAML_VALUES = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=8),
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.text(max_size=4), inner, max_size=3)
    ),
    max_leaves=6,
)


@given(_YAML_VALUES)
def test_every_yaml_shape_is_a_finding_or_accepted_never_an_exception(
    value: object,
) -> None:
    """Oracle: exactly the string ``full`` is accepted; any other scalar,
    list or mapping YAML can produce is a finding, never a raised error."""
    findings = dependency_evidence_findings(value)
    assert (findings == []) == (isinstance(value, str) and value == "full")


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


def test_input_spec_of_carries_the_ownership_request(tmp_path: Path) -> None:
    """``InputSpec.of`` is the loose-value builder the typed API documents:
    every field it mirrors must pass through, ownership included."""
    import dataclasses

    from abicheck.model.ownership_rules import OwnershipRequest, OwnershipRules
    from abicheck.workflows.request_inputs import InputSpec

    request = OwnershipRequest(OwnershipRules(target_roots=("inc",)), project_root=".")
    assert InputSpec.of(tmp_path, ownership=request).ownership == request
    # Every constructor field is reachable through `of` (the class of gap).
    import inspect

    of_params = set(inspect.signature(InputSpec.of).parameters) - {"cls"}
    assert {f.name for f in dataclasses.fields(InputSpec) if f.init} <= of_params

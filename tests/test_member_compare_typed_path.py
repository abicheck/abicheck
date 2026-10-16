"""A release member compares through its typed ``CompareRequest``, and that
request is the one the keyword API builds for the same operands.

The oracle is the pre-change route: ``run_compare(**keywords)`` with the
request's fields under their own names -- the keyword translation the member
performed before it built its typed request directly. It is pinned here, not
imported, because the keyword API is the documented public surface and must
keep behaving the same way.

Three properties, from the strongest structural check to the observable one:

1. **Request equality.** For a member with every field set away from its
   default, ``member_compare_request`` builds a ``CompareRequest`` equal to
   the one the keyword API hands to ``run_compare_request``. This covers the
   fields a JSON-snapshot compare cannot observe (headers, includes, PDBs,
   compile context, public header dirs, exclusions, env matrix).
2. **No silently dropped field.** Each request field, changed on its own,
   changes the member's ``CompareRequest``.
3. **Report parity.** On the F2 corpus, the member's rendered report equals
   the keyword route's report, byte for byte once parsed.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from _family_f2_routes import CORPUS

from abicheck import service_compare_pipeline
from abicheck.change_registry_types import Verdict
from abicheck.checker_policy import ChangeKind
from abicheck.compile_context import CompileContext
from abicheck.environment_matrix import EnvironmentMatrix
from abicheck.reporter import to_json
from abicheck.serialization import snapshot_to_json
from abicheck.service_compare_pipeline import run_compare
from abicheck.workflows.member_compare import compare_member
from abicheck.workflows.release_member_request import (
    MemberDelta,
    ReleaseMemberCompareRequest,
    member_compare_request,
    member_request,
)

_COMPARABLE = sorted(c for c in CORPUS if c != "scope_mismatch")


def _delta(root: Path) -> MemberDelta:
    return MemberDelta(
        old_input=root / "old.so",
        new_input=root / "new.so",
        old_pdb_path=root / "old.pdb",
        new_pdb_path=root / "new.pdb",
    )


def _rich_request(root: Path) -> ReleaseMemberCompareRequest:
    """Every field set away from its default, so a dropped field shows."""
    parent = ReleaseMemberCompareRequest(
        old_headers=[root / "old.h"],
        new_headers=[root / "new.h"],
        old_includes=[root / "old-inc"],
        new_includes=[root / "new-inc"],
        old_version="1.0",
        new_version="2.0",
        lang="c",
        lang_explicit=True,
        suppress=root / "suppress.yml",
        policy="sdk_vendor",
        policy_file_path=root / "policy.yml",
        scope_to_public_surface=False,
        pattern_verdicts=False,
        include_dependencies=True,
        contract_evaluation=False,
        contract_mode="exports",
        pack_policy_overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
        pack_internal_namespaces=("internal",),
        compile_context=CompileContext(),
        depth="headers",
        public_header_dirs=[root / "public"],
        collapse_versioned_symbols=True,
        project_policy_overrides={ChangeKind.FUNC_ADDED: Verdict.COMPATIBLE},
        env_matrix=EnvironmentMatrix(),
        exclude_headers=("detail/*",),
        acknowledgments_path=root / "ack.yml",
        acknowledgment_unacknowledged_additions="warn",
    )
    return member_request(parent, _delta(root))


def _keyword_api_kwargs(request: ReleaseMemberCompareRequest) -> dict[str, Any]:
    """The pre-change keyword translation: each request field under its own
    name. Pinned here as the oracle (see the module docstring)."""
    return {f.name: getattr(request, f.name) for f in dataclasses.fields(request)}


def test_member_request_equals_the_keyword_api_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _rich_request(tmp_path)
    seen: list[object] = []
    monkeypatch.setattr(
        service_compare_pipeline,
        "run_compare_request",
        lambda typed: seen.append(typed),
    )
    run_compare(**_keyword_api_kwargs(request))
    (expected,) = seen
    assert member_compare_request(request) == expected


#: A value per request field, each different from the default (or from the
#: delta's own default) so a field that does not reach the request is visible.
_SAMPLES: dict[str, Any] = {
    "old_input": Path("alt-old.so"),
    "new_input": Path("alt-new.so"),
    "old_pdb_path": Path("alt.pdb"),
    "new_pdb_path": Path("alt-new.pdb"),
    "old_headers": [Path("h.h")],
    "new_headers": [Path("h.h")],
    "old_includes": [Path("inc")],
    "new_includes": [Path("inc")],
    "old_version": "9",
    "new_version": "9",
    "lang": "c",
    "lang_explicit": True,
    "suppress": Path("s.yml"),
    "policy": "sdk_vendor",
    "policy_file_path": Path("p.yml"),
    "scope_to_public_surface": False,
    "pattern_verdicts": False,
    "include_dependencies": True,
    "contract_evaluation": False,
    "contract_mode": "all",
    "pack_policy_overrides": {ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
    "pack_internal_namespaces": ("x",),
    "compile_context": CompileContext(),
    "depth": "headers",
    "public_header_dirs": [Path("pub")],
    "collapse_versioned_symbols": True,
    "project_policy_overrides": {ChangeKind.FUNC_ADDED: Verdict.COMPATIBLE},
    "env_matrix": EnvironmentMatrix(),
    "exclude_headers": ("d/*",),
    "acknowledgments_path": Path("a.yml"),
    "acknowledgment_unacknowledged_additions": "warn",
}


def test_samples_cover_every_request_field() -> None:
    assert set(_SAMPLES) == {
        f.name for f in dataclasses.fields(ReleaseMemberCompareRequest)
    }


@pytest.mark.parametrize("name", sorted(_SAMPLES))
def test_each_request_field_reaches_the_member_request(
    tmp_path: Path, name: str
) -> None:
    base = member_request(ReleaseMemberCompareRequest(), _delta(tmp_path))
    changed = dataclasses.replace(base, **{name: _SAMPLES[name]})
    assert member_compare_request(changed) != member_compare_request(base), name


def _write_snapshot(path: Path, snap: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        snapshot_to_json(dataclasses.replace(snap, library="libfoo.so")),
        encoding="utf-8",
    )
    return path


def _report(result: Any) -> dict[str, Any]:
    return json.loads(to_json(result.diff, severity_config=result.severity_config))


@pytest.mark.parametrize("case", _COMPARABLE)
def test_member_report_equals_the_keyword_route_report(
    tmp_path: Path, case: str
) -> None:
    old_snap, new_snap = CORPUS[case]
    suppress = tmp_path / "suppress.yml"
    suppress.write_text("version: 1\nsuppressions: []\n", encoding="utf-8")
    request = member_request(
        ReleaseMemberCompareRequest(
            old_version="1.0",
            new_version="2.0",
            suppress=suppress,
            contract_mode="all",
            collapse_versioned_symbols=True,
            public_header_dirs=[tmp_path / "inc"],
            exclude_headers=("internal/*",),
            project_policy_overrides={ChangeKind.FUNC_ADDED: Verdict.COMPATIBLE},
        ),
        MemberDelta(
            old_input=_write_snapshot(tmp_path / "old" / "libfoo.json", old_snap),
            new_input=_write_snapshot(tmp_path / "new" / "libfoo.json", new_snap),
        ),
    )
    expected = run_compare(**_keyword_api_kwargs(request))
    assert _report(compare_member(request)) == _report(expected)

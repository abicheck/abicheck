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

"""``.abicheck.yml``'s ``contract.overlays.post_manifest`` (one-comparison-
product Phase 9c), the only spelling of the POST manifest overlay since
Phase 9d deleted ``compare --post-manifest``.

The oracle for "the key applies the overlay" is the Tier-2 comparison called
with the manifest's allowlist directly (``service.compare_snapshots(...,
public_surface_allowlist=contract_scope_allowlist(...))``), not the CLI's
own resolution: several independently-shaped pairs (demoted kernel churn, a committed
wrapper's real break, a wrapper the new manifest omits) must give the same
verdict and findings through the CLI key. The rest pins project-root-relative
resolution, the error naming the key, the retired flag, and the routes that
cannot apply an overlay (a stderr note, never a usage error).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.action_config_overlay import rebase_relative_config_paths
from abicheck.buildsource.build_config import BuildConfig
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.post_manifest import contract_scope_allowlist, load_manifest
from abicheck.serialization import snapshot_to_json
from abicheck.service import compare_snapshots


def _fn(name: str, ret: str = "void", params: tuple[str, ...] = ()) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _snap(functions: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(library="libpost.so", version="1", functions=functions)


def _manifest(path: Path, symbols: tuple[str, ...] = ("pp_foo",)) -> Path:
    exports = [
        {
            "name": s.removeprefix("pp_"),
            "c_symbol": s,
            "params": ["Float64"],
            "return_dtype": "Float64",
        }
        for s in symbols
    ]
    path.write_text(json.dumps({"post_abi": 1, "exports": exports}), encoding="utf-8")
    return path


#: (old functions, new functions): sibling shapes the overlay decides
#: differently, so a key that is read but mis-applied cannot pass all three.
PAIRS = {
    "kernel_churn_demoted": (
        [_fn("pp_foo"), _fn("__pp_foo_impl")],
        [_fn("pp_foo")],
    ),
    "committed_wrapper_breaks": (
        [_fn("pp_foo", "double", ("double",)), _fn("__pp_k")],
        [_fn("pp_foo", "float", ("float",)), _fn("__pp_k2")],
    ),
    "omitted_old_wrapper_kept": (
        [_fn("pp_foo"), _fn("pp_undeclared", "int", ("int",))],
        [_fn("pp_foo"), _fn("pp_undeclared", "long", ("long",))],
    ),
}


def _pair(root: Path, name: str) -> tuple[Path, Path]:
    old, new = PAIRS[name]
    old_p, new_p = root / f"{name}.old.json", root / f"{name}.new.json"
    old_p.write_text(snapshot_to_json(_snap(old)), encoding="utf-8")
    new_p.write_text(snapshot_to_json(_snap(new)), encoding="utf-8")
    return old_p, new_p


def _config(root: Path, text: str) -> Path:
    cfg = root / ".abicheck.yml"
    cfg.write_text(text, encoding="utf-8")
    return cfg


def _run(*args: str) -> tuple[int, dict, str]:
    res = CliRunner().invoke(main, ["compare", *args, "-o", "json=-"])
    out = res.stdout
    doc = json.loads(out[out.find("{") :]) if "{" in out else {}
    return res.exit_code, doc, res.stderr


def _outcome(doc: dict) -> tuple:
    return (
        doc.get("verdict"),
        sorted((c["kind"], c.get("symbol")) for c in doc.get("changes", [])),
        json.dumps(doc.get("surface_scope"), sort_keys=True),
    )


_KEY = "contract:\n  overlays:\n    post_manifest: m.json\n"


@pytest.mark.parametrize("name", sorted(PAIRS))
def test_the_config_key_applies_the_manifest_overlay(tmp_path: Path, name: str) -> None:
    old_p, new_p = _pair(tmp_path, name)
    manifest = _manifest(tmp_path / "m.json")
    old, new = (_snap(fns) for fns in PAIRS[name])
    oracle = compare_snapshots(
        old,
        new,
        public_surface_allowlist=contract_scope_allowlist(
            load_manifest(manifest), old, new
        ),
    )
    code, doc, err = _run(
        str(old_p), str(new_p), "--config", str(_config(tmp_path, _KEY))
    )
    assert doc.get("verdict") == oracle.verdict.value, err
    assert sorted(c["kind"] for c in doc.get("changes", [])) == sorted(
        c.kind.value for c in oracle.changes
    )
    unscoped = _run(str(old_p), str(new_p))
    if name == "kernel_churn_demoted":
        # Vacuity guard: the overlay really changed something on this pair.
        assert _outcome(doc) != _outcome(unscoped[1])
        assert code == 0 and unscoped[0] != 0


def test_a_relative_path_resolves_against_the_project_root(tmp_path: Path) -> None:
    """``.github/.abicheck.yml`` belongs to the repository root, so a relative
    overlay path is read from there, not from ``.github/`` -- and not from
    the current directory, which here is elsewhere entirely."""
    old_p, new_p = _pair(tmp_path, "kernel_churn_demoted")
    _manifest(tmp_path / "m.json")
    gh = tmp_path / ".github"
    gh.mkdir()
    cfg = gh / ".abicheck.yml"
    cfg.write_text(_KEY, encoding="utf-8")
    code, _doc, err = _run(str(old_p), str(new_p), "--config", str(cfg))
    assert code == 0, err


def test_an_unreadable_document_is_a_usage_error_naming_the_key(tmp_path: Path) -> None:
    old_p, new_p = _pair(tmp_path, "kernel_churn_demoted")
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    cfg = _config(tmp_path, "contract:\n  overlays:\n    post_manifest: bad.json\n")
    res = CliRunner().invoke(
        main, ["compare", str(old_p), str(new_p), "--config", str(cfg)]
    )
    assert res.exit_code == 64, res.output
    assert "contract.overlays.post_manifest" in res.output


def test_the_retired_flag_is_a_usage_error(tmp_path: Path) -> None:
    """Phase 9d (plan F-27): no alias, no silent ignore."""
    old_p, new_p = _pair(tmp_path, "kernel_churn_demoted")
    manifest = _manifest(tmp_path / "m.json")
    res = CliRunner().invoke(
        main, ["compare", str(old_p), str(new_p), "--post-manifest", str(manifest)]
    )
    assert res.exit_code == 64, res.output
    assert "No such option" in res.output and "--post-manifest" in res.output


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    old_d, new_d = tmp_path / "old", tmp_path / "new"
    old_d.mkdir()
    new_d.mkdir()
    old, new = PAIRS["kernel_churn_demoted"]
    (old_d / "libpost.json").write_text(snapshot_to_json(_snap(old)), encoding="utf-8")
    (new_d / "libpost.json").write_text(snapshot_to_json(_snap(new)), encoding="utf-8")
    return old_d, new_d


def test_the_config_key_is_noted_not_applied_on_a_directory_comparison(
    tmp_path: Path,
) -> None:
    old_d, new_d = _dirs(tmp_path)
    _manifest(tmp_path / "m.json")
    cfg = _config(tmp_path, _KEY)
    keyed = CliRunner().invoke(
        main, ["compare", str(old_d), str(new_d), "--config", str(cfg)]
    )
    plain = CliRunner().invoke(main, ["compare", str(old_d), str(new_d)])
    assert "contract.overlays.post_manifest is not applied" in keyed.stderr
    assert keyed.exit_code == plain.exit_code != 64


def test_the_config_key_is_noted_not_applied_on_a_no_baseline_audit(
    tmp_path: Path,
) -> None:
    _old_p, new_p = _pair(tmp_path, "kernel_churn_demoted")
    _manifest(tmp_path / "m.json")
    cfg = _config(tmp_path, _KEY)
    res = CliRunner().invoke(
        main, ["compare", "--no-baseline", str(new_p), "--config", str(cfg)]
    )
    assert res.exit_code != 64, res.output
    assert (
        "contract.overlays.post_manifest is not applied on a --no-baseline audit"
        in res.stderr
    )


@pytest.mark.parametrize(
    ("block", "fragment"),
    [
        ("contract: 3", "contract must be a mapping"),
        ("contract:\n  bogus: 1", "unknown .abicheck.yml key contract.'bogus'"),
        ("contract:\n  overlays: [m.json]", "contract.overlays must be a mapping"),
        (
            "contract:\n  overlays:\n    headers: x",
            "unknown contract overlay 'headers'",
        ),
        (
            "contract:\n  overlays:\n    post_manifest: ''",
            "must be a non-empty path string",
        ),
        (
            "contract:\n  overlays:\n    post_manifest: 7",
            "must be a non-empty path string",
        ),
    ],
)
def test_malformed_blocks_fail_strict_loading(block: str, fragment: str) -> None:
    import yaml

    with pytest.raises(ValueError) as info:
        BuildConfig.from_dict(yaml.safe_load(block))
    assert fragment in str(info.value)


@pytest.mark.parametrize("value", [None, "m.json", "/abs/m.json"])
def test_the_block_round_trips(value: str | None) -> None:
    cfg = BuildConfig(contract_post_manifest=value)
    again = BuildConfig.from_dict(cfg.to_dict())
    assert again.contract_post_manifest == value
    assert ("contract" in cfg.to_dict()) is (value is not None)


def test_the_action_relocation_keeps_the_overlay_path_pointing_home(
    tmp_path: Path,
) -> None:
    root = tmp_path / "proj"
    rebased = rebase_relative_config_paths(
        {"contract": {"overlays": {"post_manifest": "m.json"}}},
        found_path=root / ".abicheck.yml",
    )
    assert rebased["contract"]["overlays"]["post_manifest"] == str(
        (root / "m.json").resolve()
    )
    absolute = rebase_relative_config_paths(
        {"contract": {"overlays": {"post_manifest": "/x/m.json"}}},
        found_path=root / ".abicheck.yml",
    )
    assert absolute["contract"]["overlays"]["post_manifest"] == "/x/m.json"


@pytest.mark.parametrize(
    "exports",
    [
        [],
        [
            {
                "name": "other",
                "c_symbol": "pp_other",
                "params": [],
                "return_dtype": "Float64",
            }
        ],
    ],
    ids=["empty-manifest", "unrelated-manifest"],
)
def test_a_discovered_config_cannot_narrow_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exports: list
) -> None:
    """Codex security review (P1): a pull request can add or edit the
    auto-discovered ``.abicheck.yml`` in the very checkout being judged, and a
    manifest that omits a real export would move its removal out of the gate.
    Only an explicitly named ``--config`` is trusted to apply the overlay --
    the trust the retired ``--post-manifest`` flag had by construction. The
    oracle is the run with no overlay at all: a discovered one must not change
    its exit code, while the same document named explicitly does."""
    old_p, new_p = tmp_path / "old.json", tmp_path / "new.json"
    old_p.write_text(
        snapshot_to_json(_snap([_fn("public_api"), _fn("pp_x")])), encoding="utf-8"
    )
    new_p.write_text(snapshot_to_json(_snap([_fn("pp_x")])), encoding="utf-8")
    (tmp_path / "m.json").write_text(
        json.dumps({"post_abi": 1, "exports": exports}), encoding="utf-8"
    )
    cfg = _config(tmp_path, _KEY)
    plain = CliRunner().invoke(main, ["compare", str(old_p), str(new_p)])
    assert plain.exit_code == 4, plain.output
    monkeypatch.chdir(tmp_path)  # cfg is now the auto-discovered config
    discovered = CliRunner().invoke(main, ["compare", str(old_p), str(new_p)])
    assert discovered.exit_code == plain.exit_code, discovered.output
    assert "contract.overlays.post_manifest is not applied" in discovered.stderr
    explicit = CliRunner().invoke(
        main, ["compare", str(old_p), str(new_p), "--config", str(cfg)]
    )
    assert explicit.exit_code != plain.exit_code, explicit.output  # the overlay is real


def test_the_action_strips_the_overlay_from_a_discovered_config(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from abicheck.action_config_overlay import strip_untrusted_execution_keys

    base = {
        "contract": {"overlays": {"post_manifest": "m.json"}},
        "scope": {"public": True},
    }
    stripped = strip_untrusted_execution_keys(base)
    assert stripped["contract"]["overlays"] == {}
    assert stripped["scope"] == {"public": True}
    assert base["contract"]["overlays"] == {"post_manifest": "m.json"}  # not mutated
    assert "contract.overlays.post_manifest was dropped" in capsys.readouterr().err
    untouched = {"scope": {"public": False}}
    assert strip_untrusted_execution_keys(untouched) == untouched

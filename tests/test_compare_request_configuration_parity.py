# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A single-pair compare's configuration, through the CLI and the typed API.

``tests/test_release_request_parity.py`` proves the *release* request resolves
identically through ``compare`` and ``abicheck.service``. This is its
single-pair sibling for the configuration a run is scored under: ``--pack``
manifests (a policy pack, and a gate pack that moves a severity level, so the
exit code changes rather than only a receipt), ``.abicheck.yml``
``policy.overrides``, and ``contract.overlays.post_manifest``.

Every case is behavioural first -- each configuration must *change* the
outcome versus the unconfigured run (checked by :func:`_baseline`), else an
agreement could hold between two front ends that both ignore it -- and the
oracle is the real CLI run, never a re-resolution the test performs itself.

The typed API states project configuration as already-resolved request fields
(``project_policy_overrides``, ``public_surface_allowlist``) since a
``CompareRequest`` names no ``.abicheck.yml``; those cases derive the fields
through the same public helpers the CLI uses, so what is compared is the
scoring, not two parsers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck import service
from abicheck.cli import main
from abicheck.errors import ValidationError
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json
from abicheck.workflows.contracts import CompareRequest
from abicheck.workflows.request_inputs import InputSpec


def _fn(name: str, mangled: str | None = None) -> Function:
    return Function(
        name=name,
        mangled=mangled or name,
        return_type="int",
        visibility=Visibility.PUBLIC,
    )


def _write(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


@pytest.fixture
def removal_pair(tmp_path: Path) -> tuple[Path, Path]:
    """One removed exported function: the plainest break, so every case is
    about what the configuration does to it."""
    common = {"library": "libfoo.so.1", "from_headers": True}
    old = AbiSnapshot(
        version="1.0",
        functions=[_fn("api_a", "_Z5api_av"), _fn("api_b", "_Z5api_bv")],
        **common,
    )
    new = AbiSnapshot(version="2.0", functions=[_fn("api_a", "_Z5api_av")], **common)
    return _write(tmp_path / "old.json", old), _write(tmp_path / "new.json", new)


@pytest.fixture
def post_pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A POST pair whose only change is private ``__pp_*`` kernel churn, plus
    the manifest committing ``pp_foo`` alone."""
    old = AbiSnapshot(
        library="libpost.so",
        version="1",
        functions=[_fn("pp_foo"), _fn("__pp_foo_impl")],
    )
    new = AbiSnapshot(library="libpost.so", version="1", functions=[_fn("pp_foo")])
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "post_abi": 1,
                "exports": [
                    {
                        "name": "foo",
                        "c_symbol": "pp_foo",
                        "params": ["Float64"],
                        "return_dtype": "Float64",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return (
        _write(tmp_path / "pold.json", old),
        _write(tmp_path / "pnew.json", new),
        manifest,
    )


def _file(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _cli(
    old: Path, new: Path, *extra: str
) -> tuple[int, str, dict[str, object] | None]:
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", "json=-", *extra]
    )
    out = result.output
    doc = json.loads(out[out.find("{") :]) if "{" in out else None
    return result.exit_code, out, doc


def _api(old: Path, new: Path, **fields: object):
    return service.run_compare_request(
        CompareRequest(old=InputSpec(path=old), new=InputSpec(path=new), **fields)
    )


def _outcome_cli(old: Path, new: Path, *extra: str) -> tuple[int, str]:
    code, out, doc = _cli(old, new, *extra)
    assert doc is not None, out
    return code, str(doc["verdict"])


def _outcome_api(old: Path, new: Path, **fields: object) -> tuple[int, str]:
    result = _api(old, new, **fields)
    return result.exit_decision.code, result.diff.verdict.name


def _baseline(old: Path, new: Path) -> tuple[int, str]:
    cli = _outcome_cli(old, new)
    assert cli == _outcome_api(old, new), "the unconfigured run must already agree"
    return cli


POLICY_PACK = (
    "id: relax\nversion: 1\nkind: policy\nassignments:\n  func_removed: ignore\n"
)
GATE_PACK = "id: lenient\nversion: 1\nkind: gate\nassignments:\n  gate.severity.abi_breaking: warning\n"


class TestPacksScoreTheSameOnBothSurfaces:
    @pytest.mark.parametrize(
        "packs",
        [
            pytest.param((POLICY_PACK,), id="policy-pack"),
            pytest.param((GATE_PACK,), id="gate-pack-moves-severity"),
            pytest.param((POLICY_PACK, GATE_PACK), id="policy-and-gate-packs"),
        ],
    )
    def test_a_pack_changes_both_outcomes_identically(
        self, removal_pair: tuple[Path, Path], tmp_path: Path, packs: tuple[str, ...]
    ) -> None:
        old, new = removal_pair
        baseline = _baseline(old, new)
        paths = [_file(tmp_path, f"pack{i}.yml", body) for i, body in enumerate(packs)]
        cli = _outcome_cli(old, new, *[a for p in paths for a in ("--pack", str(p))])
        assert cli != baseline, "the pack must change the CLI outcome"
        assert _outcome_api(old, new, pack_paths=tuple(paths)) == cli

    def test_a_gate_pack_moves_the_api_severity_config(
        self, removal_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """The severity level itself, not only the exit code it produces."""
        old, new = removal_pair
        pack = _file(tmp_path, "gate.yml", GATE_PACK)
        _, out, doc = _cli(old, new, "--pack", str(pack))
        assert doc is not None, out
        cli_level = doc["severity"]["config"]["abi_breaking"]  # type: ignore[index]
        api = _api(old, new, pack_paths=(pack,))
        assert api.severity_config is not None
        assert api.severity_config.abi_breaking.value == cli_level == "warning"

    def test_an_explicitly_stated_preset_outranks_a_gate_pack_on_both(
        self, removal_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """D8: a stated value beats a pack, so the pack must *not* move it."""
        old, new = removal_pair
        pack = _file(tmp_path, "gate.yml", GATE_PACK)
        cli = _outcome_cli(old, new, "--pack", str(pack), "--severity-preset", "strict")
        api = _outcome_api(old, new, pack_paths=(pack,), severity_preset="strict")
        assert cli == api
        assert cli[0] == 4

    def test_conflicting_packs_are_a_usage_error_on_both(
        self, removal_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        old, new = removal_pair
        first = _file(tmp_path, "a.yml", POLICY_PACK.replace("relax", "a"))
        second = _file(
            tmp_path,
            "b.yml",
            POLICY_PACK.replace("relax", "b").replace("ignore", "warn"),
        )
        code, out, _ = _cli(old, new, "--pack", str(first), "--pack", str(second))
        assert code == 64, out
        with pytest.raises(ValidationError) as excinfo:
            _api(old, new, pack_paths=(first, second))
        assert str(excinfo.value) in out


class TestProjectConfigScoresTheSameOnBothSurfaces:
    def test_policy_overrides(
        self, removal_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        from abicheck.workflows.compare_configuration import project_policy_overrides

        old, new = removal_pair
        baseline = _baseline(old, new)
        cfg = _file(
            tmp_path,
            "proj.abicheck.yml",
            "policy:\n  overrides:\n    func_removed: ignore\n",
        )
        cli = _outcome_cli(old, new, "--config", str(cfg))
        assert cli != baseline
        from abicheck.buildsource.build_config import load_build_config

        overrides = project_policy_overrides(load_build_config(cfg), cfg)
        api = _outcome_api(old, new, project_policy_overrides=tuple(overrides.items()))
        assert api == cli

    def test_a_pack_outranks_project_policy_overrides_on_both(
        self, removal_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """Project config is the weakest tier: a pack stating the same kind wins."""
        from abicheck.workflows.compare_configuration import project_policy_overrides

        old, new = removal_pair
        cfg = _file(
            tmp_path,
            "proj.abicheck.yml",
            "policy:\n  overrides:\n    func_removed: ignore\n",
        )
        pack = _file(
            tmp_path,
            "p.yml",
            "id: keep\nversion: 1\nkind: policy\nassignments:\n  func_removed: break\n",
        )
        cli = _outcome_cli(old, new, "--config", str(cfg), "--pack", str(pack))
        from abicheck.buildsource.build_config import load_build_config

        overrides = project_policy_overrides(load_build_config(cfg), cfg)
        api = _outcome_api(
            old,
            new,
            pack_paths=(pack,),
            project_policy_overrides=tuple(overrides.items()),
        )
        assert api == cli
        assert cli[0] == 4

    def test_malformed_policy_overrides_are_rejected_on_both(
        self, removal_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        from abicheck.workflows.compare_configuration import (
            CompareConfigurationError,
            project_policy_overrides,
        )

        old, new = removal_pair
        cfg = _file(
            tmp_path,
            "bad.abicheck.yml",
            "policy:\n  overrides:\n    not_a_kind: ignore\n",
        )
        code, out, _ = _cli(old, new, "--config", str(cfg))
        assert code != 0 and "not_a_kind" in out
        from abicheck.buildsource.build_config import load_build_config

        with pytest.raises(CompareConfigurationError, match="not_a_kind"):
            project_policy_overrides(load_build_config(cfg), cfg)

    def test_post_manifest_scope(
        self, post_pair: tuple[Path, Path, Path], tmp_path: Path
    ) -> None:
        from abicheck.post_manifest import contract_scope_allowlist, load_manifest
        from abicheck.serialization import load_snapshot

        old, new, manifest = post_pair
        baseline = _baseline(old, new)
        cfg = _file(
            tmp_path,
            "post.abicheck.yml",
            f"contract:\n  overlays:\n    post_manifest: {str(manifest)!r}\n",
        )
        cli = _outcome_cli(old, new, "--config", str(cfg))
        assert cli != baseline
        allowlist = contract_scope_allowlist(
            load_manifest(manifest), load_snapshot(old), load_snapshot(new)
        )
        api = _outcome_api(old, new, public_surface_allowlist=frozenset(allowlist))
        assert api == cli


class TestStatedSettings:
    """``CompareRequest.stated_settings`` is the typed spelling of the CLI's
    Click ``ParameterSource`` answer: the same "was ``policy`` stated?" fact
    must yield the same receipt provenance on both surfaces."""

    @pytest.mark.parametrize(
        ("cli_extra", "stated", "expected_layer"),
        [
            pytest.param((), frozenset(), "built_in_default", id="policy-defaulted"),
            pytest.param(
                ("--policy", "strict_abi"),
                frozenset({"policy"}),
                None,
                id="policy-typed",
            ),
        ],
    )
    def test_policy_base_provenance_matches_the_cli(
        self,
        removal_pair: tuple[Path, Path],
        tmp_path: Path,
        cli_extra: tuple[str, ...],
        stated: frozenset[str],
        expected_layer: str | None,
    ) -> None:
        old, new = removal_pair
        pack = _file(tmp_path, "relax.yml", POLICY_PACK)
        _, out, doc = _cli(
            old, new, "--pack", str(pack), "--contract", "auto", *cli_extra
        )
        assert doc is not None, out
        provenance = doc["contract_context"]["evaluation_context"]["field_provenance"]  # type: ignore[index]
        cli_layer = provenance["policy.base"]["layer"]
        api = _api(old, new, pack_paths=(pack,), stated_settings=stated)
        api_layer = api.diff.evaluation_config.provenance["policy.base"].layer.value
        if expected_layer is not None:
            assert cli_layer == api_layer == expected_layer
        else:
            # Typed on the CLI, stated on the API: each its own explicit tier.
            assert "built_in_default" not in (cli_layer, api_layer)

    def test_none_keeps_reading_every_field_as_stated(
        self, removal_pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        old, new = removal_pair
        pack = _file(tmp_path, "relax.yml", POLICY_PACK)
        api = _api(old, new, pack_paths=(pack,))
        layer = api.diff.evaluation_config.provenance["policy.base"].layer.value
        assert layer != "built_in_default"

# Copyright 2026 Nikolay Petrov
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

"""P0.2: Bazel root-target scoping.

A caller (``dump --build-target``, ``.abicheck.yml``'s ``build.targets``, or
the typed ``InputSpec.build_targets``) can declare which specific Bazel
target(s) are the library under test, instead of always collecting a
workspace-wide ``deps(//...)`` query -- the motivating report is a Bazel
validation lab whose workspace-wide query captured unrelated fixture/test
targets alongside the real library.

Covers, bottom-up:

* :func:`abicheck.buildsource.adapters.bazel.bazel_deps_expression` -- the
  pure query-language expression builder shared by the live-query adapter
  path and the zero-config inferred-query path.
* :class:`~abicheck.buildsource.adapters.bazel.BazelAdapter` -- multi-target
  support and ``BuildEvidence.target_scope`` accounting, against pre-captured
  jsonproto fixtures (no real ``bazel`` binary required).
* :mod:`abicheck.buildsource.build_query` -- the zero-config inferred-query
  command construction and the end-to-end ``run_inferred_build_query`` wiring
  (subprocess mocked).
* ``.abicheck.yml``'s ``build.targets`` -- :class:`BuildConfig` parsing,
  serialization round-trip, and strict-schema type validation.
* The typed API -- :class:`InputSpec.build_targets` reaching
  ``embed_build_source`` through ``service_input_resolution.
  embed_side_build_source``.
* The CLI -- ``dump --build-target`` reaching ``embed_build_source``.
* The report block -- ``_build_coverage``'s L3_build row surfaces
  ``requested_roots``/``resolved_roots``/``transitive_targets``/
  ``compile_units``/``link_units``, unpopulated for an unscoped run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from abicheck.buildsource.adapters.bazel import BazelAdapter, bazel_deps_expression
from abicheck.buildsource.build_evidence import BuildEvidence, TargetScope
from abicheck.buildsource.build_query import (
    inferred_bazel_cquery_command,
    inferred_query_command,
    run_inferred_build_query,
)
from abicheck.buildsource.model import LayerCoverage

# A configured-target graph scoped to //foo, plus an unrelated //fixture
# target that a workspace-wide query would also capture.
_CQUERY = json.dumps(
    {
        "results": [
            {
                "target": {
                    "rule": {
                        "name": "//foo:foo",
                        "ruleClass": "cc_library",
                        "attribute": [
                            {
                                "name": "srcs",
                                "type": "LABEL_LIST",
                                "stringListValue": ["//foo:foo.cc"],
                            }
                        ],
                    }
                }
            },
            {
                "target": {
                    "rule": {
                        "name": "//fixture:fixture",
                        "ruleClass": "cc_library",
                    }
                }
            },
        ]
    }
)


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ── bazel_deps_expression (pure) ──────────────────────────────────────────


def test_deps_expression_empty_is_workspace_wide():
    assert bazel_deps_expression(()) == "//..."


def test_deps_expression_single_target_is_bare():
    assert bazel_deps_expression(("//:math",)) == "//:math"


def test_deps_expression_multiple_targets_use_set():
    assert bazel_deps_expression(("//:math", "//:util")) == "set(//:math //:util)"


# ── BazelAdapter target_scope accounting ──────────────────────────────────


def test_adapter_no_targets_leaves_target_scope_none():
    ev = BazelAdapter(cquery=_CQUERY).collect()
    assert ev.target_scope is None


def test_adapter_single_target_backcompat_kwarg_populates_scope():
    # The pre-existing singular `target=` kwarg (unused in production but
    # part of the adapter's public constructor) must still work and must
    # feed the same TargetScope accounting as the plural `targets=`.
    ev = BazelAdapter(cquery=_CQUERY, target="//foo:foo").collect()
    assert ev.target_scope == TargetScope(
        requested=["//foo:foo"], resolved=["//foo:foo"], transitive_count=2
    )


def test_adapter_multiple_targets_resolved_and_transitive_count():
    ev = BazelAdapter(cquery=_CQUERY, targets=["//foo:foo", "//bar:bar"]).collect()
    assert ev.target_scope is not None
    assert ev.target_scope.requested == ["//foo:foo", "//bar:bar"]
    # //bar:bar was requested but never appears in the cquery results --
    # a typo'd/nonexistent label must not silently read as resolved.
    assert ev.target_scope.resolved == ["//foo:foo"]
    assert ev.target_scope.transitive_count == 2


def test_adapter_targets_without_cquery_evidence_leaves_transitive_count_none():
    # aquery-only replay (no cquery text at all): "never checked" must stay
    # distinct from "checked, found nothing".
    aquery = json.dumps({"actions": []})
    ev = BazelAdapter(aquery=aquery, targets=["//foo:foo"]).collect()
    assert ev.target_scope is not None
    assert ev.target_scope.requested == ["//foo:foo"]
    assert ev.target_scope.resolved == []
    assert ev.target_scope.transitive_count is None


def test_target_scope_round_trips_through_build_evidence_json():
    ev = BuildEvidence(
        target_scope=TargetScope(
            requested=["//:math"], resolved=["//:math"], transitive_count=5
        )
    )
    restored = BuildEvidence.from_dict(json.loads(json.dumps(ev.to_dict())))
    assert restored.target_scope == ev.target_scope


def test_target_scope_omitted_from_dict_when_unset():
    # Additive-field convention (mirrors `comdat`): absent, not `null`, when
    # no scoping was requested -- an older reader must be able to tell
    # "field doesn't exist" from "field exists and is empty".
    assert "target_scope" not in BuildEvidence().to_dict()


def test_build_evidence_merge_prefers_the_scoped_side():
    a = BuildEvidence()
    b = BuildEvidence(target_scope=TargetScope(requested=["//:math"]))
    a.merge(b)
    assert a.target_scope == b.target_scope


# ── zero-config inferred-query command construction ───────────────────────


def test_inferred_aquery_unscoped_by_default(tmp_path: Path):
    cmd = inferred_query_command("bazel", tmp_path)
    assert cmd is not None
    assert "deps(//...)" in cmd[-1]


def test_inferred_aquery_scoped_to_declared_targets(tmp_path: Path):
    cmd = inferred_query_command("bazel", tmp_path, bazel_targets=("//:math",))
    assert cmd is not None
    assert "deps(//:math)" in cmd[-1]
    assert "//..." not in cmd[-1]


def test_inferred_aquery_scoped_to_multiple_targets_unions(tmp_path: Path):
    cmd = inferred_query_command(
        "bazel", tmp_path, bazel_targets=("//:math", "//:util")
    )
    assert cmd is not None
    assert "deps(set(//:math //:util))" in cmd[-1]


def test_inferred_cquery_unscoped_by_default():
    cmd = inferred_bazel_cquery_command()
    assert cmd[-1] == "deps(//...)"


def test_inferred_cquery_scoped_to_declared_targets():
    cmd = inferred_bazel_cquery_command(("//:math",))
    assert cmd[-1] == "deps(//:math)"


# ── end-to-end: run_inferred_build_query threads bazel_targets ────────────


def test_run_inferred_build_query_scopes_both_aquery_and_cquery(
    tmp_path: Path, monkeypatch
):
    (tmp_path / "MODULE.bazel").write_text("module(name='x')\n", encoding="utf-8")
    seen_cmds: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen_cmds.append(cmd)
        if "aquery" in cmd:
            return _FakeProc(0, stdout=json.dumps({"actions": []}))
        return _FakeProc(0, stdout=_CQUERY)

    from abicheck.buildsource import build_query as _bq

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    merged, ext = BuildEvidence(), []
    out = run_inferred_build_query(
        tmp_path,
        merged,
        ext,
        which=lambda tool: f"/usr/bin/{tool}",
        bazel_targets=("//foo:foo",),
    )
    assert out is None
    aquery_cmd = next(c for c in seen_cmds if "aquery" in c)
    cquery_cmd = next(c for c in seen_cmds if "cquery" in c)
    assert "deps(//foo:foo)" in aquery_cmd[-1]
    assert cquery_cmd[-1] == "deps(//foo:foo)"
    # The scoped run's requested/resolved accounting reaches the merged
    # evidence, not just the query text.
    assert merged.target_scope is not None
    assert merged.target_scope.requested == ["//foo:foo"]
    assert merged.target_scope.resolved == ["//foo:foo"]


def test_run_inferred_build_query_unscoped_leaves_target_scope_none(
    tmp_path: Path, monkeypatch
):
    (tmp_path / "MODULE.bazel").write_text("module(name='x')\n", encoding="utf-8")

    def fake_run(cmd, **kw):
        if "aquery" in cmd:
            return _FakeProc(0, stdout=json.dumps({"actions": []}))
        return _FakeProc(0, stdout=_CQUERY)

    from abicheck.buildsource import build_query as _bq

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    merged, ext = BuildEvidence(), []
    run_inferred_build_query(
        tmp_path, merged, ext, which=lambda tool: f"/usr/bin/{tool}"
    )
    assert merged.target_scope is None


# ── .abicheck.yml `build.targets` ─────────────────────────────────────────


def test_build_config_parses_build_targets():
    from abicheck.buildsource.inline import BuildConfig

    cfg = BuildConfig.from_dict({"build": {"system": "bazel", "targets": ["//:math"]}})
    assert cfg.system == "bazel"
    assert cfg.targets == ["//:math"]


def test_build_config_targets_round_trips_through_to_dict():
    from abicheck.buildsource.inline import BuildConfig

    cfg = BuildConfig(targets=["//:math", "//:util"])
    restored = BuildConfig.from_dict(cfg.to_dict())
    assert restored.targets == ["//:math", "//:util"]


def test_build_config_omits_targets_when_default():
    from abicheck.buildsource.inline import BuildConfig

    assert "targets" not in BuildConfig().to_dict().get("build", {})


def test_build_config_rejects_non_string_target():
    from abicheck.buildsource.inline import BuildConfig

    with pytest.raises(ValueError, match="build.targets"):
        BuildConfig.from_dict({"build": {"targets": [123]}})


def test_build_config_accepts_single_bare_string_target():
    # `_strs()` folds a bare scalar to a 1-element list, same as
    # `public_headers`/`exclude` already do.
    from abicheck.buildsource.inline import BuildConfig

    cfg = BuildConfig.from_dict({"build": {"targets": "//:math"}})
    assert cfg.targets == ["//:math"]


# ── typed API: InputSpec.build_targets ─────────────────────────────────────


def test_input_spec_of_coerces_build_targets():
    from abicheck.api_types import InputSpec

    spec = InputSpec.of(Path("lib.so"), build_targets=["//:math", "//:util"])
    assert spec.build_targets == ("//:math", "//:util")


def test_input_spec_default_build_targets_is_empty():
    from abicheck.api_types import InputSpec

    assert InputSpec(path=Path("lib.so")).build_targets == ()


def test_compare_request_carries_build_targets_per_side():
    # CompareRequest.old/new are InputSpec verbatim (ADR-055 D1), so a field
    # added there needs no CompareRequest-specific plumbing -- pinned here so
    # a future InputSpec refactor can't silently drop compare's own access.
    from abicheck.api_types import CompareRequest, InputSpec

    req = CompareRequest(
        old=InputSpec(path=Path("old.so"), build_targets=("//:old",)),
        new=InputSpec(path=Path("new.so"), build_targets=("//:new",)),
    )
    assert req.old.build_targets == ("//:old",)
    assert req.new.build_targets == ("//:new",)


def test_dump_request_input_carries_build_targets():
    from abicheck.api_types import DumpRequest, InputSpec

    req = DumpRequest(input=InputSpec(path=Path("lib.so"), build_targets=("//:math",)))
    assert req.input.build_targets == ("//:math",)


def test_embed_side_build_source_forwards_build_targets(monkeypatch, tmp_path: Path):
    from abicheck import cli_buildsource
    from abicheck.api_types import InputSpec
    from abicheck.model import AbiSnapshot
    from abicheck.service_compare_evidence import SideEvidence
    from abicheck.service_input_resolution import embed_side_build_source

    captured: dict = {}

    def _fake_embed(snap, *, build_info, sources, build_targets=(), **kwargs):
        captured["build_targets"] = build_targets

    monkeypatch.setattr(cli_buildsource, "embed_build_source", _fake_embed)

    src = tmp_path / "src"
    src.mkdir()
    side = InputSpec(path=tmp_path / "lib.so", sources=src, build_targets=("//:math",))
    evidence = SideEvidence(
        headers=[], compile=None, collect_mode="source-target", dump_manifest=None
    )
    embed_side_build_source(
        AbiSnapshot(library="lib", version="1.0"),
        side,
        evidence,
        header_backend="auto",
        public_headers=[],
        public_header_dirs=[],
    )
    assert captured["build_targets"] == ("//:math",)


# ── CLI: `dump --build-target` ─────────────────────────────────────────────


def test_dump_cli_build_target_flag_reaches_embed_build_source(
    monkeypatch, tmp_path: Path
):
    from click.testing import CliRunner

    from abicheck import cli_buildsource
    from abicheck.cli import main

    captured: dict = {}

    def _fake_embed(snap, build_info, sources, *, build_targets=(), **kwargs):
        captured["build_targets"] = build_targets

    monkeypatch.setattr(cli_buildsource, "embed_build_source", _fake_embed)

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.cpp").write_text("int f() { return 0; }\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "dump",
            "--sources",
            str(src),
            "--build-target",
            "//:math",
            "--build-target",
            "//:util",
            "-o",
            str(tmp_path / "out.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["build_targets"] == ("//:math", "//:util")


def test_dump_cli_build_target_overrides_config(monkeypatch, tmp_path: Path):
    from click.testing import CliRunner

    from abicheck import cli_buildsource
    from abicheck.cli import main

    captured: dict = {}

    def _fake_embed(snap, build_info, sources, *, build_targets=(), **kwargs):
        captured["build_targets"] = build_targets

    monkeypatch.setattr(cli_buildsource, "embed_build_source", _fake_embed)

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.cpp").write_text("int f() { return 0; }\n", encoding="utf-8")
    (src / ".abicheck.yml").write_text(
        "build:\n  system: bazel\n  targets:\n    - //:from_config\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "dump",
            "--sources",
            str(src),
            "--build-target",
            "//:from_cli",
            "-o",
            str(tmp_path / "out.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["build_targets"] == ("//:from_cli",)


def test_dot_abicheck_yml_build_targets_flow_into_dump_with_no_cli_flag(
    monkeypatch, tmp_path: Path
):
    """`.abicheck.yml`'s `build.targets` alone (no `--build-target`) reaches
    the inferred Bazel query, end to end through `dump --sources` -- the
    scenario the real-world report asked for (a project committing
    `build.targets` once instead of every caller re-typing `--build-target`).
    The subprocess is mocked; `embed_build_source` itself is NOT, so this
    exercises the real config-discovery -> BuildConfig -> bazel_targets fold.
    """
    from click.testing import CliRunner

    from abicheck.buildsource import build_query as _bq
    from abicheck.cli import main

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.cpp").write_text("int f() { return 0; }\n", encoding="utf-8")
    (src / "MODULE.bazel").write_text("module(name='x')\n", encoding="utf-8")
    (src / ".abicheck.yml").write_text(
        "build:\n  system: bazel\n  targets:\n    - //:from_config\n",
        encoding="utf-8",
    )

    seen_cmds: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen_cmds.append(cmd)
        if "aquery" in cmd:
            return _FakeProc(0, stdout=json.dumps({"actions": []}))
        return _FakeProc(0, stdout="{}")

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    # `run_inferred_build_query`'s `which=shutil.which` default is bound at
    # module-import time, so monkeypatching `shutil.which` after the fact
    # cannot intercept it -- a fake `bazel` executable on PATH is the only
    # way to make the real `shutil.which("bazel")` gate pass here (the
    # subprocess call itself is mocked above, so it's never actually run).
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_bazel = bin_dir / "bazel"
    fake_bazel.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_bazel.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["dump", "--sources", str(src), "-o", str(tmp_path / "out.json")],
    )
    assert result.exit_code == 0, result.output
    assert seen_cmds, "the inferred bazel query never ran"
    aquery_cmd = next(c for c in seen_cmds if "aquery" in c)
    assert "deps(//:from_config)" in aquery_cmd[-1]
    assert "deps(//...)" not in aquery_cmd[-1]


# ── report block: L3_build coverage row ────────────────────────────────────


def test_layer_coverage_unscoped_report_fields_are_empty():
    from abicheck.cli_buildsource_helpers import _build_coverage

    merged = BuildEvidence()
    rows = _build_coverage(merged, has_build=True)
    l3 = next(r for r in rows if r.layer == "L3_build")
    assert l3.requested_roots == ()
    assert l3.resolved_roots == ()
    assert l3.transitive_targets is None
    assert l3.compile_units is None
    assert l3.link_units is None


def test_layer_coverage_scoped_report_fields_are_populated():
    from abicheck.cli_buildsource_helpers import _build_coverage

    merged = BuildEvidence(
        target_scope=TargetScope(
            requested=["//:math"], resolved=["//:math"], transitive_count=5
        )
    )
    rows = _build_coverage(merged, has_build=True)
    l3 = next(r for r in rows if r.layer == "L3_build")
    assert l3.requested_roots == ("//:math",)
    assert l3.resolved_roots == ("//:math",)
    assert l3.transitive_targets == 5
    assert l3.compile_units == 0
    assert l3.link_units == 0
    assert "scoped to 1 root target(s) (1 resolved)" in l3.detail


def test_layer_coverage_to_dict_carries_new_fields():
    row = LayerCoverage(
        layer="L3_build",
        requested_roots=("//:math",),
        resolved_roots=("//:math",),
        transitive_targets=3,
        compile_units=2,
        link_units=1,
    )
    d = row.to_dict()
    assert d["requested_roots"] == ["//:math"]
    assert d["resolved_roots"] == ["//:math"]
    assert d["transitive_targets"] == 3
    assert d["compile_units"] == 2
    assert d["link_units"] == 1
    restored = LayerCoverage.from_dict(d)
    assert restored == row

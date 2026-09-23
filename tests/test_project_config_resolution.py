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

"""One project-config selection rule for every command.

Bug class ``config.command_specific_discovery``: ``compare`` walked up from
the current directory for ``.abicheck.yml`` while ``dump`` looked only at the
``--sources`` tree root, so the same project config applied to one command and
not the other -- ``dump`` run from a project checkout failed without
``--config`` and succeeded with it. The rule now lives once, in
:func:`abicheck.config_paths.resolve_project_config`; these tests state it
over every combination of inputs, plus the trust half (a discovered config
never runs ``build.query``) and the end-to-end ``dump`` behaviour.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.config_paths import ProjectConfigRef, resolve_project_config

_CFG = ".abicheck.yml"


def _write_cfg(directory: Path, body: str = "version: 1\n") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _CFG
    path.write_text(body, encoding="utf-8")
    return path


# ── the precedence rule, exhaustively ──────────────────────────────────────

_EXPLICIT = ("none", "given")
_SOURCES = ("none", "with_cfg", "without_cfg")
_SEARCH = ("none", "inside_project", "outside_project")


def _expected(
    explicit: str, sources: str, search: str, paths: dict
) -> ProjectConfigRef:
    """Independent statement of the documented order: explicit > sources
    root > nearest config above the search root > nothing."""
    if explicit == "given":
        return ProjectConfigRef(paths["explicit"], True)
    if sources == "with_cfg":
        return ProjectConfigRef(paths["sources_cfg"], False)
    if search == "inside_project":
        return ProjectConfigRef(paths["project_cfg"], False)
    return ProjectConfigRef(None, False)


@pytest.mark.parametrize(
    ("explicit", "sources", "search"),
    list(itertools.product(_EXPLICIT, _SOURCES, _SEARCH)),
)
def test_precedence_over_every_input_combination(
    tmp_path: Path, explicit: str, sources: str, search: str
) -> None:
    project = tmp_path / "project"
    paths = {
        "explicit": _write_cfg(tmp_path / "elsewhere"),
        "project_cfg": _write_cfg(project),
        "sources_cfg": _write_cfg(tmp_path / "src_with"),
    }
    (tmp_path / "src_without").mkdir()
    (tmp_path / "outside").mkdir()
    nested = project / "build" / "deep"
    nested.mkdir(parents=True)

    got = resolve_project_config(
        paths["explicit"] if explicit == "given" else None,
        sources={
            "none": None,
            "with_cfg": tmp_path / "src_with",
            "without_cfg": tmp_path / "src_without",
        }[sources],
        search_from={
            "none": None,
            "inside_project": nested,
            "outside_project": tmp_path / "outside",
        }[search],
    )
    # "outside" walks up past tmp_path; a config found above it is the
    # host's, not part of this test's tree, so it reads as "none found".
    if got.path is not None and tmp_path not in got.path.parents:
        got = ProjectConfigRef(None, got.explicit)
    assert got == _expected(explicit, sources, search, paths)


def test_the_oracle_is_not_constant() -> None:
    """Vacuity guard: the expectation above distinguishes every outcome."""
    paths = {"explicit": Path("e"), "project_cfg": Path("p"), "sources_cfg": Path("s")}
    outcomes = {
        _expected(e, s, r, paths)
        for e, s, r in itertools.product(_EXPLICIT, _SOURCES, _SEARCH)
    }
    assert len(outcomes) == 4


def test_only_an_explicit_config_is_trusted(tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path)
    assert resolve_project_config(cfg).explicit is True
    assert resolve_project_config(None, sources=tmp_path).explicit is False
    assert resolve_project_config(None, search_from=tmp_path).explicit is False


def test_no_search_root_never_depends_on_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The typed API passes no search root, so a config above the process's
    working directory must not leak into it."""
    _write_cfg(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert resolve_project_config(None) == ProjectConfigRef(None, False)


# ── dump's config-sourced fields use the one selection ─────────────────────


def test_dump_reads_every_config_field_from_a_config_above_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import click

    from abicheck.frontends.cli.dump_debug_config import resolve_dump_project_config

    cfg = _write_cfg(
        tmp_path,
        "compile:\n  lang: c\n"
        "debug:\n  dwarf_only: true\n"
        "scope:\n  exclude_headers: [private.h]\n"
        "build:\n  compile_db_filter: 'src/*'\n",
    )
    work = tmp_path / "build"
    work.mkdir()
    monkeypatch.chdir(work)

    with click.Context(click.Command("dump")) as ctx:
        got = resolve_dump_project_config(
            ctx,
            build_config=None,
            sources=None,
            lang=None,
            lang_default="c++",
            apply_env_toggles=lambda _c, _p: None,
            exclude_headers=(),
            resolved_debug=None,
            debug_roots=(),
        )
    assert got.path == cfg
    assert got.explicit is False
    assert got.lang == "c"
    assert got.debug.dwarf_only is True
    assert got.exclude_headers == ("private.h",)
    assert got.compile_db_filter == "src/*"


# ── a discovered config is read, never trusted to execute ──────────────────


@pytest.mark.parametrize("explicit", [True, False])
def test_embed_trust_follows_explicitness_not_presence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit: bool
) -> None:
    from abicheck.buildsource import embed, inline
    from abicheck.model import AbiSnapshot

    cfg = _write_cfg(tmp_path, "build:\n  query: 'echo pwned'\n")
    src = tmp_path / "src"
    src.mkdir()
    seen: dict = {}

    def _capture(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(inline, "collect_inline_pack", _capture)
    with pytest.raises(RuntimeError, match="stop after capture"):
        embed.embed_build_source(
            AbiSnapshot(library="x", version="1"),
            None,
            src,
            build_config=cfg,
            build_config_explicit=explicit,
        )
    assert seen["build_config"].query == "echo pwned"  # passive read happens
    assert seen["build_config_trusted_for_query"] is explicit
    assert seen["compile_db_explicit"] is explicit


@pytest.mark.parametrize("explicit", [True, False])
def test_l2_seed_trust_follows_explicitness_not_presence(
    tmp_path: Path, explicit: bool
) -> None:
    from abicheck.buildsource.l2_seed_args import _resolve_l2_seed_pack_args

    cfg = _write_cfg(tmp_path, "build:\n  query: 'echo pwned'\n")
    args = _resolve_l2_seed_pack_args(
        cfg, tmp_path, None, None, None, build_config_explicit=explicit
    )
    assert args is not None
    assert args.build_config_trusted_for_query is explicit
    assert args.compile_db_explicit is explicit


# ── end to end: `dump` from a project checkout, no --config ────────────────

_HAVE_TOOLS = shutil.which("gcc") is not None and shutil.which("castxml") is not None


@pytest.mark.integration
@pytest.mark.skipif(not _HAVE_TOOLS, reason="gcc + castxml required")
@pytest.mark.parametrize("pass_config", [False, True])
def test_dump_from_a_checkout_honors_its_config_with_or_without_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pass_config: bool
) -> None:
    """The reported bug: `dump` failed without `--config` and succeeded with
    it. The config gates a declaration behind a macro, so the declaration is
    in the snapshot only if the config was actually applied."""
    import json

    from click.testing import CliRunner

    from abicheck.cli import main

    project = tmp_path / "proj"
    inc = project / "include"
    inc.mkdir(parents=True)
    # The gated declaration is a type, not an export: only a header parse
    # that saw FEATURE_ON can produce it (an exported function's name would
    # reach the snapshot from the ELF symbol table regardless).
    (inc / "api.h").write_text(
        "#ifdef FEATURE_ON\nstruct feature_on_t { int x; };\n"
        "int gated(struct feature_on_t *p);\n#endif\nint always(void);\n"
    )
    (project / "lib.c").write_text(
        "struct feature_on_t { int x; };\n"
        "int gated(struct feature_on_t *p){return p->x;}\n"
        "int always(void){return 2;}\n"
    )
    lib = project / "libapi.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(lib), str(project / "lib.c")],
        check=True,
        capture_output=True,
    )
    cfg = _write_cfg(project, "compile:\n  lang: c\n  defines: [FEATURE_ON]\n")
    work = project / "build"
    work.mkdir()
    monkeypatch.chdir(work)

    argv = ["dump", str(lib), "-H", str(inc / "api.h")]
    if pass_config:
        argv += ["--config", str(cfg)]
    result = CliRunner().invoke(main, argv)
    assert result.exit_code == 0, result.output
    snap = json.loads(result.stdout)
    assert "feature_on_t" in json.dumps(snap.get("types", snap))


@pytest.mark.parametrize("explicit", [False, True])
def test_a_discovered_config_never_executes_its_build_query(
    tmp_path: Path, explicit: bool
) -> None:
    """Executing, not asserting a flag: a hostile ``build.query`` in a config
    handed down as *discovered* must leave no side effect, while the same
    config named explicitly runs it (the control that proves the query is
    real and would have run)."""
    from abicheck.buildsource.embed import embed_build_source
    from abicheck.model import AbiSnapshot

    src = tmp_path / "src"
    src.mkdir()
    sentinel = tmp_path / "pwned"
    cfg = _write_cfg(tmp_path, f"build:\n  query: 'touch {sentinel}'\n")

    embed_build_source(
        AbiSnapshot(library="x", version="1"),
        None,
        src,
        build_config=cfg,
        build_config_explicit=explicit,
        collect_mode="build",
    )
    assert sentinel.exists() is explicit

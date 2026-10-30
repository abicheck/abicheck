"""``--set compile.*=…`` resolves the same compile context as the same key in a file.

ADR-091 D4 / the Action overlay replacement: the Action used to synthesize a
``compile:`` block into a temporary ``.abicheck.yml``. It now forwards
``--set compile.K=V`` instead. The differential below holds the two to the same
resolved :class:`~abicheck.dry_run_estimate.CompileContext`:

* the *file* side writes ``compile: {K: V}`` into a ``.abicheck.yml`` and resolves
  it the way the CLI does (an explicit ``--config``);
* the *set* side resolves no file and passes ``config_overrides={"compile.K": V}``.

The oracle for the file side is the overlay the deleted Action helper produced,
reconstructed in :func:`_legacy_overlay_block` from its own rules (``compiler =
gcc_path or gcc_prefix``, ``options`` shell-split on a single line and one token
per line on a multi-line value, ``frontend`` omitted when ``auto``, ``nostdinc``
only when true), so a regression in either direction fails here.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
import yaml

from abicheck.dry_run_estimate import CompileContext
from abicheck.workflows.compile_config import (
    COMPILE_CONTEXT_OVERRIDE_PATHS,
    CompileConfigError,
    merge_compile_config,
    resolve_compile_context,
)


def _legacy_overlay_block(
    *,
    frontend: str = "",
    gcc_path: str = "",
    gcc_prefix: str = "",
    gcc_options: str = "",
    sysroot: str = "",
    nostdinc: bool = False,
) -> dict[str, object]:
    """The ``compile:`` block the deleted Action helper synthesized (its oracle)."""
    block: dict[str, object] = {}
    if frontend and frontend != "auto":
        block["frontend"] = frontend
    compiler = gcc_path or gcc_prefix
    if compiler:
        block["compiler"] = compiler
    if gcc_options:
        block["options"] = (
            [line for line in gcc_options.splitlines() if line]
            if "\n" in gcc_options
            else shlex.split(gcc_options)
        )
    if sysroot:
        block["sysroot"] = sysroot
    if nostdinc:
        block["nostdinc"] = True
    return block


def _resolve_from_file(
    directory: Path, block: dict[str, object], cli: CompileContext | None = None
) -> CompileContext:
    cfg = directory / ".abicheck.yml"
    cfg.write_text(yaml.safe_dump({"compile": block}), encoding="utf-8")
    ctx, _includes = merge_compile_config(
        cli or CompileContext(), (), cfg, config_explicit=True
    )
    return ctx


def _resolve_from_set(
    overrides: dict[str, object], cli: CompileContext | None = None
) -> CompileContext:
    ctx, _includes = resolve_compile_context(
        sysroot=None,
        nostdinc=False,
        header_backend="auto",
        includes=(),
        build_config=None,
        config_overrides=overrides,
        compiler_path=(cli.gcc_path if cli else None),
    )
    return ctx


#: (CLI-level key, the ``--set`` path, the value a user writes, legacy overlay kwargs)
MATRIX: list[tuple[str, str, object, dict[str, object]]] = [
    ("compiler", "compile.compiler", "/usr/bin/g++", {"gcc_path": "/usr/bin/g++"}),
    (
        "compiler",
        "compile.compiler",
        "aarch64-linux-gnu-",
        {"gcc_prefix": "aarch64-linux-gnu-"},
    ),
    (
        "options",
        "compile.options",
        # The Action's shell-quoted input, split the way the overlay split it.
        shlex.split('-DNAME="x" -fno-exceptions'),
        {"gcc_options": '-DNAME="x" -fno-exceptions'},
    ),
    ("sysroot", "compile.sysroot", "/opt/sysroot", {"sysroot": "/opt/sysroot"}),
    ("nostdinc", "compile.nostdinc", True, {"nostdinc": True}),
    ("frontend", "compile.frontend", "clang", {"frontend": "clang"}),
    ("frontend", "compile.frontend", "castxml", {"frontend": "castxml"}),
    ("std", "compile.std", "c++17", {}),
]


@pytest.mark.parametrize(
    ("key", "path", "value", "legacy"),
    MATRIX,
    ids=[f"{path}={value!r}" for _key, path, value, _legacy in MATRIX],
)
def test_set_resolves_the_same_context_as_the_file_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    path: str,
    value: object,
    legacy: dict[str, object],
) -> None:
    monkeypatch.chdir(tmp_path)
    field = path.partition(".")[2]
    # The file's own key, written the way a user would write it.
    file_block = {field: value}
    from_file = _resolve_from_file(tmp_path, file_block)
    from_set = _resolve_from_set({path: value})
    assert from_set == from_file, key
    # And the file form is the one the old overlay produced, so --set reproduces
    # the Action's behaviour, not just the file's.
    if legacy:
        oracle_dir = tmp_path / "oracle"
        oracle_dir.mkdir()
        assert _resolve_from_file(oracle_dir, _legacy_overlay_block(**legacy)) == (
            from_set
        ), key


def test_options_with_quotes_and_spaces_resolve_identically_to_the_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single-line ``gcc-options`` shell-splits, quotes and all, as before."""
    monkeypatch.chdir(tmp_path)
    gcc_options = "-DMODE='fast' -O2"
    legacy = _legacy_overlay_block(gcc_options=gcc_options)
    assert legacy == {"options": ["-DMODE=fast", "-O2"]}
    from_file = _resolve_from_file(tmp_path, legacy)
    from_set = _resolve_from_set({"compile.options": legacy["options"]})
    assert from_set == from_file


def test_a_multi_line_options_value_is_one_token_per_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    gcc_options = "-DA\n-DB=2\n"
    legacy = _legacy_overlay_block(gcc_options=gcc_options)
    assert legacy == {"options": ["-DA", "-DB=2"]}
    assert _resolve_from_set({"compile.options": legacy["options"]}) == (
        _resolve_from_file(tmp_path, legacy)
    )


def test_a_full_matrix_with_a_user_file_block_matches_the_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user's own ``compile:`` block is the base; the overlay wins per key."""
    monkeypatch.chdir(tmp_path)
    user_block = {"std": "c++17", "defines": ["FROM_FILE"], "frontend": "castxml"}
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(yaml.safe_dump({"compile": user_block}), encoding="utf-8")
    # The overlay as the old Action merged it into a copy of the user's file.
    merged_block = {
        **user_block,
        **_legacy_overlay_block(frontend="clang", sysroot="/s"),
    }
    from_file = _resolve_from_file(tmp_path, merged_block)
    ctx_set, _ = merge_compile_config(
        CompileContext(),
        (),
        cfg,
        config_explicit=True,
        config_overrides={"compile.frontend": "clang", "compile.sysroot": "/s"},
    )
    assert ctx_set == from_file


def test_an_explicit_cli_flag_still_beats_a_set_value(tmp_path: Path) -> None:
    cli = CompileContext(frontend="clang")
    ctx, _ = merge_compile_config(
        cli,
        (),
        None,
        frontend_explicit=True,
        config_overrides={"compile.frontend": "castxml"},
    )
    assert ctx.frontend == "clang"


def test_a_set_value_beats_the_file_it_is_laid_over(tmp_path: Path) -> None:
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(
        yaml.safe_dump({"compile": {"frontend": "castxml"}}), encoding="utf-8"
    )
    ctx, _ = merge_compile_config(
        CompileContext(),
        (),
        cfg,
        config_explicit=True,
        config_overrides={"compile.frontend": "clang"},
    )
    assert ctx.frontend == "clang"


def test_a_discovered_compiler_is_not_trusted_but_a_set_compiler_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``compile.compiler`` from an auto-discovered file is ignored; ``--set`` is trusted."""
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(
        yaml.safe_dump({"compile": {"compiler": "/evil/cc"}}), encoding="utf-8"
    )
    ignored, _ = merge_compile_config(CompileContext(), (), cfg, config_explicit=False)
    assert ignored.gcc_path is None
    trusted, _ = merge_compile_config(
        CompileContext(),
        (),
        cfg,
        config_explicit=False,
        config_overrides={"compile.compiler": "/usr/bin/g++"},
    )
    assert trusted.gcc_path == "/usr/bin/g++"


def test_an_overrides_only_run_without_a_file_resolves_the_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    ctx, _ = merge_compile_config(
        CompileContext(), (), None, config_overrides={"compile.sysroot": "/s"}
    )
    assert str(ctx.sysroot) == "/s"


def test_a_malformed_explicit_file_still_fails_loud_with_overrides(
    tmp_path: Path,
) -> None:
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("compile: [not, a, mapping\n", encoding="utf-8")
    with pytest.raises(CompileConfigError):
        merge_compile_config(
            CompileContext(),
            (),
            cfg,
            config_explicit=True,
            config_overrides={"compile.frontend": "clang"},
        )


def test_the_applied_compile_keys_are_the_documented_config_tier() -> None:
    """Every key the Action's overlay carried is applied; core keys are not listed."""
    assert {
        "compile.compiler",
        "compile.frontend",
        "compile.options",
        "compile.sysroot",
        "compile.nostdinc",
        "compile.lang",
    } <= COMPILE_CONTEXT_OVERRIDE_PATHS
    assert "compile.include_dirs" not in COMPILE_CONTEXT_OVERRIDE_PATHS
    assert "compile.defines" not in COMPILE_CONTEXT_OVERRIDE_PATHS


def test_an_invalid_override_with_no_file_is_a_compile_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no file, a rejected override is a usage error, not a silent empty context."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(CompileConfigError, match="unknown config key"):
        merge_compile_config(
            CompileContext(), (), None, config_overrides={"compile.no_such_key": 1}
        )

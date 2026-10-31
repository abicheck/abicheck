"""``--set`` on ``dump`` and ``project capture-variants``, and the typed
``DumpRequest.config_overrides`` / ``run_dump(config_overrides=)`` (ADR-091 D4).

The CLI and the typed API resolve the same mapping through one validator and
one loader (``workflows/dump/project_config.py``). These tests check the
resolved config and the resolved request, not a mock of either path, and pin
the refusals: a key a command does not apply exits 64 naming it, never
silently dropped. ``project plan`` and the other project/deps/aggregate
commands take no ``--set`` at all (ADR-091 D4 / plan Phase 2 note).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.build_config_overrides import ConfigOverrideError
from abicheck.cli import main
from abicheck.service import run_dump
from abicheck.workflows.bundle_variants_capture import (
    VariantCaptureError,
    load_bundle_variants_config,
)
from abicheck.workflows.dump.project_config import (
    DUMP_APPLIED_PATHS,
    DumpConfigError,
    resolve_dump_project_config,
    run_dump_override_arguments,
    with_dump_config_overrides,
)
from abicheck.workflows.service_requests import DumpRequest, InputSpec


def _write_config(directory: Path, text: str) -> Path:
    path = directory / ".abicheck.yml"
    path.write_text(text, encoding="utf-8")
    return path


def _resolve(
    config: Path | None,
    *,
    overrides: dict[str, object] | None = None,
    exclude_headers: tuple[str, ...] = (),
):
    """The dump project config as the CLI resolves it (``dump_cmd`` calls this)."""
    return resolve_dump_project_config(
        lambda restore: restore(),  # no pipeline runs, so restore at once
        build_config=config,
        sources=None,
        lang=None,
        lang_default="c++",
        exclude_headers=exclude_headers,
        resolved_debug=None,
        debug_roots=(),
        config_overrides=overrides,
    )


def _dump(*args: str):
    return CliRunner().invoke(main, ["dump", *args], catch_exceptions=False)


def _operand(directory: Path) -> Path:
    path = directory / "libfoo.so"
    path.write_bytes(b"\x7fELF")
    return path


# ── dump: precedence and file-less behaviour ───────────────────────────────


def test_dump_set_beats_the_file_for_a_key_dump_applies(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path, "debug:\n  dwarf_only: true\ncompile:\n  lang: c\n"
    )
    assert _resolve(config).debug.dwarf_only is True  # file alone
    resolved = _resolve(
        config, overrides={"debug.dwarf_only": False, "compile.lang": "c++"}
    )
    assert resolved.debug.dwarf_only is False  # --set > file
    assert (resolved.lang, resolved.lang_explicit) == ("c++", True)


def test_dump_flag_beats_the_file_and_set_beats_the_file(tmp_path: Path) -> None:
    """``--exclude-header`` is dump's flag for ``scope.exclude_headers`` (a core
    key, so ``--set`` cannot set it): the flag still beats the file. ``--set``
    beats the file for a non-core key in the same run."""
    config = _write_config(
        tmp_path,
        "scope:\n  exclude_headers: [from_file.h]\ndebug:\n  dwarf_only: true\n",
    )
    resolved = _resolve(
        config,
        overrides={"debug.dwarf_only": False},
        exclude_headers=("from_flag.h",),
    )
    assert resolved.exclude_headers == ("from_flag.h",)  # flag > file
    assert resolved.debug.dwarf_only is False  # --set > file


def test_dump_set_builds_the_config_when_there_is_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no ambient .abicheck.yml above the test
    resolved = _resolve(None, overrides={"debug.format": "dwarf"})
    assert resolved.path is None
    assert resolved.debug.format == "dwarf"
    assert resolved.compile_db_filter is None  # the built-in default


def test_a_malformed_file_is_lenient_without_set_and_fatal_with_set(
    tmp_path: Path,
) -> None:
    config = _write_config(tmp_path, "debug: [not, a, mapping\n")
    assert _resolve(config).debug.dwarf_only is False  # existing leniency
    with pytest.raises(DumpConfigError, match="cannot read build config"):
        _resolve(config, overrides={"debug.dwarf_only": True})


# ── dump CLI: refusals exit 64 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "item",
    [
        "build.query=make",  # executable: never reached by dump's resolver
        "severity.addition=error",  # a gate key dump does not evaluate
        "sources.method=auto",  # compare-only CLI key
        "bundle_variants={x86: {target_triple: x86_64-linux-gnu, compiler_family: gcc}}",
    ],
)
def test_dump_refuses_a_key_it_does_not_apply(tmp_path: Path, item: str) -> None:
    operand = _operand(tmp_path)
    result = _dump(str(operand), "--dry-run", "--set", item)
    key = item.partition("=")[0]
    assert result.exit_code == 64, result.output
    assert f"dump does not apply {key} yet" in result.output


@pytest.mark.parametrize(
    ("item", "flag"),
    [
        ("scope.exclude_headers=[x.h]", "--exclude-header"),
        ("debug.pdb_path=/x.pdb", "--debug-info"),
    ],
)
def test_dump_refuses_a_core_key_and_names_its_flag(
    tmp_path: Path, item: str, flag: str
) -> None:
    result = _dump(str(_operand(tmp_path)), "--dry-run", "--set", item)
    assert result.exit_code == 64, result.output
    assert flag in result.output


def test_dump_set_malformed_value_is_a_usage_error(tmp_path: Path) -> None:
    result = _dump(str(_operand(tmp_path)), "--dry-run", "--set", "debug.dwarf_only=[")
    assert result.exit_code == 64, result.output
    assert "not valid YAML" in result.output


def test_dump_set_wrong_typed_value_is_a_usage_error(tmp_path: Path) -> None:
    result = _dump(
        str(_operand(tmp_path)), "--dry-run", "--set", "debug.dwarf_only=maybe"
    )
    assert result.exit_code == 64, result.output
    assert "debug.dwarf_only" in result.output


def test_dump_set_with_a_malformed_config_is_a_usage_error(tmp_path: Path) -> None:
    config = _write_config(tmp_path, "debug: [not, a, mapping\n")
    result = _dump(
        str(_operand(tmp_path)),
        "--config",
        str(config),
        "--dry-run",
        "--set",
        "debug.dwarf_only=true",
    )
    assert result.exit_code == 64, result.output
    assert "cannot read build config" in result.output


def test_dump_accepts_an_applied_set_on_the_cli(tmp_path: Path) -> None:
    config = _write_config(tmp_path, "debug:\n  dwarf_only: false\n")
    result = _dump(
        str(_operand(tmp_path)),
        "--config",
        str(config),
        "--dry-run",
        "--set",
        "debug.dwarf_only=true",
    )
    assert result.exit_code == 0, result.output


def test_dump_set_is_the_applied_set_the_cli_validates() -> None:
    """The CLI's refusals and the typed channel table name the same keys."""
    assert {
        "compile.lang",
        "debug.format",
        "build.compile_db_filter",
    } <= DUMP_APPLIED_PATHS
    assert "compile.std" in DUMP_APPLIED_PATHS
    assert "compile.include_dirs" not in DUMP_APPLIED_PATHS  # core: --include
    assert "scope.exclude_headers" not in DUMP_APPLIED_PATHS  # core: refused first


# ── typed API: DumpRequest.config_overrides ────────────────────────────────


def _request(**kwargs: object) -> DumpRequest:
    return DumpRequest(input=InputSpec(path=Path("libfoo.so")), **kwargs)  # type: ignore[arg-type]


#: Each applied key with a non-default value, as the CLI would parse it.
_APPLIED_VALUES: list[tuple[str, object]] = [
    ("compile.lang", "c"),
    ("debug.format", "dwarf"),
    ("debug.dwarf_only", True),
    ("debug.debuginfod", True),
    ("debug.debuginfod_url", "https://debug.example"),
    ("build.compile_db_filter", "^src/"),
]


@pytest.mark.parametrize(("path", "value"), _APPLIED_VALUES)
def test_cli_and_typed_dump_resolve_the_same_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, value: object
) -> None:
    """``dump --set path=value`` and ``DumpRequest(config_overrides=...)`` agree
    on every field that key reaches, at the resolved config."""
    monkeypatch.chdir(tmp_path)  # no ambient .abicheck.yml above the test
    cli = _resolve(None, overrides={path: value})
    typed = with_dump_config_overrides(_request(config_overrides={path: value}))

    assert typed.lang == cli.lang
    assert typed.lang_explicit == cli.lang_explicit
    assert typed.input.compile_db_filter == cli.compile_db_filter
    assert typed.debug_format == cli.debug.format
    assert typed.dwarf_only == cli.debug.dwarf_only
    assert typed.debuginfod == cli.debug.debuginfod
    assert typed.debuginfod_url == cli.debug.debuginfod_url


def test_typed_set_refuses_a_key_with_no_typed_channel() -> None:
    # compile.std is applied since the typed compile fold (ADR-091 D4); a key
    # dump reads through no typed field is still refused, never dropped.
    with pytest.raises(ConfigOverrideError, match="sources.method"):
        with_dump_config_overrides(
            _request(config_overrides={"sources.method": "dump"})
        )


def test_typed_set_applies_a_compile_context_key() -> None:
    """``compile.std`` reaches the input's compile context, as ``dump --set`` does."""
    out = with_dump_config_overrides(
        _request(config_overrides={"compile.std": "c++17"})
    )
    assert out.input.compile is not None
    assert out.input.compile.gcc_option_tokens == ("-std=c++17",)


@pytest.mark.parametrize(
    "path", ["compile.ast_frontend_fallback", "compile.allow_unsupported_castxml"]
)
def test_typed_set_refuses_the_cli_only_env_toggles(path: str) -> None:
    """A process-wide env toggle with no typed field: refused, not a no-op."""
    with pytest.raises(ConfigOverrideError, match="no typed DumpRequest field"):
        with_dump_config_overrides(_request(config_overrides={path: True}))


def test_typed_set_refuses_a_core_key_and_names_its_flag() -> None:
    with pytest.raises(ConfigOverrideError, match="--exclude-header"):
        with_dump_config_overrides(
            _request(config_overrides={"scope.exclude_headers": ["x.h"]})
        )


def test_typed_argument_set_away_from_default_beats_the_override() -> None:
    request = _request(debug_format="btf", config_overrides={"debug.format": "dwarf"})
    assert with_dump_config_overrides(request).debug_format == "btf"


def test_typed_request_without_overrides_is_returned_unchanged() -> None:
    request = _request()
    assert with_dump_config_overrides(request) is request


def test_dump_request_config_overrides_default_is_an_empty_mapping() -> None:
    assert dict(_request().config_overrides) == {}


# ── run_dump(config_overrides=) ────────────────────────────────────────────


def test_run_dump_override_arguments_fills_defaults_and_keeps_typed_args() -> None:
    defaults = {"lang": "c++", "debug_format": None, "dwarf_only": False}
    filled = run_dump_override_arguments(
        {"compile.lang": "c", "debug.format": "dwarf"},
        {},
        defaults,
    )
    assert filled == {"lang": "c", "lang_explicit": True, "debug_format": "dwarf"}
    kept = run_dump_override_arguments(
        {"debug.format": "dwarf"}, {"debug_format": "btf"}, defaults
    )
    assert kept == {}  # an explicit argument beats the override


def test_run_dump_refuses_a_key_with_no_run_dump_parameter() -> None:
    with pytest.raises(ConfigOverrideError, match="no run_dump parameter"):
        run_dump_override_arguments(
            {"build.compile_db_filter": "^src/"}, {}, {"lang": "c++"}
        )


def test_run_dump_rejects_a_bad_override_before_extracting() -> None:
    with pytest.raises(ConfigOverrideError, match="sources.method"):
        run_dump(
            Path("missing-libfoo.so"),
            "elf",
            config_overrides={"sources.method": "dump"},
        )


def test_run_dump_takes_the_config_overrides_keyword() -> None:
    import inspect

    assert "config_overrides" in inspect.signature(run_dump).parameters


# ── project capture-variants --set ─────────────────────────────────────────

_VARIANTS = (
    "bundle_variants:\n  x86: {target_triple: x86_64-linux-gnu, compiler_family: gcc}\n"
)


def _capture_dry_run(*args: str):
    return CliRunner().invoke(
        main,
        ["project", "capture-variants", *args],
        catch_exceptions=False,
    )


def test_capture_set_replaces_the_declared_block_over_the_file(tmp_path: Path) -> None:
    config = _write_config(tmp_path, _VARIANTS)
    overridden = {
        "bundle_variants": {
            "x86": {"target_triple": "aarch64-linux-gnu", "compiler_family": "gcc"}
        }
    }
    assert load_bundle_variants_config(config).variants[0].target_triple == (
        "x86_64-linux-gnu"
    )
    loaded = load_bundle_variants_config(config, config_overrides=overridden)
    assert loaded.variants[0].target_triple == "aarch64-linux-gnu"


def test_capture_set_reaches_the_dry_run_plan(tmp_path: Path) -> None:
    config = _write_config(tmp_path, _VARIANTS)
    lib = tmp_path / "in"
    lib.mkdir()
    (lib / "liba.so").write_bytes(b"\x7fELF")
    result = _capture_dry_run(
        "--config",
        str(config),
        "--variant",
        f"x86={lib}",
        "--package",
        str(tmp_path / "pkg"),
        "--dry-run",
        "--set",
        "bundle_variants={x86: {target_triple: aarch64-linux-gnu, compiler_family: gcc}}",
        "-o",
        "json=-",
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output[result.output.index("{") :])
    assert payload["variants"]["x86"]["declared"]["target_triple"] == (
        "aarch64-linux-gnu"
    )


@pytest.mark.parametrize(
    "item",
    [
        "compile.std=c++17",  # not read by capture-variants
        "debug.dwarf_only=true",  # a dump key; capture's per-variant dump takes no config
    ],
)
def test_capture_refuses_a_key_it_does_not_apply(tmp_path: Path, item: str) -> None:
    config = _write_config(tmp_path, _VARIANTS)
    result = _capture_dry_run(
        "--config",
        str(config),
        "--variant",
        f"x86={tmp_path}",
        "--package",
        str(tmp_path / "pkg"),
        "--dry-run",
        "--set",
        item,
    )
    assert result.exit_code == 64, result.output
    assert "project capture-variants does not apply" in result.output


def test_capture_set_with_a_wrong_typed_block_is_a_usage_error(tmp_path: Path) -> None:
    config = _write_config(tmp_path, _VARIANTS)
    with pytest.raises(VariantCaptureError):
        load_bundle_variants_config(config, config_overrides={"bundle_variants": 5})


# ── commands that read no key --set can set take no --set ──────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["project", "validate"],
        ["project", "plan"],
        ["project", "history"],
        ["deps", "tree"],
        ["deps", "compare"],
        ["aggregate"],
    ],
)
def test_commands_without_a_config_key_take_no_set(argv: list[str]) -> None:
    help_text = CliRunner().invoke(main, [*argv, "--help"]).output
    assert "--set" not in help_text
    result = CliRunner().invoke(main, [*argv, "--set", "debug.dwarf_only=true"])
    assert result.exit_code == 64, result.output


def test_resolve_dump_project_config_wraps_a_refused_override(tmp_path: Path) -> None:
    """A typed or programmatic caller that skips the CLI's parse-time check gets
    the same refusal as exit 64 (DumpConfigError), naming the core flag."""
    with pytest.raises(DumpConfigError, match="--exclude-header"):
        _resolve(None, overrides={"scope.exclude_headers": ["x.h"]})

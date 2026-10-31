"""The typed API applies ``compile.*`` overrides exactly as ``--set`` does (ADR-091 D4).

``compare --set compile.K=V`` / ``dump --set compile.K=V`` and
``CompareRequest(config_overrides=...)`` / ``DumpRequest(config_overrides=...)`` /
``run_dump(config_overrides=...)`` must produce the same effective compile
settings for every key of
:data:`~abicheck.workflows.compile_config.COMPILE_CONTEXT_OVERRIDE_PATHS`.

The CLI side is the real Click command, stopped at the request it hands the shared
resolution. The typed side is the public entry point, stopped at the same place.
The two requests are compared on what the extraction reads: the effective
frontend (a side's own backend outranks the request-level one), the language and
whether it was stated, and the rest of each side's compile context. The oracle is
the CLI's own result, not the typed fold's helper.
"""

from __future__ import annotations

import dataclasses
import functools
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.compile_context import CompileContext
from abicheck.workflows.compile_config import COMPILE_CONTEXT_OVERRIDE_PATHS
from abicheck.workflows.request_inputs import InputSpec
from abicheck.workflows.service_requests import CompareRequest, DumpRequest

#: One or more non-default values per ``compile.*`` key, as ``--set`` text and as
#: the typed value it parses to.
CASES: list[tuple[str, str, object]] = [
    ("compile.compiler", "/usr/bin/g++", "/usr/bin/g++"),
    ("compile.compiler", "aarch64-linux-gnu-", "aarch64-linux-gnu-"),
    ("compile.frontend", "clang", "clang"),
    ("compile.frontend", "castxml", "castxml"),
    ("compile.frontend_context", "device", "device"),
    ("compile.lang", "c", "c"),
    ("compile.nostdinc", "true", True),
    ("compile.options", "[-O2, -fno-exceptions]", ["-O2", "-fno-exceptions"]),
    ("compile.std", "c++17", "c++17"),
    ("compile.sysroot", "/opt/sysroot", "/opt/sysroot"),
]

_IDS = [f"{path}={text}" for path, text, _value in CASES]


def test_every_compile_override_key_is_covered() -> None:
    assert {path for path, _text, _value in CASES} == COMPILE_CONTEXT_OVERRIDE_PATHS


class _Stop(Exception):
    pass


def _fake_elf(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x7fELF" + b"\x00" * 100)
    return path


def _effective(request: Any, side: Any) -> dict[str, object]:
    """What the extraction reads for *side* of *request*."""
    ctx = side.compile or CompileContext()
    frontend = ctx.frontend if ctx.frontend != "auto" else request.frontend
    frontend_context = (
        ctx.frontend_context
        if ctx.frontend_context != "host"
        else request.frontend_context
    )
    rest = dataclasses.replace(
        ctx, frontend="auto", frontend_context="host", defines=()
    )
    return {
        "frontend": frontend,
        "frontend_context": frontend_context,
        "lang": request.lang,
        "lang_explicit": request.lang_explicit,
        "context": rest,
    }


def _capture_resolution(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    import abicheck.workflows.compare.execute as execute_mod
    import abicheck.workflows.compare.resolve as resolve_mod

    seen: list[Any] = []

    def _capture(request: Any, *_a: object, **_kw: object) -> Any:
        seen.append(request)
        raise _Stop

    monkeypatch.setattr(resolve_mod, "resolve_compare_request", _capture)
    monkeypatch.setattr(execute_mod, "resolve_compare_request", _capture)
    return seen


@pytest.mark.parametrize(("path", "text", "value"), CASES, ids=_IDS)
def test_compare_cli_and_typed_request_resolve_the_same_compile_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    text: str,
    value: object,
) -> None:
    monkeypatch.chdir(tmp_path)  # no ambient .abicheck.yml
    old, new = _fake_elf(tmp_path, "old.so"), _fake_elf(tmp_path, "new.so")
    seen = _capture_resolution(monkeypatch)

    with pytest.raises(_Stop):
        CliRunner().invoke(
            main,
            ["compare", str(old), str(new), "--set", f"{path}={text}"],
            catch_exceptions=False,
        )
    from abicheck.workflows.compare.execute import run_compare_request

    with pytest.raises(_Stop):
        run_compare_request(
            CompareRequest(
                old=InputSpec(path=old),
                new=InputSpec(path=new),
                config_overrides={path: value},
            )
        )
    cli, typed = seen
    for side in ("old", "new"):
        expected = _effective(cli, getattr(cli, side))
        assert _effective(typed, getattr(typed, side)) == expected, side
    # And the key did reach the CLI's request (the test is not comparing two
    # unchanged defaults).
    baseline = CompareRequest(old=InputSpec(path=old), new=InputSpec(path=new))
    assert _effective(cli, cli.old) != _effective(baseline, baseline.old)


def _capture_dump_request(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    import abicheck.cli_dump_request as cli_dump_request

    real = cli_dump_request.build_dump_request
    seen: list[Any] = []

    def _capture(**kwargs: Any) -> Any:
        seen.append(real(**kwargs))
        raise _Stop

    monkeypatch.setattr(cli_dump_request, "build_dump_request", _capture)
    return seen


@pytest.mark.parametrize(("path", "text", "value"), CASES, ids=_IDS)
def test_dump_cli_and_typed_request_resolve_the_same_compile_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    text: str,
    value: object,
) -> None:
    from abicheck.workflows.dump.project_config import with_dump_config_overrides

    monkeypatch.chdir(tmp_path)
    lib = _fake_elf(tmp_path, "libfoo.so")
    seen = _capture_dump_request(monkeypatch)
    with pytest.raises(_Stop):
        CliRunner().invoke(
            main, ["dump", str(lib), "--set", f"{path}={text}"], catch_exceptions=False
        )
    (cli,) = seen
    typed = with_dump_config_overrides(
        DumpRequest(input=InputSpec(path=lib), config_overrides={path: value})
    )
    assert _effective(typed, typed.input) == _effective(cli, cli.input)
    baseline = DumpRequest(input=InputSpec(path=lib))
    assert _effective(cli, cli.input) != _effective(baseline, baseline.input)


@pytest.mark.parametrize(("path", "text", "value"), CASES, ids=_IDS)
def test_run_dump_shim_folds_the_same_compile_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    text: str,
    value: object,
) -> None:
    """``run_dump(config_overrides=)`` reaches the same settings as ``dump --set``."""
    import abicheck.workflows.dump.native as native

    monkeypatch.chdir(tmp_path)
    lib = _fake_elf(tmp_path, "libfoo.so")
    seen = _capture_dump_request(monkeypatch)
    with pytest.raises(_Stop):
        CliRunner().invoke(
            main, ["dump", str(lib), "--set", f"{path}={text}"], catch_exceptions=False
        )
    (cli,) = seen

    calls: list[dict[str, Any]] = []

    # The real signature (its defaults are what the shim fills), a stub body.
    @functools.wraps(native._run_dump_uncached)
    def _uncached(*_args: object, **kwargs: Any) -> Any:
        calls.append(kwargs)
        raise _Stop

    from abicheck.workflows.run_dump_scope import wrap_run_dump_with_dependency_scope

    shim = wrap_run_dump_with_dependency_scope(_uncached)
    with pytest.raises(_Stop):
        shim(lib, "elf", config_overrides={path: value})
    (kwargs,) = calls

    @dataclasses.dataclass
    class _Shim:
        frontend: str
        frontend_context: str
        lang: str
        lang_explicit: bool

    @dataclasses.dataclass
    class _Side:
        compile: CompileContext | None

    shim_request = _Shim(
        frontend=kwargs.get("frontend", "auto"),
        frontend_context="host",
        lang=kwargs.get("lang", "c++"),
        lang_explicit=kwargs.get("lang_explicit", False),
    )
    assert _effective(shim_request, _Side(kwargs.get("compile"))) == _effective(
        cli, cli.input
    )


def test_a_typed_value_beats_the_compile_override() -> None:
    """The typed argument is the CLI layer: set away from its default, it wins."""
    from abicheck.workflows.compare.execute import _with_config_overrides

    pinned = CompileContext(
        gcc_path="/opt/cc", sysroot=Path("/mine"), frontend="castxml"
    )
    request = CompareRequest(
        old=InputSpec(path=Path("a.so"), compile=pinned),
        new=InputSpec(path=Path("b.so")),
        frontend="castxml",
        lang="c",
        lang_explicit=True,
        config_overrides={
            "compile.compiler": "/usr/bin/g++",
            "compile.sysroot": "/theirs",
            "compile.frontend": "clang",
            "compile.lang": "c++",
            "compile.options": ["-O2"],
        },
    )
    out = _with_config_overrides(request)
    assert out.frontend == "castxml"
    assert out.lang == "c" and out.lang_explicit
    assert out.old.compile is not None
    assert out.old.compile.gcc_path == "/opt/cc"
    assert out.old.compile.sysroot == Path("/mine")
    assert out.old.compile.frontend == "castxml"
    # The side the caller left unset takes the override.
    assert out.new.compile is not None
    assert out.new.compile.gcc_path == "/usr/bin/g++"
    assert out.new.compile.sysroot == Path("/theirs")
    # The override's tokens come first, so a caller's repeated flag wins.
    assert out.new.compile.gcc_option_tokens == ("-O2",)


def test_no_compile_override_leaves_the_request_unchanged() -> None:
    from abicheck.workflows.compare.execute import _with_config_overrides

    request = CompareRequest(
        old=InputSpec(path=Path("a.so")),
        new=InputSpec(path=Path("b.so")),
        config_overrides={"scope.public": False},
    )
    out = _with_config_overrides(request)
    assert out.old.compile is None and out.new.compile is None
    assert out.frontend == "auto"

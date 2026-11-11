# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``gcc`` is a known, explicit header frontend (ADR-092) on every entry
point that names one, while genuinely unknown names stay refused."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys

import pytest

from abicheck.buildsource.inline import BuildConfig
from abicheck.errors import ValidationError
from abicheck.extract.header_ast_backend import _resolve_header_backend
from tests._abicheck_subprocess import abicheck_command

_UNKNOWN = ("edg", "msvc", "gcc-plugin", "gcc-experimental")


def test_resolver_accepts_gcc() -> None:
    assert _resolve_header_backend("gcc") == "gcc"
    assert _resolve_header_backend("GCC") == "gcc"


@pytest.mark.parametrize("name", _UNKNOWN)
def test_resolver_rejects_unknown_names(name: str) -> None:
    # "gcc-experimental" is the dump's producer stamp, not a selectable
    # frontend: the two names have different jobs and are not unified.
    with pytest.raises(ValidationError):
        _resolve_header_backend(name)


def test_build_config_accepts_gcc() -> None:
    assert (
        BuildConfig.from_dict({"compile": {"frontend": "gcc"}}).compile_frontend
        == "gcc"
    )


@pytest.mark.parametrize("name", _UNKNOWN)
def test_build_config_rejects_unknown_names(name: str) -> None:
    with pytest.raises(ValueError, match="compile.frontend"):
        BuildConfig.from_dict({"compile": {"frontend": name}})


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell shim")
@pytest.mark.parametrize("explicit", [False, True])
def test_only_an_explicit_config_picks_the_native_compiler(tmp_path, explicit) -> None:
    """Trust policy: an auto-discovered ``.abicheck.yml`` (a checked-out,
    possibly hostile tree) may not choose the executable the native GCC
    backend runs. Executed, not text-asserted: the shim records any call."""
    marker = tmp_path / "shim-was-executed"
    shim = tmp_path / "evil-gcc"
    shim.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 0\n")
    shim.chmod(0o755)
    (tmp_path / ".abicheck.yml").write_text(
        f"compile:\n  frontend: gcc\n  compiler: {shim}\n  lang: c++\n"
    )
    (tmp_path / "api.h").write_text("int api();\n")
    lib = tmp_path / "lib.so"
    cc = shutil.which("cc") or shutil.which("gcc")
    if cc is None:
        pytest.skip("needs a C compiler to build the ELF operand")
    (tmp_path / "api.c").write_text("int api(void) { return 0; }\n")
    subprocess.run(
        [cc, "-shared", "-fPIC", tmp_path / "api.c", "-o", lib],
        check=True,
        capture_output=True,
    )
    env = {**os.environ, "ABICHECK_GCC_PLUGIN": str(tmp_path / "no-plugin.so")}
    env.pop("ABICHECK_AST_FRONTEND", None)
    result = subprocess.run(
        abicheck_command(
            "dump",
            lib,
            "-H",
            tmp_path / "api.h",
            "-o",
            tmp_path / "s.json",
            *(("--config", tmp_path / ".abicheck.yml") if explicit else ()),
        ),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    # Control: the same file passed explicitly is trusted, and the native
    # backend does run that compiler -- so the auto-discovered case proves
    # the policy, not an unreached code path. Neither run yields a snapshot.
    assert marker.exists() is explicit, result.stderr
    assert not (tmp_path / "s.json").exists()
    assert result.returncode != 0
    assert "Traceback" not in result.stderr, result.stderr
    if explicit:
        # The shim prints no version banner: a clear refusal, not a crash.
        assert "requires a real GCC compiler" in result.stderr, result.stderr

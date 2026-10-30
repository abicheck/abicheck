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

"""Schema validation of the project config the CLI loads for a run.

PR #1222 (Codex review, P2): a schema-invalid value in a field the Action
used to strip before the CLI parsed it (``build.query: 7``) was silently
dropped, turning a config that ``compare``/``dump --config`` refuse into a
silently accepted run. ADR-091 D4 removed the Action's own overlay: the
compile-context inputs are ``--set compile.*`` and the config file is read by
the CLI itself. So the fail-loud and the valid-document cases are asserted
against the real CLI, which owns the schema check now, and the sources-root
case against the same loaders the CLI uses.

Each test runs the same ``dump`` the Action would issue, with the ``--set``
tokens the Action forwards, and reads the outcome the CLI produces.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from _abicheck_subprocess import abicheck_command

from abicheck.buildsource.build_config import BuildConfig, load_build_config
from abicheck.config_paths import discover_build_config
from abicheck.workflows.compile_config import resolve_compile_context

_SET_COMPILER = 'compile.compiler="/opt/gcc-14/bin/g++"'


def _lib(tmp_path: Path) -> Path:
    """A real ELF shared object, so ``dump`` reaches config validation."""
    if shutil.which("gcc") is None:
        pytest.skip("needs gcc to build a shared object")
    src = tmp_path / "a.c"
    src.write_text("int f(int x){return x+1;}\n", encoding="utf-8")
    lib = tmp_path / "liba.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", str(src), "-o", str(lib)], check=True
    )
    return lib


def _dump(cwd: Path, lib: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        abicheck_command(
            "dump",
            str(lib),
            *extra,
            "--set",
            _SET_COMPILER,
            "-o",
            str(cwd / "out.json"),
        ),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


class TestCliValidatesTheConfigBeforeAnyStripping:
    """The CLI rejects a schema-invalid config, discovered or explicit."""

    def test_discovered_invalid_build_query_type_fails_loud(
        self, tmp_path: Path
    ) -> None:
        lib = _lib(tmp_path)
        (tmp_path / ".abicheck.yml").write_text(
            "build:\n  query: 7\n", encoding="utf-8"
        )
        result = _dump(tmp_path, lib)
        assert result.returncode != 0
        assert "build.query" in result.stdout + result.stderr

    def test_explicit_build_config_invalid_document_also_fails_loud(
        self, tmp_path: Path
    ) -> None:
        """Schema validity is independent of the trust question: an explicit
        build-config is trusted to run ``build.query``, but it must still be a
        valid document."""
        lib = _lib(tmp_path)
        build_config = tmp_path / "my-build-config.yml"
        build_config.write_text("build:\n  query: 7\n", encoding="utf-8")
        result = _dump(tmp_path, lib, "--config", str(build_config))
        assert result.returncode != 0
        assert "build.query" in result.stdout + result.stderr

    def test_valid_discovered_document_is_unaffected(self, tmp_path: Path) -> None:
        """Negative control: a schema-valid discovered document still loads and
        its own settings survive."""
        lib = _lib(tmp_path)
        (tmp_path / ".abicheck.yml").write_text(
            "severity:\n  abi_breaking: error\n", encoding="utf-8"
        )
        result = _dump(tmp_path, lib)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            load_build_config(tmp_path / ".abicheck.yml").severity_abi_breaking
            == "error"
        )


class TestDumpClearsSourcesRootBlocksWhenItHasNoOwnConfig:
    """P1 finding (PR #1222 ninth round): ``dump`` with a ``--sources`` tree that
    has no config of its own clears ``build:``/``sources:``/``source:``/
    ``debug:`` and the sources-root ``compile:`` to defaults. The checkout's
    severity still applies, and this Action's ``compile.compiler`` still applies
    on top."""

    def test_dump_mode_clears_all_five_sources_root_blocks(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "severity:\n  abi_breaking: error\n"
            "build:\n  system: make\n"
            "sources:\n  graph: full\n"
            "sources:\n  method: headers\n"
            "debug:\n  format: btf\n"
            "compile:\n  sysroot: /opt/checkout-sysroot\n",
            encoding="utf-8",
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        # The --sources tree has its own, empty config: selected, all defaults.
        (src_dir / ".abicheck.yml").write_text("", encoding="utf-8")
        cfg = load_build_config(discover_build_config(src_dir))
        assert cfg.system == BuildConfig().system
        assert cfg.graph_detail == BuildConfig().graph_detail
        assert cfg.source_method is None
        assert cfg.debug_format is None
        ctx, _ = resolve_compile_context(
            sysroot=None,
            nostdinc=False,
            header_backend="auto",
            includes=(),
            build_config=None,
            sources=src_dir,
            config_overrides={"compile.compiler": "/opt/gcc-14/bin/g++"},
        )
        assert ctx.sysroot is None
        assert ctx.gcc_path == "/opt/gcc-14/bin/g++"
        assert (
            load_build_config(tmp_path / ".abicheck.yml").severity_abi_breaking
            == "error"
        )

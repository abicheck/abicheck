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

"""``dump --dry-run``'s ``build.query`` trust report -- Flow-2
``abicheck_inputs/`` pack recognition (CLI cleanup phase two, PR F
prerequisite 3).

Split out of ``tests/test_dry_run_build_query_contract.py`` (already near
its 2000-line hard cap; root ``AGENTS.md`` "Files that are large") rather
than added inline there. Mirrors that file's own classic-``BuildSourcePack``
precedence tests (``test_build_info_pack_with_compile_units_takes_
precedence``, etc.) one-for-one, for the Flow-2 pack shape --
``abicheck/cli_dump_dry_run_build_query.py``'s ``_is_pack_dir_any``/
``_pack_dir_build_evidence`` now recognize both shapes uniformly, closing
what that module's docstring used to list as a "known, deliberately
unclosed gap."
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.buildsource import SourceAbiTu, SourceEntity, SourceLocation
from abicheck.buildsource.inputs_pack import ABICHECK_INPUTS_VERSION, INPUTS_KIND
from abicheck.cli import main


def _write_config(sources: Path, *, compile_db: str | None = None) -> Path:
    """Byte-for-byte mirror of ``TestDumpDryRunBuildQueryTrust._write_config``."""
    cfg = sources / ".abicheck.yml"
    body = "build:\n  query: cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON\n"
    if compile_db:
        body += f"  compile_db: {compile_db}\n"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def _write_flow2_inputs_pack(root: Path, *, with_compile_db: bool) -> Path:
    """A minimal, valid Flow-2 ``abicheck_inputs/`` pack (ADR-035 D5).

    Mirrors ``tests/test_inputs_pack.py``'s own ``_write_inputs_pack``
    fixture, trimmed to what this dry-run section reads: a
    ``kind: abicheck_inputs`` manifest, an empty ``source_facts/``, and --
    when *with_compile_db* -- a ``build/compile_commands.json``.
    """
    pack = root / "abicheck_inputs"
    (pack / "source_facts").mkdir(parents=True)
    if with_compile_db:
        (pack / "build").mkdir(parents=True)
        (pack / "build" / "compile_commands.json").write_text(
            json.dumps(
                [
                    {
                        "directory": str(root),
                        "file": "api.c",
                        "arguments": ["cc", "-c", "api.c"],
                    }
                ]
            ),
            encoding="utf-8",
        )
    manifest = {
        "kind": INPUTS_KIND,
        "abicheck_inputs_version": ABICHECK_INPUTS_VERSION,
        "library": "libfoo.so",
        "version": "1.0",
        "created_by": "abicheck-clang-plugin 0.1",
    }
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pack


def test_flow2_build_info_pack_with_compile_units_takes_precedence(
    tmp_path: Path,
) -> None:
    # Closes the "known, deliberately unclosed gap" this module's own
    # docstring used to document: a Flow-2 abicheck_inputs/ pack is now
    # recognized the identical way a classic BuildSourcePack is (mirrors
    # TestDumpDryRunBuildQueryTrust.
    # test_build_info_pack_with_compile_units_takes_precedence).
    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"")
    header = tmp_path / "api.h"
    header.write_text("int foo(int x);\n", encoding="utf-8")
    cfg = _write_config(tmp_path)
    pack_dir = _write_flow2_inputs_pack(tmp_path, with_compile_db=True)

    result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so_path),
            "--sources",
            str(tmp_path),
            "-H",
            str(header),
            "--build-info",
            str(pack_dir),
            "--config",
            str(cfg),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "will NOT run" in result.output
    assert "already carries L3 compile units" in result.output


def test_flow2_build_info_pack_without_compile_db_still_reports_will_run(
    tmp_path: Path,
) -> None:
    # The counterpart: a Flow-2 pack with no build/compile_commands.json
    # does not short-circuit resolution (mirrors
    # TestDumpDryRunBuildQueryTrust.
    # test_build_info_pack_without_compile_units_still_reports_will_run).
    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"")
    header = tmp_path / "api.h"
    header.write_text("int foo(int x);\n", encoding="utf-8")
    cfg = _write_config(tmp_path)
    pack_dir = _write_flow2_inputs_pack(tmp_path, with_compile_db=False)

    result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so_path),
            "--sources",
            str(tmp_path),
            "-H",
            str(header),
            "--build-info",
            str(pack_dir),
            "--config",
            str(cfg),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "will run (trusted -- explicit --config)" in result.output


def test_flow2_sources_pack_with_compile_units_takes_precedence(
    tmp_path: Path,
) -> None:
    # Mirrors TestDumpDryRunBuildQueryTrust.
    # test_sources_pack_with_compile_units_takes_precedence, via --sources
    # instead of --build-info.
    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"")
    header = tmp_path / "api.h"
    header.write_text("int foo(int x);\n", encoding="utf-8")
    src_pack = _write_flow2_inputs_pack(tmp_path, with_compile_db=True)

    result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so_path),
            "--sources",
            str(src_pack),
            "-H",
            str(header),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "will NOT run" in result.output
    assert "--sources" in result.output
    assert "already carries L3 compile units" in result.output


def _tu(name: str, *, mangled: str) -> SourceAbiTu:
    """A minimal, single-declaration per-TU dump (mirrors test_inputs_pack.py's own)."""
    ent = SourceEntity(
        id=f"decl://{name}",
        kind="function",
        qualified_name=name,
        mangled_name=mangled,
        signature_hash="sig1",
        source_location=SourceLocation(
            path=f"include/{name}.h", line=3, origin="PUBLIC_HEADER"
        ),
        visibility="public_header",
    )
    return SourceAbiTu(
        tu_id="cu://src/foo.cpp#cfg:abc",
        target_id="target://libfoo",
        source="src/foo.cpp",
        public_header_roots=[f"include/{name}.h"],
        functions=[ent],
    )


def test_flow2_build_info_pack_with_invalid_source_facts_blocks(tmp_path: Path) -> None:
    # Codex review, fresh evidence: a Flow-2 pack whose manifest/compile DB
    # are readable but whose source_facts fail hard validation (here: two
    # records sharing one tu_id) must not report the invocation as valid.
    # embed_build_source's real _load_inputs_pack_or_raise runs
    # validate_inputs_pack -> raises click.ClickException (exit 1) for this
    # exact input before ever reaching collect_inline_pack/build.query
    # precedence -- the lighter compile-DB-only parse this dry-run section
    # otherwise uses never read source_facts/ at all, so it used to report
    # "will NOT run -- already carries L3 compile units" for an invocation
    # that cannot actually run.
    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"")
    header = tmp_path / "api.h"
    header.write_text("int foo(int x);\n", encoding="utf-8")
    cfg = _write_config(tmp_path)
    pack_dir = _write_flow2_inputs_pack(tmp_path, with_compile_db=True)
    lines = "\n".join(
        json.dumps(_tu("foo", mangled="_Z3foov").to_dict()) for _ in range(2)
    )
    (pack_dir / "source_facts" / "libfoo.jsonl").write_text(
        lines + "\n", encoding="utf-8"
    )

    result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so_path),
            "--sources",
            str(tmp_path),
            "-H",
            str(header),
            "--build-info",
            str(pack_dir),
            "--config",
            str(cfg),
            "--dry-run",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "will run" not in result.output
    assert "validation error" in result.output.lower()

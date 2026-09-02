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

"""ADR-063 Phase 1: the legacy ``-p``/``--compile-db`` auto-match's own
derived flags, threaded into the typed ``DumpRequest``/``execute_dump_request``
pipeline.

See ``docs/contribute/known-gaps.md``'s "ADR-063 Phase 1" entry for the full
mechanism this closes and what remains open. In short: ``dump_cmd``'s legacy
``-p``/``--compile-db`` auto-match (``cli_helpers_compare.
_resolve_build_context_flags``, backed by ``build_context_for_header``/
``build_context_union_fallback``) is a *different* code path from the P0.3
L3->L2 fold (``buildsource.l2_seed.seed_includes_and_fold_compile_context``,
backed by ``header_compile_context.resolve_header_compile_context``) the
typed pipeline already runs. The two disagree on a header no compile unit's
source text ``#include``s: the fold returns ``context=None`` (nothing to
apply — no union fallback, see ``resolve_header_compile_context``'s own
docstring), while the legacy match's ``build_context_for_header`` falls back
to ``build_context_union_fallback``, which still merges the compile
database's defines and reports a match. Before this change, that union-
fallback evidence reached only the real ``dump`` CLI's ELF path
(``cli_dump_helpers.perform_elf_dump``, via ``legacy_build_context_flags``);
the typed pipeline (``execute_dump_request`` and everything it feeds --
``run_dump_request``, ``compare``'s implicit dump, ``scan``'s candidate) had
no way to see it at all.

``execute_dump_request``'s new ``legacy_compile_db_tokens`` parameter closes
that gap for any caller willing to compute the legacy match's own flags
(exactly as ``dump_cmd`` already does, via ``_resolve_build_context_flags``)
and pass them through -- proven here end to end against a real compile
database and a real clang L2 parse, not merely unit-tested against the
merge helper in isolation. The real ``dump`` CLI's ELF run does **not** pass
this parameter yet (it still executes through ``perform_elf_dump``, not
``execute_dump_request`` -- see the known-gaps entry for what still needs
routing), so this file additionally pins that the parameter is a true no-op
by default: the *same* typed request, run without
``legacy_compile_db_tokens``, must NOT see the union-fallback evidence,
proving the new capability is opt-in and additive rather than a silent
default-on behavior change.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="ELF/Linux-scoped repro (real g++-compiled .so + compile_commands.json)",
    ),
]

_HAVE_GXX = shutil.which("g++") is not None
_HAVE_CLANG = shutil.which("clang") is not None
_SKIP_REASON = "needs a real g++ and clang toolchain"


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A public header with a macro-guarded field, a compile database whose
    sole TU does NOT ``#include`` that header at all (so the P0.3 fold's own
    ``_cu_references_any_header`` match fails closed to ``None``), but does
    carry an ABI-relevant ``-DWIDE=1`` the legacy match's union fallback
    still picks up regardless of the missing ``#include``."""
    header = tmp_path / "api.h"
    header.write_text(
        "#pragma once\nstruct S {\n  int a;\n#ifdef WIDE\n  long long b;\n#endif\n};\n",
        encoding="utf-8",
    )
    # Deliberately does NOT `#include "api.h"` -- the whole point of this
    # shape is a compile unit real enough to carry `-DWIDE=1` but with no
    # textual evidence tying it to the header at all.
    src = tmp_path / "a.cpp"
    src.write_text("int not_related() { return 1; }\n", encoding="utf-8")
    so_path = tmp_path / "libapi.so"
    subprocess.run(
        ["g++", "-std=c++17", "-shared", "-fPIC", "-o", str(so_path), str(src)],
        check=True,
        capture_output=True,
    )
    compile_db = tmp_path / "compile_commands.json"
    compile_db.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "arguments": [
                        "g++",
                        "-std=c++17",
                        "-DWIDE=1",
                        "-fPIC",
                        "-c",
                        str(src),
                        "-o",
                        "a.o",
                    ],
                    "file": str(src),
                }
            ]
        ),
        encoding="utf-8",
    )
    return so_path, header, compile_db


def _wide_field_present(snapshot: dict) -> bool:
    fields = [
        f.get("name")
        for t in snapshot.get("types", [])
        if t.get("name") == "S"
        for f in t.get("fields", [])
    ]
    assert fields, snapshot.get("types")
    return "b" in fields


@pytest.mark.skipif(not (_HAVE_GXX and _HAVE_CLANG), reason=_SKIP_REASON)
class TestLegacyMatchReachesTheRealCliPath:
    """Baseline/oracle: the real ``dump`` CLI already folds the legacy
    union-fallback flags into the header parse via
    ``cli_dump_helpers.perform_elf_dump``'s own ``legacy_build_context_flags``
    -- this is the fixed point the typed-pipeline threading below must
    reproduce, not an assumption."""

    def test_cli_dump_sees_the_union_fallback_define(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        so_path, header, compile_db = _project(tmp_path)
        out = tmp_path / "cli.json"
        result = CliRunner().invoke(
            main,
            [
                "dump",
                str(so_path),
                "-H",
                str(header),
                "--sources",
                str(header.parent),
                "--build-info",
                str(compile_db),
                "--depth",
                "headers",
                "--ast-frontend",
                "clang",
                "-o",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        from abicheck.serialization import load_snapshot_document

        snapshot = load_snapshot_document(out)
        assert _wide_field_present(snapshot), (
            "the real dump CLI's own legacy -p/--compile-db union-fallback "
            "match stopped reaching the header parse -- this test's own "
            "fixture assumption is now stale"
        )


@pytest.mark.skipif(not (_HAVE_GXX and _HAVE_CLANG), reason=_SKIP_REASON)
class TestTypedApiThreadsTheLegacyMatch:
    """The typed ``DumpRequest``/``execute_dump_request`` path, given the
    identical evidence and the CLI's own already-computed legacy-match
    flags, must resolve the *same* compile context the CLI real run does --
    and must NOT do so when the new parameter isn't passed, since nothing in
    this codebase wires it in by default yet (that is the remaining, still-
    open part of ADR-063 Phase 1 -- see the module docstring)."""

    @staticmethod
    def _request(so_path: Path, header: Path, compile_db: Path):
        from abicheck.api_types import DumpRequest, InputSpec

        return DumpRequest(
            input=InputSpec(
                path=so_path,
                headers=(header,),
                sources=header.parent,
                build_info=compile_db,
            ),
            frontend="clang",
            depth="headers",
        )

    def test_without_legacy_tokens_the_typed_path_does_not_see_the_define(
        self, tmp_path: Path
    ) -> None:
        """Proves the gap this slice closes actually existed, and that the
        new parameter is genuinely opt-in rather than vacuously already
        equal: with no `legacy_compile_db_tokens` passed (the pre-existing,
        still-default behavior for every caller today), the typed path's
        own P0.3 fold fails closed on this header (no `#include` evidence)
        and the union-fallback flags never reach the parse at all."""
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        so_path, header, compile_db = _project(tmp_path)
        request = self._request(so_path, header, compile_db)
        result = execute_dump_request(resolve_dump_request(request))
        fields = [
            f.name for t in result.snapshot.types if t.name == "S" for f in t.fields
        ]
        assert fields, result.snapshot.types
        assert "b" not in fields, (
            "typed path unexpectedly saw the -DWIDE=1 define with no "
            "legacy_compile_db_tokens threaded -- either the fold now "
            "matches this shape (fixture assumption stale) or the new "
            "parameter default changed behavior for existing callers"
        )

    def test_with_legacy_tokens_the_typed_path_agrees_with_the_real_cli_run(
        self, tmp_path: Path
    ) -> None:
        """The actual proof: the legacy match's flags, computed exactly the
        way `dump_cmd` computes them today
        (`cli_helpers_compare._resolve_build_context_flags`), threaded
        through `execute_dump_request(..., legacy_compile_db_tokens=...)`,
        make the typed path resolve the identical header-AST evidence the
        real CLI run already does."""
        from abicheck.cli_helpers_compare import _resolve_build_context_flags
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        so_path, header, compile_db = _project(tmp_path)

        # The CLI's own real-execution branch computes exactly this, from
        # exactly this input, before folding it into `effective_gcc_options`
        # -- see `cli.py`'s `dump_cmd` (`_resolve_build_context_flags(
        # effective_compile_db, headers, compile_db_filter)`).
        legacy_flags, matched = _resolve_build_context_flags(
            compile_db, (header,), None
        )
        assert matched, "the legacy union-fallback match itself did not fire"
        assert legacy_flags, "the legacy match derived no flags to thread"

        request = self._request(so_path, header, compile_db)
        result = execute_dump_request(
            resolve_dump_request(request),
            legacy_compile_db_tokens=tuple(legacy_flags),
        )
        fields = [
            f.name for t in result.snapshot.types if t.name == "S" for f in t.fields
        ]
        assert fields, result.snapshot.types
        assert "b" in fields, (
            "typed path with legacy_compile_db_tokens threaded still did "
            "not see -DWIDE=1 -- the merge into the resolved CompileContext "
            "is not reaching the real service.resolve_input() parse"
        )

    def test_fold_wins_over_legacy_tokens_when_it_applies(self, tmp_path: Path) -> None:
        """Precedence pin (mirrors `perform_elf_dump`'s own "legacy-match
        overlap" fix): when the P0.3 fold DOES independently match a header
        (a real `#include`), its own result must win outright -- passing
        `legacy_compile_db_tokens` must not additionally stack a duplicate
        `-D` on top of it."""
        from abicheck.api_types import DumpRequest, InputSpec
        from abicheck.service_dump_pipeline import (
            execute_dump_request,
            resolve_dump_request,
        )

        header = tmp_path / "api.h"
        header.write_text("#pragma once\nstruct S { int a; };\n", encoding="utf-8")
        src = tmp_path / "a.cpp"
        src.write_text(
            '#include "api.h"\nint f(S s) { return s.a; }\n', encoding="utf-8"
        )
        so_path = tmp_path / "libapi.so"
        subprocess.run(
            ["g++", "-std=c++17", "-shared", "-fPIC", "-o", str(so_path), str(src)],
            check=True,
            capture_output=True,
        )
        compile_db = tmp_path / "compile_commands.json"
        compile_db.write_text(
            json.dumps(
                [
                    {
                        "directory": str(tmp_path),
                        "arguments": [
                            "g++",
                            "-std=c++17",
                            "-DFOO=1",
                            "-fPIC",
                            "-c",
                            str(src),
                            "-o",
                            "a.o",
                        ],
                        "file": str(src),
                    }
                ]
            ),
            encoding="utf-8",
        )
        request = DumpRequest(
            input=InputSpec(
                path=so_path,
                headers=(header,),
                sources=header.parent,
                build_info=compile_db,
            ),
            frontend="clang",
            depth="headers",
        )

        # The fold DOES match here (real #include), so it applies on its
        # own with no legacy tokens at all. The fold's own derived flags
        # ride on `gcc_option_tokens` (a tuple), not `gcc_options` (the
        # legacy match's own merge target) -- both fields together are the
        # actual compile context, so both are checked here.
        def _rendered(ctx) -> str:
            return " ".join((ctx.gcc_options or "", *ctx.gcc_option_tokens)).strip()

        baseline = execute_dump_request(resolve_dump_request(request))
        assert baseline.effective_compile_context is not None
        baseline_flags = _rendered(baseline.effective_compile_context)
        assert baseline_flags.count("-DFOO=1") == 1, baseline_flags

        # Threading the identical legacy-derived flags must not double them
        # up on top of the fold's own result.
        stacked = execute_dump_request(
            resolve_dump_request(request),
            legacy_compile_db_tokens=("-DFOO=1",),
        )
        assert stacked.effective_compile_context is not None
        stacked_flags = _rendered(stacked.effective_compile_context)
        assert stacked_flags.count("-DFOO=1") == 1, (
            "legacy_compile_db_tokens was folded in even though the P0.3 "
            "fold already applied -- precedence regression"
        )
        assert stacked_flags == baseline_flags

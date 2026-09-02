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

"""Real-clang regression test for a defect introduced and reverted within
this same PR: wiring ``dumper.dump()``'s declaration-provenance widening
(ADR-024/ADR-015's ``include_search_dirs``) straight off the dump's full
compile include path (``extra_includes``) rather than off only the caller's
own *explicit* ``-I``/``--include`` list.

``extra_includes`` also carries directories a dump auto-derives purely so an
umbrella ``-H`` header's own relative ``#include``s resolve -- typically the
umbrella header's own directory (``header_utils.resolve_inferred_header_roots``'s
"P3" auto-add). That directory can just as easily hold a genuinely *private*
sibling header, and folding it into the public-directory set silently
promoted that sibling to ``PUBLIC_HEADER`` -- defeating ADR-024's whole
private-header scoping for the (very common) case of a public umbrella
header living next to a private implementation-detail header in one
directory. Caught by the example suite's own real, compiled
``case184_internal_enum_churn_scoped`` (not by any unit test, since none of
this PR's own unit tests happened to co-locate the public and private
headers in one directory) -- this module exists so a future change to this
same wiring is caught here too, at unit speed and with the exact mechanism
named, rather than only by a slow, real-compiler example run.

Modeled on ``test_clang_header_backend_integration.py``'s own pattern:
self-skip on missing tools, not marked ``integration`` (no castxml
requirement — this is clang-only).
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump
from abicheck.model import ScopeOrigin

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="clang L2 backend integration test is ELF/Linux-scoped",
)


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _require_tools() -> None:
    if not (_have("clang") and _have("g++")) or platform.machine().lower() not in {
        "x86_64",
        "amd64",
    }:
        pytest.skip("requires an x86-64 clang + g++ toolchain")


def test_private_sibling_header_not_promoted_by_auto_derived_include_dir(
    tmp_path: Path,
) -> None:
    """The exact case184 shape: a public umbrella header (``api.h``)
    ``#include``s a private sibling (``internal.h``) living in the SAME
    directory, and no explicit ``-I`` is given at all -- the only include
    directory in play is the one ``resolve_inferred_header_roots`` auto-adds
    so the umbrella header's own relative ``#include`` resolves. That
    auto-added directory must NOT promote ``internal.h``'s own declarations
    to ``PUBLIC_HEADER``."""
    _require_tools()
    (tmp_path / "internal.h").write_text(
        "#pragma once\nvoid priv(void);\n", encoding="utf-8"
    )
    (tmp_path / "api.h").write_text(
        '#pragma once\n#include "internal.h"\nvoid pub(void);\n', encoding="utf-8"
    )
    src = tmp_path / "lib.cpp"
    src.write_text(
        '#include "api.h"\nvoid pub(void) {}\nvoid priv(void) {}\n', encoding="utf-8"
    )
    so = tmp_path / "lib.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-g", "-o", str(so), str(src)],
        check=True,
        capture_output=True,
    )

    snap = dump(
        so,
        [tmp_path / "api.h"],
        # extra_includes=[tmp_path]: mirrors what perform_elf_dump's own P3
        # auto-add (`resolve_inferred_header_roots`) would compute for a -H
        # umbrella header with no explicit -I of its own -- the umbrella
        # header's own directory, needed purely so its relative #include
        # resolves. This is the exact value the pre-fix code forwarded
        # straight to provenance as `include_search_dirs=extra_includes`.
        [tmp_path],
        header_backend="clang",
        public_headers=[tmp_path / "api.h"],
        # public_include_search_dirs deliberately omitted (no explicit -I
        # was actually given) -- the fix under test is that dump() no
        # longer falls back to widening off extra_includes when this is
        # absent.
    )
    by_name = {f.name: f for f in snap.functions}
    assert by_name["pub"].origin is ScopeOrigin.PUBLIC_HEADER
    assert by_name["priv"].origin is ScopeOrigin.PRIVATE_HEADER, (
        "a private sibling header living next to the public -H header must "
        "not be promoted merely by the umbrella header's own auto-derived "
        "#include-resolution directory"
    )


def test_explicit_include_dir_still_promotes_transitively_reached_header(
    tmp_path: Path,
) -> None:
    """Positive control -- the original defect-4/5 fix this module's own
    defect is adjacent to must keep working: a header reached transitively
    from the ``-H`` root, living under a directory the caller *explicitly*
    passed via ``-I``, is still promoted to ``PUBLIC_HEADER``."""
    _require_tools()
    include_dir = tmp_path / "include"
    detail_dir = include_dir / "detail"
    detail_dir.mkdir(parents=True)
    (detail_dir / "impl.h").write_text(
        "#pragma once\nvoid dep(void);\n", encoding="utf-8"
    )
    (include_dir / "api.h").write_text(
        '#pragma once\n#include "detail/impl.h"\nvoid pub(void);\n',
        encoding="utf-8",
    )
    src = tmp_path / "lib.cpp"
    src.write_text(
        '#include "api.h"\nvoid pub(void) {}\nvoid dep(void) {}\n', encoding="utf-8"
    )
    so = tmp_path / "lib.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-g", f"-I{include_dir}", "-o", str(so), str(src)],
        check=True,
        capture_output=True,
    )

    snap = dump(
        so,
        [include_dir / "api.h"],
        [include_dir],
        header_backend="clang",
        public_headers=[include_dir / "api.h"],
        # dump() itself is the low-level primitive; a real caller (the CLI/
        # service layer) is what decides which of its own directories are
        # genuinely explicit -I roots and threads only those here -- see
        # this module's own docstring and dump()'s own docstring note on
        # `public_include_search_dirs`.
        public_include_search_dirs=[include_dir],
    )
    by_name = {f.name: f for f in snap.functions}
    assert by_name["pub"].origin is ScopeOrigin.PUBLIC_HEADER
    assert by_name["dep"].origin is ScopeOrigin.PUBLIC_HEADER, (
        "a header reached transitively under an explicit -I root must "
        "still promote to PUBLIC_HEADER (the original defect-4/5 fix)"
    )


def test_compiler_option_include_dir_promotes_transitively_reached_header(
    tmp_path: Path,
) -> None:
    """CodeRabbit review, fresh evidence: an include directory supplied via
    the repeatable ``--compiler-option -I<dir>``/``-isystem <dir>`` flag is
    exactly as explicit as a plain ``-I`` -- the L2 seed suppression already
    trusts it that way (see the "as explicit as -I" comment in
    ``perform_elf_dump``) -- but it was never folded into
    ``public_include_search_dirs``, so a header reached ONLY through such a
    directory stayed ``PRIVATE_HEADER`` even though the caller had named
    that directory explicitly, just via ``--compiler-option`` rather than a
    bare ``-I``. Exercised end to end through the real ``abicheck dump`` CLI
    (the fix lives in ``cli_dump_helpers.py``, one layer above
    ``dumper.dump()``, so a direct ``dump()`` call cannot reproduce it)."""
    _require_tools()
    include_dir = tmp_path / "include"
    detail_dir = include_dir / "detail"
    detail_dir.mkdir(parents=True)
    (detail_dir / "impl.h").write_text(
        "#pragma once\nvoid dep(void);\n", encoding="utf-8"
    )
    (include_dir / "api.h").write_text(
        '#pragma once\n#include "detail/impl.h"\nvoid pub(void);\n',
        encoding="utf-8",
    )
    src = tmp_path / "lib.cpp"
    src.write_text(
        '#include "api.h"\nvoid pub(void) {}\nvoid dep(void) {}\n', encoding="utf-8"
    )
    so = tmp_path / "lib.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-g", f"-I{include_dir}", "-o", str(so), str(src)],
        check=True,
        capture_output=True,
    )

    from click.testing import CliRunner

    from abicheck.cli import main

    out = tmp_path / "out.json"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "dump",
            str(so),
            "-H",
            str(include_dir / "api.h"),
            "--ast-frontend",
            "clang",
            # No plain -I at all -- the only include search path is given
            # via --compiler-option, which must still count as explicit.
            "--compiler-option",
            f"-I{include_dir}",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    from abicheck.serialization import load_snapshot_document

    snap = load_snapshot_document(out)
    by_name = {f["name"]: f for f in snap["functions"]}
    assert by_name["pub"]["origin"] == "public_header"
    assert by_name["dep"]["origin"] == "public_header", (
        "a header reached transitively under a --compiler-option -I root "
        "must promote to PUBLIC_HEADER the same as a plain -I root"
    )

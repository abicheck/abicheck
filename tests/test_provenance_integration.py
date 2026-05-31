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

"""End-to-end provenance tagging through the real dumper (castxml + gcc).

These exercise the full Parse → Snapshot path: castxml/DWARF record a
``source_location`` per declaration, which ``apply_provenance`` turns into a
``source_header`` + ``origin`` against the supplied public-header set. They
require castxml and gcc/g++ and are therefore marked ``integration`` (the
suite auto-skips them when those tools are absent — see ``conftest.py``).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.model import ScopeOrigin

# gcc on macOS emits Mach-O and on Windows MinGW emits PE; the origin logic is
# platform-independent, but this fixture builds an ELF .so, so pin to Linux.
pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="provenance integration fixture builds an ELF .so (Linux gcc)",
)


def _compile_so(tmp: Path) -> Path:
    """Build a tiny .so whose public API pulls a type in from a private header."""
    (tmp / "impl.h").write_text(
        "#ifndef IMPL_H\n#define IMPL_H\nstruct Impl { int a; int b; };\n#endif\n"
    )
    (tmp / "api.h").write_text(
        "#ifndef API_H\n#define API_H\n"
        '#include "impl.h"\n'
        "int public_fn(struct Impl *p);\n"
        "#endif\n"
    )
    src = '#include "api.h"\nint public_fn(struct Impl *p){return p?p->a:0;}\n'
    (tmp / "api.c").write_text(src)
    so = tmp / "libapi.so"
    res = subprocess.run(
        [
            "gcc",
            "-g",
            "-I",
            str(tmp),
            "-shared",
            "-fPIC",
            "-o",
            str(so),
            str(tmp / "api.c"),
        ],
        capture_output=True,
    )
    if res.returncode != 0:
        pytest.skip(f"gcc failed: {res.stderr.decode()[:200]}")
    return so


@pytest.mark.integration
def test_dump_tags_public_and_private_origin(tmp_path: Path) -> None:
    from abicheck.dumper import dump

    so = _compile_so(tmp_path)
    pub = tmp_path / "api.h"

    snap = dump(so, headers=[pub], compiler="cc", lang="C", public_headers=[pub])

    fns = {f.name: f for f in snap.functions}
    assert "public_fn" in fns, "castxml/DWARF did not surface the public function"
    pf = fns["public_fn"]
    assert pf.source_header is not None and pf.source_header.endswith("api.h")
    assert pf.origin is ScopeOrigin.PUBLIC_HEADER

    # The struct pulled in from the (non-public) impl.h must be PRIVATE_HEADER.
    impl_types = [t for t in snap.types if (t.source_header or "").endswith("impl.h")]
    assert impl_types, "Impl type not captured from the private header"
    assert all(t.origin is ScopeOrigin.PRIVATE_HEADER for t in impl_types)


@pytest.mark.integration
def test_dump_without_public_set_leaves_origin_unknown(tmp_path: Path) -> None:
    # D4: omitting --public-header keeps every origin UNKNOWN, but source_header
    # is still populated descriptively from the parsed source location.
    from abicheck.dumper import dump

    so = _compile_so(tmp_path)
    pub = tmp_path / "api.h"

    snap = dump(so, headers=[pub], compiler="cc", lang="C")  # no public set

    pf = {f.name: f for f in snap.functions}.get("public_fn")
    assert pf is not None
    assert pf.origin is ScopeOrigin.UNKNOWN
    assert pf.source_header is not None and pf.source_header.endswith("api.h")

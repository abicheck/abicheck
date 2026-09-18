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

"""The reported defect, reproduced end to end against a real binary.

``real/include/pub.h`` declares an exported ``foo``; ``link/include`` is a
symlink to ``real/include``. castxml records the *canonical* source
location, so before the alias fix ``-H link/include`` classified ``foo``
non-public and the public-surface reconciliation reported a false
``exported_not_public``, while ``-H real/include`` was clean.

The assertion is an *equivalence*, not a fixed expectation: both spellings
of one tree must produce the same origins and the same findings. That is
the property the fix claims, and it cannot be satisfied by hard-coding
either spelling's answer.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.model import ScopeOrigin

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="fixture builds an ELF .so with gcc and needs POSIX symlinks",
)


def _fixture(tmp: Path) -> tuple[Path, Path, Path]:
    """Build ``real/include/pub.h`` + ``libpub.so``, and ``link`` -> ``real``.

    Returns ``(so, real_include, link_include)``.
    """
    real_inc = tmp / "real" / "include"
    real_inc.mkdir(parents=True)
    (real_inc / "pub.h").write_text(
        "#ifndef PUB_H\n#define PUB_H\nint foo(int x);\n#endif\n"
    )
    src = tmp / "pub.c"
    src.write_text('#include "pub.h"\nint foo(int x){return x + 1;}\n')
    so = tmp / "libpub.so"
    res = subprocess.run(
        ["gcc", "-g", "-I", str(real_inc), "-shared", "-fPIC", "-o", str(so), str(src)],
        capture_output=True,
    )
    if res.returncode != 0:
        pytest.skip(f"gcc failed: {res.stderr.decode()[:200]}")
    (tmp / "link").symlink_to(tmp / "real", target_is_directory=True)
    return so, real_inc, tmp / "link" / "include"


@pytest.mark.integration
def test_symlinked_header_root_matches_canonical_root(tmp_path: Path) -> None:
    from abicheck.dumper import dump

    so, real_inc, link_inc = _fixture(tmp_path)

    def origins(include_dir: Path) -> dict[str, ScopeOrigin]:
        snap = dump(
            so,
            headers=[include_dir / "pub.h"],
            compiler="cc",
            lang="C",
            public_header_dirs=[include_dir],
        )
        return {f.name: f.origin for f in snap.functions}

    via_real = origins(real_inc)
    via_link = origins(link_inc)

    assert via_real.get("foo") is ScopeOrigin.PUBLIC_HEADER, (
        "canonical spelling regressed -- the control arm of this comparison"
    )
    assert via_link.get("foo") is ScopeOrigin.PUBLIC_HEADER, (
        "the reported defect: a symlinked -H root left the declaration non-public"
    )
    assert via_link == via_real, "the two spellings of one tree disagreed"


@pytest.mark.integration
def test_symlinked_header_root_emits_no_false_exported_not_public(
    tmp_path: Path,
) -> None:
    """The user-visible half: identical finding sets through a real
    comparison, with no ``exported_not_public`` about ``foo`` either way."""
    from abicheck.checker import compare
    from abicheck.dumper import dump

    so, real_inc, link_inc = _fixture(tmp_path)

    def findings(include_dir: Path) -> set[tuple[str, str]]:
        snap = dump(
            so,
            headers=[include_dir / "pub.h"],
            compiler="cc",
            lang="C",
            public_header_dirs=[include_dir],
        )
        result = compare(snap, snap)
        return {(c.kind.value, c.symbol) for c in result.changes}

    via_real = findings(real_inc)
    via_link = findings(link_inc)

    assert not [f for f in via_link if "exported_not_public" in f[0] and "foo" in f[1]]
    assert via_link == via_real

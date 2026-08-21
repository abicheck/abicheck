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

"""ADR-050 Phase 1 — ``dump()`` populating ``AbiSnapshot.contract``.

These run the **live** clang L2 header backend (no castxml requirement, so
they're not gated behind the ``integration`` marker's castxml-present skip
condition — see ``test_clang_header_backend_integration.py``'s module
docstring for the same rationale) over a real compiled ``.so`` and asserts
the ``compute_extraction_contract`` wiring in ``dumper.dump()``:

1. a header-based dump populates ``contract`` with both fingerprints and the
   expected profile/scope fields;
2. ``symbols_only``/``dwarf_only``/no-headers dumps leave ``contract`` as
   ``None`` — the case a naive "gate on headers being non-empty" design
   would get wrong, since ``dwarf_only=True`` still accepts a ``headers``
   argument but ignores it (``from_headers`` stays ``False``);
3. two independent dumps of the same content are deterministic, and feeding
   the resulting pair through ``checker.compare`` does not spuriously raise
   — the regression gate for "the wiring works but the gate now fires on
   every routine compare."
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import Verdict, compare
from abicheck.dumper import dump
from abicheck.errors import ProfileMismatchError

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="clang L2 backend integration test is ELF/Linux-scoped",
)

_HEADER = """
#pragma once
#define FEATURE_X 1

struct Point { int x; int y; };

int add(int a, int b);
void scale(struct Point *p, double factor);
"""

_SOURCE = """
#include "api.h"
int add(int a, int b) { return a + b; }
void scale(struct Point *p, double factor) { p->x = (int)(p->x * factor); }
"""


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


@pytest.fixture
def built_lib(tmp_path: Path) -> tuple[Path, Path]:
    """Build a tiny ELF .so + its public header, returning (so_path, header)."""
    if not (_have("clang") and _have("gcc")):
        pytest.skip(
            "clang and gcc are required for the contract-wiring integration test"
        )
    header = tmp_path / "api.h"
    header.write_text(_HEADER)
    src = tmp_path / "api.c"
    src.write_text(_SOURCE)
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )
    return so, header


def test_header_dump_populates_contract(built_lib: tuple[Path, Path]) -> None:
    so, header = built_lib
    snap = dump(so, [header], compiler="cc", header_backend="clang")

    assert snap.contract is not None
    assert snap.contract.profile_fingerprint.startswith("sha256:")
    assert snap.contract.scope_fingerprint.startswith("sha256:")
    assert snap.contract.profile_fields["compiler_family"] == "clang"
    assert snap.contract.profile_fields["abi_dialect"] in ("gnu", "msvc")
    # A single declared header's own filename is not load-bearing profile
    # identity either (Codex review, PR #624 follow-up) -- see
    # test_comparability.py's declared_header_order tests for the
    # multi-header case that still tracks real ordering.
    assert snap.contract.profile_fields["header_sequence"] == '["<single-header>"]'
    # A single declared header's own filename is not load-bearing scope
    # identity (Codex review, PR #624 follow-up) -- see
    # test_comparability.py's test_2b for the multi-header case that is.
    assert snap.contract.scope_fields["headers"] == '["<single-header>"]'


def test_symbols_only_dump_leaves_contract_none(built_lib: tuple[Path, Path]) -> None:
    so, _header = built_lib
    snap = dump(so, [], compiler="cc", symbols_only=True)

    assert snap.from_headers is False
    assert snap.contract is None


def test_no_headers_dump_leaves_contract_none(built_lib: tuple[Path, Path]) -> None:
    so, _header = built_lib
    snap = dump(so, [], compiler="cc")

    assert snap.from_headers is False
    assert snap.contract is None


def test_dwarf_only_with_headers_supplied_leaves_contract_none(tmp_path: Path) -> None:
    """Pins the fix for the naive "gate on non-empty ``headers``" bug.

    ``dwarf_only=True`` still accepts a ``headers`` argument on the call
    signature but forces a DWARF-derived snapshot and ignores it (warns
    "ignoring provided headers") — ``from_headers`` stays ``False``, and the
    contract must therefore stay ``None`` too, even though ``headers`` was
    non-empty here.

    Needs a real DWARF-carrying ``.so`` (``-g``): without debug info,
    ``dwarf_only`` can't actually engage and ``dump()`` falls back to the
    header path instead (a different, also-tested code path) — see
    ``dumper.py``'s ADR-003 fallback chain.
    """
    if not (_have("clang") and _have("gcc")):
        pytest.skip(
            "clang and gcc are required for the contract-wiring integration test"
        )
    header = tmp_path / "api.h"
    header.write_text(_HEADER)
    src = tmp_path / "api.c"
    src.write_text(_SOURCE)
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )

    with pytest.warns(UserWarning, match="ignoring provided headers"):
        snap = dump(so, [header], compiler="cc", dwarf_only=True)

    assert snap.from_headers is False
    assert snap.contract is None


def test_explicit_language_standard_flows_into_profile_fingerprint(
    built_lib: tuple[Path, Path],
) -> None:
    """Codex review, PR #624 follow-up: two dumps differing only by an
    explicit -std= must not share a profile_fingerprint — the extraction
    context genuinely differs (e.g. __cplusplus-gated declarations)."""
    so, header = built_lib
    snap_17 = dump(
        so, [header], compiler="cc", header_backend="clang", gcc_options="-std=gnu11"
    )
    snap_99 = dump(
        so, [header], compiler="cc", header_backend="clang", gcc_options="-std=gnu99"
    )

    assert snap_17.contract is not None
    assert snap_99.contract is not None
    assert snap_17.contract.profile_fields["language_standard"] == "gnu11"
    assert snap_99.contract.profile_fields["language_standard"] == "gnu99"
    assert snap_17.contract.profile_fingerprint != snap_99.contract.profile_fingerprint
    # Scope is untouched by a profile-only difference.
    assert snap_17.contract.scope_fingerprint == snap_99.contract.scope_fingerprint


def test_lang_mode_alone_flows_into_profile_fingerprint(
    built_lib: tuple[Path, Path],
) -> None:
    """Codex review, PR #624 follow-up: the same clang executable, no
    explicit -std=, differing only by --lang c vs --lang c++, must not
    share a profile_fingerprint — the actual frontend commands parse
    different languages (-x c vs. C++ default) regardless.

    abicheck-internal-bugs finding 2 follow-up: with no explicit -std=,
    ``language_standard`` now also carries the resolved compiler's own
    *probed* default standard (``dumper_toolchain._probe_default_language_
    standard``), not just the bare lang mode — so the exact suffix is
    host-compiler-version-dependent and only the ``"c:"``/``"c++:"`` prefix
    is pinned here; the fingerprint-distinctness assertion (this test's
    actual point) is unaffected either way."""
    so, header = built_lib
    snap_c = dump(so, [header], compiler="cc", header_backend="clang", lang="c")
    snap_cpp = dump(so, [header], compiler="cc", header_backend="clang", lang="c++")

    assert snap_c.contract is not None
    assert snap_cpp.contract is not None
    assert snap_c.contract.profile_fields["language_standard"].startswith("c:")
    assert snap_cpp.contract.profile_fields["language_standard"].startswith("c++:")
    assert snap_c.contract.profile_fingerprint != snap_cpp.contract.profile_fingerprint


def test_forced_include_resolved_via_extra_includes_is_content_hashed(
    tmp_path: Path,
) -> None:
    """A ``-include`` operand only reachable through the ``-I`` dir passed
    as ``extra_includes`` (not CWD) resolves to a real content hash rather
    than falling back to opaque string hashing."""
    if not (_have("clang") and _have("gcc")):
        pytest.skip(
            "clang and gcc are required for the contract-wiring integration test"
        )
    inc_dir = tmp_path / "inc"
    inc_dir.mkdir()
    (inc_dir / "force.h").write_text("#define FORCED_MACRO 1\n")
    header = tmp_path / "api.h"
    header.write_text(_HEADER)
    src = tmp_path / "api.c"
    src.write_text(_SOURCE)
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )

    snap = dump(
        so,
        [header],
        [inc_dir],
        compiler="cc",
        header_backend="clang",
        gcc_options="-include force.h",
    )

    assert snap.contract is not None
    pass_through = snap.contract.profile_fields["pass_through_flags"]
    assert '"str:-include"' in pass_through
    assert '"str:force.h"' not in pass_through
    assert '"path:' in pass_through


def test_unresolvable_forced_include_does_not_crash_dump(tmp_path: Path) -> None:
    """Regression pin for the reviewer-flagged crash: a forced-include
    operand the compiler resolves through search paths abicheck doesn't
    model (not CWD, not the known extra_includes) must degrade the profile
    fingerprint to an opaque string hash, never raise out of dump()."""
    if not (_have("clang") and _have("gcc")):
        pytest.skip(
            "clang and gcc are required for the contract-wiring integration test"
        )
    inc_dir = tmp_path / "inc"
    inc_dir.mkdir()
    (inc_dir / "force.h").write_text("#define FORCED_MACRO 1\n")
    header = tmp_path / "api.h"
    header.write_text(_HEADER)
    src = tmp_path / "api.c"
    src.write_text(_SOURCE)
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )

    # -I passed as raw gcc_options text (not the dedicated extra_includes
    # param) -- the compiler resolves force.h fine, but resolve_pass_through_paths
    # only consults extra_includes + CWD, so this operand is unresolvable by
    # abicheck's own search. Must fall back to str, not raise.
    snap = dump(
        so,
        [header],
        compiler="cc",
        header_backend="clang",
        gcc_options=f"-I{inc_dir} -include force.h",
    )

    assert snap.contract is not None
    assert '"str:force.h"' in snap.contract.profile_fields["pass_through_flags"]


def test_contract_is_deterministic_and_gate_does_not_spuriously_raise(
    built_lib: tuple[Path, Path],
) -> None:
    so, header = built_lib

    snap_a = dump(so, [header], version="1.0", compiler="cc", header_backend="clang")
    snap_b = dump(so, [header], version="1.0", compiler="cc", header_backend="clang")

    assert snap_a.contract is not None
    assert snap_b.contract is not None
    assert snap_a.contract.profile_fingerprint == snap_b.contract.profile_fingerprint
    assert snap_a.contract.scope_fingerprint == snap_b.contract.scope_fingerprint

    result = compare(snap_a, snap_b)
    assert result.verdict == Verdict.NO_CHANGE


def test_renamed_single_header_does_not_spuriously_block_compare(
    tmp_path: Path,
) -> None:
    """Regression pin for the CI-red incident this wiring caused once live
    on real dumps at scale (Codex review, PR #624 follow-up): a project
    with exactly one declared header per side that gets renamed between
    versions (v1.h -> v2.h, or any other rename -- a common, legitimate
    practice, e.g. examples/case31_enum_rename's own fixture layout) must
    not raise ScopeMismatchError/ProfileMismatchError -- the comparability
    gate has nothing to disambiguate a lone header's name against, and the
    real API surface is still verified by the ordinary diff engine below."""
    if not (_have("clang") and _have("gcc")):
        pytest.skip(
            "clang and gcc are required for the contract-wiring integration test"
        )
    old_header = tmp_path / "v1.h"
    old_header.write_text(
        "typedef enum { LOG_ERR = 1 } log_level_t;\n"
        "void set_log_level(log_level_t level);\n"
    )
    new_header = tmp_path / "v2.h"
    new_header.write_text(
        "typedef enum { LOG_ERROR = 1 } log_level_t;\n"
        "void set_log_level(log_level_t level);\n"
    )
    old_src = tmp_path / "v1.c"
    old_src.write_text('#include "v1.h"\nvoid set_log_level(log_level_t level) {}\n')
    new_src = tmp_path / "v2.c"
    new_src.write_text('#include "v2.h"\nvoid set_log_level(log_level_t level) {}\n')
    old_so = tmp_path / "libv1.so"
    new_so = tmp_path / "libv2.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(old_so), str(old_src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(new_so), str(new_src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )

    snap_old = dump(old_so, [old_header], version="v1", header_backend="clang")
    snap_new = dump(new_so, [new_header], version="v2", header_backend="clang")

    assert snap_old.contract is not None
    assert snap_new.contract is not None
    assert snap_old.contract.scope_fingerprint == snap_new.contract.scope_fingerprint
    assert (
        snap_old.contract.profile_fingerprint == snap_new.contract.profile_fingerprint
    )

    result = compare(snap_old, snap_new)
    names = {c.kind.value for c in result.changes}
    assert "enum_member_renamed" in names


def test_cpp20_heuristic_forced_standard_flows_into_profile_fingerprint(
    tmp_path: Path,
) -> None:
    """P0 evidence-provider audit: two dumps of the SAME binary, no explicit
    -std=, differing only by whether their headers contain a genuine C++20
    requires-clause (which forces gnu++20 on only one side via
    dumper_ast_config_cpp20's heuristic) must not silently share a
    profile_fingerprint -- before ast_resolved_standard fed
    language_standard_field, this exact divergence was invisible to
    check_contracts_comparable, so a caller could unknowingly compare a
    C++20-parsed surface against a C++17-or-earlier one."""
    if not (_have("clang") and _have("gcc")):
        pytest.skip(
            "clang and gcc are required for the contract-wiring integration test"
        )
    header_plain = tmp_path / "plain.h"
    header_plain.write_text(
        "#pragma once\n"
        "struct Point { int x; int y; };\n"
        'extern "C" int add(int a, int b);\n'
    )
    header_cxx20 = tmp_path / "cxx20.h"
    header_cxx20.write_text(
        "#pragma once\n"
        "struct Point { int x; int y; };\n"
        'extern "C" int add(int a, int b);\n'
        "template<typename T>\n"
        "requires (sizeof(T) > 0)\n"
        "void touch(T value) {}\n"
    )
    src = tmp_path / "api.cpp"
    src.write_text(
        '#include "plain.h"\nextern "C" int add(int a, int b) { return a + b; }\n'
    )
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )

    snap_plain = dump(
        so, [header_plain], version="1.0", compiler="c++", header_backend="clang"
    )
    snap_cxx20 = dump(
        so, [header_cxx20], version="2.0", compiler="c++", header_backend="clang"
    )

    assert snap_plain.contract is not None
    assert snap_cxx20.contract is not None
    assert snap_plain.ast_resolved_standard != snap_cxx20.ast_resolved_standard
    assert snap_cxx20.ast_resolved_standard == "gnu++20"
    assert (
        snap_plain.contract.profile_fingerprint
        != snap_cxx20.contract.profile_fingerprint
    )

    with pytest.raises(ProfileMismatchError):
        compare(snap_plain, snap_cxx20)

    # ADR-050 D2's sanctioned escape hatch still lets a caller force the
    # comparison through, with the mismatch surfaced rather than hidden.
    result = compare(snap_plain, snap_cxx20, diagnostic_comparison=True)
    assert result.assurance == "none"
    assert result.coverage_warnings
    assert any("profile_fingerprint" in w for w in result.coverage_warnings)


def _build_lib_in_dir(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a tiny ELF .so with its public header inside a subdirectory,
    returning (so_path, header_path, header_dir)."""
    include_dir = tmp_path / "include"
    include_dir.mkdir()
    header = include_dir / "api.h"
    header.write_text(_HEADER)
    src = tmp_path / "api.c"
    src.write_text(_SOURCE)
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{include_dir}"],
        check=True,
        capture_output=True,
    )
    return so, header, include_dir


def test_scope_header_dirs_matches_compare_style_public_header_dir_scope(
    tmp_path: Path,
) -> None:
    """Regression pin (G30 pilot validation): a snapshot ``dump``-produced from a
    bare ``-H <dir>`` directory argument (the CLI now folds that raw
    directory into ``scope_header_dirs``, ``cli_dump_helpers.
    perform_elf_dump``) must agree on ``scope_fingerprint`` with a live
    extraction of the identical header set where the directory feeds
    ``public_header_dirs`` directly (``compare``'s own ``--header`` handling,
    ``cli_resolve._resolve_compare_snapshots`` via ``split_public_header_
    inputs``) -- comparing the two must not raise ``ScopeMismatchError`` even
    though nothing about the declared surface differs."""
    if not (_have("clang") and _have("gcc")):
        pytest.skip(
            "clang and gcc are required for the contract-wiring integration test"
        )
    so, header, include_dir = _build_lib_in_dir(tmp_path)

    # Simulates `dump --header <dir>` (no --public-header-dir given): the
    # CLI folds the raw directory argument into scope_header_dirs only.
    dump_style = dump(
        so,
        [header],
        compiler="cc",
        header_backend="clang",
        scope_header_dirs=[include_dir],
    )
    # Simulates `compare --header <dir>` (compare has no separate opt-in
    # flag, so its own --header directly feeds public_header_dirs).
    compare_style = dump(
        so,
        [header],
        compiler="cc",
        header_backend="clang",
        public_header_dirs=[include_dir],
    )

    assert dump_style.contract is not None
    assert compare_style.contract is not None
    assert (
        dump_style.contract.scope_fields["public_header_dirs"]
        == compare_style.contract.scope_fields["public_header_dirs"]
    )
    assert (
        dump_style.contract.scope_fingerprint
        == compare_style.contract.scope_fingerprint
    )

    result = compare(dump_style, compare_style)
    assert result.verdict == Verdict.NO_CHANGE


def test_scope_header_dirs_does_not_enable_provenance_tagging(
    tmp_path: Path,
) -> None:
    """``scope_header_dirs`` feeds only the extraction contract's scope
    fingerprint (ADR-050 D1) -- it must never silently opt a plain ``dump
    --header <dir>`` invocation into declaration-provenance tagging (ADR-015
    stays opt-in via the separate ``--public-header``/``--public-header-dir``
    flags, unchanged)."""
    if not (_have("clang") and _have("gcc")):
        pytest.skip(
            "clang and gcc are required for the contract-wiring integration test"
        )
    so, header, include_dir = _build_lib_in_dir(tmp_path)

    snap = dump(
        so,
        [header],
        compiler="cc",
        header_backend="clang",
        scope_header_dirs=[include_dir],
    )

    from abicheck.model import ScopeOrigin

    assert snap.functions
    assert all(fn.origin == ScopeOrigin.UNKNOWN for fn in snap.functions)

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

"""Generalized parity guard: ``abicheck dump``'s CLI path and the typed
``DumpRequest``/``run_dump_request`` API path must resolve the *same* real
L3 build evidence into the *same* L2 compile context.

Why this exists (root cause of the "dump-vs-scan compile-context
divergence" bug class, PR #810): ``abicheck dump`` is, structurally, **two
independently-maintained implementations**, not one function called from
two front ends:

* the CLI path (``cli.py``'s ``dump_cmd`` -> ``cli_dump_helpers.
  perform_elf_dump``/``handle_non_elf_dump``) calls ``dumper.dump()``
  directly, seeding its L2 include/compile context via
  ``buildsource.l2_seed.seed_includes_and_fold_compile_context``;
* the typed Tier-2 API (``DumpRequest``/``run_dump_request``, used by
  ``compare``'s implicit-dump operand and by ``scan``'s candidate
  resolution) resolves through
  ``service_input_resolution._resolve_side_snapshot_impl`` ->
  ``service.resolve_input``, seeding its own L2 context via
  ``service_input_resolution._seeded_includes_and_compile_context`` --
  which calls the *same* ``seed_includes_and_fold_compile_context``
  primitive, but through different surrounding wiring (its own debug-
  artifact resolution, its own argument defaults, its own post-processing
  hooks).

AGENTS.md's own "Known gaps" section documents, across dozens of numbered
findings, that this class of split has repeatedly let a fix land on one of
these two paths without the other -- which is exactly how the bug report
this test file accompanies was possible: the shared fold was wired into
the CLI path in one PR, and the two paths drifted for a real, if narrow,
window before that. `AGENTS.md`'s "PR C (typed dump/scan convergence)"
entry records this structural split as still open today (``dump_cmd``
still does not build a ``DumpRequest`` at all) -- so a *unification* fix
is not available yet, but a *regression guard on the observable symptom*
is: this module asserts, for a small matrix of real build-evidence shapes
(not just the one exact shape the bug report happened to use), that both
paths resolve to the same ``ast_resolved_standard``/``ast_compile_args``/
``parsed_with_build_context`` -- the exact fields whose disagreement is
what ``profile_fingerprint`` comparability checks on, and what made
`scan --against` refuse a real, unchanged comparison as ``NOT_COMPARABLE``.

Two lenses, deliberately: ``test_dump_cli_and_typed_api_agree_on_resolved_
compile_context`` compares the *effective* compiler invocation through
``split_compile_args``' semantics-preserving normalization ("did both paths
reach the same compile"), while ``test_dump_cli_and_typed_api_agree_on_
extraction_contract`` compares the extraction contract's profile fields
*as recorded* ("are the two snapshots comparable to each other"). The
second is strictly stricter, and it is where the currently-open
legacy-compile-DB-match overlap shows up -- see
``_CONTRACT_KNOWN_DIVERGENT_FIELDS``' own comment.

A future change to either path's own L2-seeding wiring (not just the
shared ``seed_includes_and_fold_compile_context`` primitive itself) that
reintroduces a disagreement should fail here, on at least one of the
parametrized shapes, well before it reaches a downstream comparability
check -- rather than needing its own bug report against a specific real
build shape the way #810 did.

Marked ``integration`` (real g++ + clang toolchain), like its sibling
``test_dump_scan_l3_comparability.py``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Non-test-prefixed sibling helper (mirrors `_strict_process.py`/
# `_workflow_exec.py` -- see `tests/CLAUDE.md`'s "Helpers" section),
# imported plainly rather than relatively since `tests/` carries no
# `__init__.py` (same pattern every other such helper import in this
# suite uses).
from _dump_compile_args_normalize import split_compile_args
from click.testing import CliRunner

from abicheck.api_types import DumpRequest, InputSpec
from abicheck.cli import main
from abicheck.compile_context import CompileContext
from abicheck.service import run_dump_request

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


def _build_library(
    tmp_path: Path,
    *,
    std: str = "c++17",
    macros: tuple[str, ...] = (),
    extra_include_dir: str | None = None,
    duplicate_owned_include_dirs: bool = False,
) -> tuple[Path, Path, Path]:
    """Compile a small, real library whose header depends on the given
    ``-std=``/macros, plus a matching ``compile_commands.json`` -- varying
    the build-evidence shape across the parametrized cases below, not just
    the one exact shape #810's own repro used.

    ``duplicate_owned_include_dirs`` reproduces the exact real-world shape
    reported against a Bazel ``cc_library`` with an ``includes = [...]``
    attribute (``napetrov/abicheck-bazel-lab``'s ``UPSTREAM_TO_ABICHECK.md``,
    2026-08-21 entry, ``//:math``): Bazel's compile action carries *two*
    distinct ``-I`` search directories that are both ancestors of the
    *same* public header -- one from the package's own ``includes``
    attribute (here, ``<root>/include``), one from Bazel's own
    always-present package/workspace-root search path (here, ``<root>``
    itself). Neither is redundant to drop and both are real compiler argv,
    so ``declared_includes`` legitimately gets two "owned" slots for one
    physical header, tokenized as two different header-relative-to-dir
    spellings (``pkg/widget.h`` under the root dir, ``widget.h`` under the
    package dir). This is a distinct shape from ``extra-include-dir``
    above (an unrelated *second* header under its own, non-overlapping
    directory) -- here both dirs own the exact same header, which is what
    exercises the ``_slot_token_for_ancestor`` dedup/ordering machinery on
    a genuine multi-owner case rather than a multi-header one."""
    include_dirs = [tmp_path]
    dep_header_snippet = ""
    dep_field = ""
    if extra_include_dir is not None:
        dep_dir = tmp_path / extra_include_dir
        dep_dir.mkdir(parents=True, exist_ok=True)
        (dep_dir / "dep.h").write_text(
            "#pragma once\nstruct Dep { int tag; };\n", encoding="utf-8"
        )
        include_dirs.append(dep_dir)
        dep_header_snippet = '#include "dep.h"\n'
        dep_field = "    Dep dep;\n"

    macro_guard = "\n".join(
        f'#ifndef {m.split("=")[0]}\n#error "missing {m}"\n#endif' for m in macros
    )

    if duplicate_owned_include_dirs:
        header_dir = tmp_path / "include" / "pkg"
        header_dir.mkdir(parents=True, exist_ok=True)
        header = header_dir / "widget.h"
        header_include_spelling = "pkg/widget.h"
        # Both the package's own `include` dir (owns the header as
        # `pkg/widget.h`) and the workspace root itself (owns it as
        # `include/pkg/widget.h`) are real, simultaneous `-I` search
        # directories -- mirroring Bazel's `includes = ["include"]` plus
        # its own always-present package-root search path.
        include_dirs.append(tmp_path / "include")
        include_dirs.append(tmp_path)
    else:
        header = tmp_path / "widget.h"
        header_include_spelling = "widget.h"

    header.write_text(
        "#pragma once\n"
        f"{dep_header_snippet}"
        "#if __cplusplus < " + ("202002L" if std == "c++20" else "201703L") + "\n"
        '#error "needs a newer standard"\n'
        "#endif\n"
        f"{macro_guard}\n"
        "struct Widget {\n"
        "    int x;\n"
        "    int y;\n"
        f"{dep_field}"
        "    int sum() const { return x + y; }\n"
        "};\n",
        encoding="utf-8",
    )
    src = tmp_path / "widget.cpp"
    src.write_text(
        f'#include "{header_include_spelling}"\n'
        "int compute(const Widget& w) { return w.sum(); }\n",
        encoding="utf-8",
    )
    so_path = tmp_path / "libwidget.so"
    include_flags = [f"-I{d}" for d in include_dirs[1:]]
    macro_flags = [f"-D{m}" for m in macros]
    subprocess.run(
        [
            "g++",
            f"-std={std}",
            *macro_flags,
            *include_flags,
            "-shared",
            "-fPIC",
            "-o",
            str(so_path),
            str(src),
        ],
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
                        f"-std={std}",
                        *macro_flags,
                        *include_flags,
                        "-shared",
                        "-fPIC",
                        "-c",
                        str(src),
                        "-o",
                        "widget.o",
                    ],
                    "file": str(src),
                }
            ]
        ),
        encoding="utf-8",
    )
    return so_path, header, compile_db


def _dump_via_cli(
    so_path: Path, header: Path, tmp_path: Path, compile_db: Path
) -> dict:
    baseline = tmp_path / "baseline_cli.json"
    result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so_path),
            "-H",
            str(header),
            "--sources",
            str(tmp_path),
            "--build-info",
            str(compile_db),
            "--depth",
            "source",
            "--ast-frontend",
            "clang",
            "-o",
            str(baseline),
        ],
    )
    assert result.exit_code == 0, result.output
    from abicheck.serialization import load_snapshot_document

    return load_snapshot_document(baseline)


def _dump_via_typed_api(
    so_path: Path, header: Path, tmp_path: Path, compile_db: Path
) -> dict:
    request = DumpRequest(
        input=InputSpec(
            path=so_path,
            headers=(header,),
            sources=tmp_path,
            build_info=compile_db,
            compile=CompileContext(frontend="clang"),
        ),
        depth="source",
    )
    snap = run_dump_request(request)
    return {
        "ast_resolved_standard": snap.ast_resolved_standard,
        "ast_compile_args": snap.ast_compile_args,
        "parsed_with_build_context": snap.parsed_with_build_context,
    }


def _contract_profile_fields_via_typed_api(
    so_path: Path, header: Path, tmp_path: Path, compile_db: Path
) -> dict:
    """The typed path's own ``ExtractionContract`` profile fields.

    Deliberately a second helper rather than a widening of
    :func:`_dump_via_typed_api` above: that one returns three hand-picked
    scalar fields and its callers compare them through
    ``split_compile_args``' semantics-preserving normalization, which is
    exactly the wrong lens for this question -- ``profile_fingerprint`` is
    computed from the *raw* recorded fields, so a divergence normalization
    hides is still a real comparability failure downstream.

    ``include_dependencies=False`` matches the ``dump`` CLI's own default
    (``--include-system-declarations`` is opt-in), so the two paths are
    compared under the same dependency scope rather than at two different
    ones.
    """
    from abicheck.serialization import snapshot_to_dict

    request = DumpRequest(
        input=InputSpec(
            path=so_path,
            headers=(header,),
            sources=tmp_path,
            build_info=compile_db,
            include_dependencies=False,
            compile=CompileContext(frontend="clang"),
        ),
        frontend="clang",
        depth="source",
    )
    snap = run_dump_request(request)
    contract = snapshot_to_dict(snap).get("contract") or {}
    fields: dict = dict(contract.get("profile_fields") or {})
    fields["__scope_fingerprint__"] = contract.get("scope_fingerprint")
    return fields


_BUILD_SHAPES: dict[str, dict[str, object]] = {
    "plain-c++17": {"std": "c++17"},
    "c++20-with-macro": {"std": "c++20", "macros": ("FOO=1",)},
    "extra-include-dir": {"std": "c++17", "extra_include_dir": "dep"},
    # Real-world Bazel `cc_library(includes = [...])` shape -- see
    # `_build_library`'s own docstring paragraph on
    # `duplicate_owned_include_dirs` for the exact upstream report this
    # reproduces (`napetrov/abicheck-bazel-lab`, `//:math`, 2026-08-21).
    "duplicate-owned-include-dirs": {
        "std": "c++17",
        "duplicate_owned_include_dirs": True,
    },
}


# Comparing `ast_compile_args` for real semantic equivalence (not literal
# list/set equality) needs to know which flags are last-wins,
# order-sensitive, or genuinely inert under reordering/duplication -- see
# `_dump_compile_args_normalize.py`'s own module docstring for the full
# reasoning (two Codex review rounds: a plain `set()` first missed
# include-search ordering, then still missed -D/-std last-wins
# conflicts). `test_dump_compile_args_normalize.py` pins that helper's
# normalization rules directly, with synthetic inputs, independent of any
# real compiler.


@pytest.mark.skipif(not (_HAVE_GXX and _HAVE_CLANG), reason=_SKIP_REASON)
@pytest.mark.parametrize("shape_name", sorted(_BUILD_SHAPES))
def test_dump_cli_and_typed_api_agree_on_resolved_compile_context(
    tmp_path: Path, shape_name: str
) -> None:
    """The invariant this whole file exists for: two independent code paths
    that both claim to fold the same real L3 evidence into an L2 compile
    context must actually agree, for more than one hand-picked build
    shape."""
    shape = _BUILD_SHAPES[shape_name]
    so_path, header, compile_db = _build_library(tmp_path, **shape)  # type: ignore[arg-type]

    cli_snap = _dump_via_cli(so_path, header, tmp_path, compile_db)
    typed_snap = _dump_via_typed_api(so_path, header, tmp_path, compile_db)

    assert cli_snap["ast_resolved_standard"] == typed_snap["ast_resolved_standard"], (
        shape_name,
        cli_snap["ast_resolved_standard"],
        typed_snap["ast_resolved_standard"],
    )
    assert (
        cli_snap["parsed_with_build_context"] == typed_snap["parsed_with_build_context"]
    )
    # Both paths were given the identical real -std=/macros/-I evidence, so
    # every ABI-relevant flag reaching the typed path's parse must also
    # reach the CLI path's -- compared per `split_compile_args`'s own
    # semantics-preserving normalization, not literal set/list equality
    # (see that module's docstring for the full reasoning, including why
    # every order-sensitive/unrecognized token shares one unified stream
    # rather than several independently-compared buckets).
    cli_last_wins, cli_presence, cli_stream = split_compile_args(
        tuple(cli_snap["ast_compile_args"])
    )
    typed_last_wins, typed_presence, typed_stream = split_compile_args(
        tuple(typed_snap["ast_compile_args"])
    )
    assert cli_last_wins or cli_presence, "the CLI dump path resolved no compile flags"
    assert typed_last_wins or typed_presence, (
        "the typed-API dump path resolved no compile flags"
    )
    # Last-wins state (-D/-U/-std=/--target=): exact dict equality -- this
    # is the one that catches a *conflicting* value reached via a
    # different order on each path (e.g. one path's effective -DFOO ends
    # up "1", the other's "2"), which a plain set of tokens cannot
    # distinguish from an identical, merely-reordered pair.
    assert cli_last_wins == typed_last_wins, (
        shape_name,
        cli_last_wins,
        typed_last_wins,
    )
    # Presence-only flags (-fPIC and the like): genuinely order- and
    # repeat-count-insensitive, so a set comparison is the correct
    # semantics here, not merely a convenient one.
    missing_from_cli = typed_presence - cli_presence
    missing_from_typed = cli_presence - typed_presence
    assert not missing_from_cli, (
        f"{shape_name}: typed-API path resolved flag(s) {missing_from_cli} "
        "the CLI dump path did not"
    )
    assert not missing_from_typed, (
        f"{shape_name}: CLI dump path resolved flag(s) {missing_from_typed} "
        "the typed-API path did not"
    )
    # Everything else -- include-search/forced-include pairs (attached or
    # separate spelling, normalized identically) and any unrecognized
    # token -- shares one unified, order- and multiplicity-preserving
    # stream: see `split_compile_args`'s own docstring for why this must
    # be one sequence rather than several independently-compared ones.
    assert cli_stream == typed_stream, (shape_name, cli_stream, typed_stream)


# The *raw* extraction-contract counterpart of the test above, and the
# reason it is a separate test rather than three more assertions inside it:
# that test compares `ast_compile_args` through `split_compile_args`'
# semantics-preserving normalization (duplicates collapsed, last-wins
# resolved, presence flags set-compared), which is the right lens for "did
# both paths reach the same effective compiler invocation". It is the
# *wrong* lens for comparability. `ExtractionContract.profile_fingerprint`
# is a hash over the recorded profile fields as-recorded, so a difference
# normalization deliberately hides -- the same macro recorded twice, an
# include dir recorded through a different channel -- still makes two
# snapshots of the identical library, from the identical evidence,
# `NOT_COMPARABLE`.
#
# Measured, not assumed (2026-08-21, CLI cleanup phase two PR 3A
# investigation): two shapes diverged field-by-field against a real g++
# build + clang L2 parse, and both traced to ONE root cause -- the `dump`
# CLI ran the legacy `-p`/`--compile-db` auto-match
# (`cli_helpers_compare._resolve_build_context_flags`, folded into
# `effective_gcc_options`) *in addition to* the P0.3 L3->L2 fold, when both
# are fed by the same `--build-info` compile database, while every other
# resolver runs the fold alone:
#
#   * `macro_ops` -- the CLI recorded `[["D","FOO=1"],["D","FOO=1"]]` where
#     the typed path records one entry: the same `-D` arrived twice, once
#     derived by the fold and once by the legacy match.
#   * `include_sequence` -- the CLI recorded `[]` where the typed path
#     records one slot: the legacy match put `-I<dep>` into
#     `effective_gcc_options` *before* the L2 seed ran, and
#     `seed_l2_includes` then (correctly) declined to seed a directory
#     explicit context already supplies -- so the dir reached the parse via
#     `gcc_option_tokens` and never became a `declared_includes` slot,
#     which is the sole source `include_sequence` tokenizes.
#
# **Both are closed** (same session, PR 3A): the fold is now the sole source
# of compile-database-derived context whenever it resolves one for these
# headers, and the legacy match's own derived flags are unfolded rather than
# stacked on top of it -- see `cli_dump_helpers.perform_elf_dump`'s
# `legacy_build_context_flags` parameter and its own docstring paragraph.
# `--compile-db-filter` does not go inert with it (the concern the plan's PR
# 3A section recorded as the blocker): the filter reaches the shared fold
# too, since PR "3A slice 1" threaded `source_filter` through
# `seed_includes_and_fold_compile_context`/`resolve_header_compile_context`.
# The legacy match still runs -- and still applies -- whenever the fold does
# not (no `--build-info`, or a header no compile unit matches), so the only
# thing dropped is the overlap.
#
# The mapping stays (empty) rather than being deleted, because the mechanism
# it encodes is the right one for the *next* divergence found here: a listed
# (shape, field) pair has exactly two acceptable outcomes -- the exact known
# signature reproduces (expected `xfail`), or the test fails outright. "The
# gap closed" fails just as loudly as "a new field diverged", so a mapping
# must be updated deliberately rather than going quietly stale.
_CONTRACT_KNOWN_DIVERGENT_FIELDS: dict[str, str] = {}


def _assert_duplicate_owned_include_dirs_oracle(fields: dict) -> None:
    """Positive oracle for the ``"duplicate-owned-include-dirs"`` shape
    (Codex review, PR #825): the cross-path equality check in the test
    below only proves the CLI and typed-API paths agree with *each other*
    -- if a regression made every path uniformly drop one or both owned
    ``-I`` roots (e.g. a change to ``seed_includes_and_fold_compile_context``
    or ``_slot_token_for_ancestor`` that stops recognizing this shape at
    all), the snapshots would still be equal, and the equality check alone
    would stay green while this fixture silently stopped exercising the
    two-owner-directories behavior it exists to pin. Assert the actual
    expected content instead: two ordered ``hdrs:`` slots, one per owning
    ``-I`` directory, each with the distinct header-relative spelling
    ``_build_library``'s docstring documents for this shape."""
    raw = fields.get("include_sequence")
    assert raw is not None, "duplicate-owned-include-dirs: no include_sequence recorded"
    slots = json.loads(raw)
    assert len(slots) == 2, (
        "duplicate-owned-include-dirs: expected exactly two owned include-dir "
        f"slots, got {slots!r}"
    )
    assert slots[0].startswith("0:hdrs:") and slots[1].startswith("1:hdrs:"), slots
    assert '"pkg/widget.h"' in slots[0], slots
    assert '"include/pkg/widget.h"' in slots[1], slots


_SHAPE_ORACLES: dict[str, object] = {
    "duplicate-owned-include-dirs": _assert_duplicate_owned_include_dirs_oracle,
}


@pytest.mark.skipif(not (_HAVE_GXX and _HAVE_CLANG), reason=_SKIP_REASON)
@pytest.mark.parametrize("shape_name", sorted(_BUILD_SHAPES))
def test_dump_cli_and_typed_api_agree_on_extraction_contract(
    tmp_path: Path, shape_name: str
) -> None:
    """A ``dump``-CLI snapshot and a typed-API snapshot of the same library,
    from the same evidence, must record the same extraction contract --
    otherwise they are not comparable to each other, nor to a ``compare``
    implicit dump or a ``scan`` candidate."""
    shape = _BUILD_SHAPES[shape_name]
    so_path, header, compile_db = _build_library(tmp_path, **shape)  # type: ignore[arg-type]

    cli_contract = _dump_via_cli(so_path, header, tmp_path, compile_db)
    # `_dump_via_cli` returns the whole written snapshot dict; re-read the
    # contract block out of it rather than dumping a second time.
    cli_block = cli_contract.get("contract") or {}
    cli_fields: dict = dict(cli_block.get("profile_fields") or {})
    cli_fields["__scope_fingerprint__"] = cli_block.get("scope_fingerprint")
    typed_fields = _contract_profile_fields_via_typed_api(
        so_path, header, tmp_path, compile_db
    )

    assert cli_fields, f"{shape_name}: the CLI dump recorded no extraction contract"
    assert typed_fields, f"{shape_name}: the typed dump recorded no extraction contract"

    oracle = _SHAPE_ORACLES.get(shape_name)
    if oracle is not None:
        oracle(cli_fields)  # type: ignore[operator]

    differing = sorted(
        key
        for key in set(cli_fields) | set(typed_fields)
        if cli_fields.get(key) != typed_fields.get(key)
    )
    known = _CONTRACT_KNOWN_DIVERGENT_FIELDS.get(shape_name)
    if known is not None:
        if differing == [known]:
            pytest.xfail(
                f"{shape_name}: known-open legacy-compile-DB-match overlap on "
                f"{known!r} (see this module's own comment above this test)"
            )
        pytest.fail(
            f"{shape_name} is listed in _CONTRACT_KNOWN_DIVERGENT_FIELDS as "
            f"diverging on exactly {known!r}, but the differing fields were "
            f"{differing!r}. Either the underlying gap closed -- remove this "
            "shape from _CONTRACT_KNOWN_DIVERGENT_FIELDS and update the plan's "
            "PR 3A section and AGENTS.md's PR C entry -- or a different field "
            "started diverging and needs its own investigation. "
            f"cli={cli_fields!r} typed={typed_fields!r}"
        )

    assert not differing, (shape_name, differing, cli_fields, typed_fields)


def _dump_via_cli_to_file(
    so_path: Path, header: Path, tmp_path: Path, compile_db: Path, baseline: Path
) -> None:
    dump_result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so_path),
            "-H",
            str(header),
            "--sources",
            str(tmp_path),
            "--build-info",
            str(compile_db),
            "--depth",
            "source",
            "--ast-frontend",
            "clang",
            "-o",
            str(baseline),
        ],
    )
    assert dump_result.exit_code == 0, dump_result.output


# **Closed** (CLI cleanup phase two, PR 3A): the "extra -I<dependency-dir>"
# shape below used to reproduce `NOT_COMPARABLE` on `include_sequence`
# between `dump`'s baseline and `scan`'s candidate resolution -- verified
# both before (exit 6, `differing fields: include_sequence`) and after
# (exit 0) against a real `g++` build and a real clang L2 parse. The cause
# was not `scan`'s side at all: `dump`'s CLI ran the legacy
# `-p`/`--compile-db` auto-match alongside the P0.3 L3->L2 fold, so its own
# `-I<dep>` reached the parse as *explicit* context and never became a
# `declared_includes` slot, while every other resolver (`scan`'s candidate,
# `compare`'s implicit dump, the typed `DumpRequest` API) ran the fold alone
# and seeded the identical directory. Now that the fold is the sole source
# of compile-database-derived context when it resolves one, all four agree.
# See `_CONTRACT_KNOWN_DIVERGENT_FIELDS` above for the same closure from the
# contract-field side. The historical diagnosis is kept below because the
# *mechanism* it describes is still the one to check first if a new shape
# ever diverges here.
#
# Original note (the gap as it stood before the fix): the
# `dump` baseline's own `contract.profile_fields.include_sequence` reads
# `dump` baseline's own `contract.profile_fields.include_sequence` reads
# `"[]"` even though its `ast_compile_args` correctly carries `-I <dep-dir>`,
# because `dumper_contract._attach_extraction_contract` builds
# `declared_includes` (the source of `include_sequence`) **exclusively**
# from `extra_includes`, and this build shape routes the `-I` into
# `gcc_option_tokens` only, never into `extra_includes` -- while
# `scan_engine._build_new_snapshot`'s own candidate resolution (which calls
# `service.resolve_input` directly, bypassing the shared
# `resolve_side_snapshot` primitive the typed dump/compare API above already
# uses -- see this module's own docstring) seeds `eff_includes`/
# `declared_includes` for the identical directory instead. `compare`'s
# implicit-dump path does **not** reproduce this for the same inputs
# (verified directly), confirming the divergence is specifically between
# `dump`'s CLI path and `scan`'s own candidate-resolution path, not a
# `compare`-side issue. This is a live instance of the residual gap
# AGENTS.md's own entry already documents as open (the "channel disagreement"
# paragraphs under "The native ELF `abicheck dump` path never applies L3
# build context...") -- not a new bug class, but the first *reproduced*,
# non-Bazel-specific repro of it.
#
# Deliberately *not* a blanket `xfail(strict=True)` on the whole test
# (Codex review, second round): that would treat *any* failure for this
# shape as expected, including an unrelated regression -- compilation
# breaking, `dump` crashing, or `scan` failing for a completely different
# reason -- silently swallowing it as "known". Every setup step (the real
# `g++` compile, the real `dump` CLI invocation) stays a hard,
# unconditionally-checked requirement below, never inside any
# expected-failure scope. Only the exact, already-diagnosed signature
# (`NOT_COMPARABLE` naming `include_sequence`) is treated as expected via
# an explicit, conditional `pytest.xfail()` call made *after* confirming
# that signature -- so a different scan failure surfaces as a genuine,
# uncaught test failure.
#
# A quiet PASS for a listed shape is *not* an acceptable third outcome
# (Codex review, second round): if the diagnosed signature stops
# reproducing -- because the gap closed, or because it changed shape --
# this must still fail loudly, forcing `_SCAN_KNOWN_DIVERGENT_SHAPES` and
# AGENTS.md's known-gap note to be updated deliberately, rather than
# silently going stale behind an ordinary green test nobody has reason to
# look at twice. So for a listed shape there are exactly two outcomes:
# the exact known signature reproduces (expected `xfail`), or the test
# fails outright -- covering both "the gap closed" and "a different
# failure occurred" with one loud signal, matching the `strict`-xfail
# spirit without pytest's own `strict=True` bookkeeping (which an
# imperative, signature-gated `pytest.xfail()` call cannot combine with,
# per the previous review round).
_SCAN_KNOWN_DIVERGENT_SHAPES: frozenset[str] = frozenset()


@pytest.mark.skipif(not (_HAVE_GXX and _HAVE_CLANG), reason=_SKIP_REASON)
@pytest.mark.parametrize("shape_name", sorted(_BUILD_SHAPES))
def test_scan_against_real_dump_baseline_is_comparable(
    tmp_path: Path, shape_name: str
) -> None:
    """The end-to-end acceptance check from #810, generalized across build
    shapes: a real ``dump`` baseline must be comparable via
    ``scan --against`` -- never ``NOT_COMPARABLE`` -- for an unchanged
    codebase."""
    shape = _BUILD_SHAPES[shape_name]
    so_path, header, compile_db = _build_library(tmp_path, **shape)  # type: ignore[arg-type]
    baseline = tmp_path / "baseline.json"
    _dump_via_cli_to_file(so_path, header, tmp_path, compile_db, baseline)

    scan_result = CliRunner().invoke(
        main,
        [
            "scan",
            str(so_path),
            "-H",
            str(header),
            "--sources",
            str(tmp_path),
            "--build-info",
            str(compile_db),
            "--depth",
            "source",
            "--ast-frontend",
            "clang",
            "--against",
            str(baseline),
        ],
    )

    if shape_name in _SCAN_KNOWN_DIVERGENT_SHAPES:
        is_known_signature = (
            "NOT_COMPARABLE" in scan_result.output
            and "include_sequence" in scan_result.output
        )
        if is_known_signature:
            pytest.xfail(
                f"{shape_name}: known-open dump-vs-scan include-dir "
                "seeding divergence (see this module's own comment above "
                "this test)"
            )
        pytest.fail(
            f"{shape_name} is listed in _SCAN_KNOWN_DIVERGENT_SHAPES, but "
            "the previously-diagnosed failure signature (NOT_COMPARABLE "
            "naming include_sequence) did not reproduce. Either the "
            "underlying gap has closed -- remove this shape from "
            "_SCAN_KNOWN_DIVERGENT_SHAPES and update AGENTS.md's "
            "known-gap note for it -- or a different failure occurred "
            "and needs its own investigation. "
            f"exit_code={scan_result.exit_code!r} output={scan_result.output!r}"
        )

    assert "NOT_COMPARABLE" not in scan_result.output, (shape_name, scan_result.output)
    assert scan_result.exit_code == 0, (shape_name, scan_result.output)


@pytest.mark.skipif(not (_HAVE_GXX and _HAVE_CLANG), reason=_SKIP_REASON)
@pytest.mark.parametrize("shape_name", sorted(_BUILD_SHAPES))
def test_compare_implicit_dump_against_real_dump_baseline_is_comparable(
    tmp_path: Path, shape_name: str
) -> None:
    """Same acceptance check as above, against ``compare``'s implicit-dump
    operand instead of ``scan --against`` -- kept as its own test (rather
    than folded into the scan test above) precisely because the two paths
    are independent and can disagree: see
    ``_SCAN_KNOWN_DIVERGENT_SHAPES`` -- ``compare`` does not reproduce that
    gap for the identical inputs, so this test has no xfail list, and a
    future regression narrowing *this* path's coverage should fail loudly
    rather than being masked by a scan-specific xfail."""
    shape = _BUILD_SHAPES[shape_name]
    so_path, header, compile_db = _build_library(tmp_path, **shape)  # type: ignore[arg-type]
    baseline = tmp_path / "baseline.json"
    _dump_via_cli_to_file(so_path, header, tmp_path, compile_db, baseline)

    compare_result = CliRunner().invoke(
        main,
        [
            "compare",
            str(baseline),
            str(so_path),
            "-H",
            str(header),
            "--sources",
            str(tmp_path),
            "--build-info",
            str(compile_db),
            "--depth",
            "source",
            "--ast-frontend",
            "clang",
        ],
    )
    assert "NOT_COMPARABLE" not in compare_result.output, (
        shape_name,
        compare_result.output,
    )
    assert compare_result.exit_code == 0, (shape_name, compare_result.output)


# Collect-mode parity (`dump`'s CLI --depth resolver vs. the typed
# `DumpRequest` path, CLI cleanup phase two PR 3A blocker 5 sub-issue 2) lives
# in `tests/test_dump_collect_mode_parity.py`, NOT here, even though it is the
# same "CLI and typed API must agree" question this module is named for: every
# test in this module is `pytest.mark.integration` and skips without a real
# castxml/g++ toolchain, while that parity check is pure resolution logic that
# must gate the default fast/PR unit lane. Splitting it out is what makes it an
# actual gate rather than a test that skips wherever it matters.

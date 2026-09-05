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

"""Branch coverage for ``buildsource.l2_seed.seed_includes_and_fold_compile_context``
-- the one call that both seeds a dump's L2 include dirs from the build and folds
the P0.3 L3->L2 compile context, for every caller that resolves an input.

Previously ``tests/test_non_elf_dump_l2_seed.py``, where these branch tests sat
alongside per-call-site tests of ``cli_dump_non_elf.handle_non_elf_dump`` and
``cli_dump_helpers.perform_elf_dump``. Both functions were retired with ADR-063
Track 1 (no production caller once ``dump_cmd``'s real run moved onto the shared
typed executor for either binary format), and what their tests asserted about
this primitive's *callers* is owned at the shared pipeline's own seam --
``tests/test_typed_dump_request.py`` for the seed/fold and its cleanup ordering,
``tests/test_header_compile_context.py`` for the resulting compile context and
``parsed_with_build_context`` stamp, ``tests/test_legacy_compile_db_typed_
threading.py`` for the legacy ``-p``/``--compile-db`` tokens' precedence against
the fold, and ``tests/test_dump_cli_execution_behaviors.py`` for the ``dump``
CLI's own remaining share. The primitive's own branches -- no inputs, no match,
ambiguous, corrupt pack -- have no other home, so they stay here under a name
that describes what is left.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from abicheck.errors import HeaderCompileContextAmbiguousError

# ── seed_includes_and_fold_compile_context branch coverage ──────────────────


def _seed_and_fold(**overrides: Any):
    from abicheck.buildsource.l2_seed import seed_includes_and_fold_compile_context

    kwargs = dict(
        headers=[],
        includes=[],
        sources=None,
        build_info=None,
        build_config=None,
        build_query=None,
        build_compile_db=None,
        collect_mode="source-target",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=False,
        frontend="auto",
        frontend_context="host",
        lang="c++",
        lang_explicit=False,
        pending_cleanups=[],
    )
    kwargs.update(overrides)
    return seed_includes_and_fold_compile_context(**kwargs)


def _write_compile_db(tmp_path, src, extra_args):
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "arguments": ["c++", "-c", str(src), "-o", "out.o", *extra_args],
                }
            ]
        ),
        encoding="utf-8",
    )


def _write_corrupt_pack(pack_dir):
    """A directory ``is_pack_dir()`` recognizes as a (corrupt) pack -- a
    ``manifest.json`` present but unparseable, so ``pack_io.load()``
    raises decoding it."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "manifest.json").write_text("{not valid json", encoding="utf-8")


def test_seed_and_fold_no_inputs_is_noop():
    from pathlib import Path

    incs, applied, ctx, dirs = _seed_and_fold(headers=[Path("x.h")])
    assert (incs, applied, dirs) == ([], False, ())
    assert ctx.gcc_path is None


def test_seed_and_fold_no_match_returns_none(tmp_path):
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "unrelated.cpp"
    src.write_text("int f() { return 0; }\n", encoding="utf-8")
    _write_compile_db(tmp_path, src, ["-std=c++20"])

    pending: list[Any] = []
    incs, applied, ctx, dirs = _seed_and_fold(
        headers=[header], sources=tmp_path, pending_cleanups=pending
    )
    assert (applied, dirs, pending) == (False, (), [])


def test_seed_and_fold_ambiguous_raises_and_drains_pending_cleanups(tmp_path):
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src_a),
                    "arguments": ["c++", "-c", str(src_a), "-std=c++17"],
                },
                {
                    "directory": str(tmp_path),
                    "file": str(src_b),
                    "arguments": ["c++", "-c", str(src_b), "-std=c++20"],
                },
            ]
        ),
        encoding="utf-8",
    )
    pending: list[Any] = []
    with pytest.raises(HeaderCompileContextAmbiguousError):
        _seed_and_fold(headers=[header], sources=tmp_path, pending_cleanups=pending)
    # The fail-closed case still drains any temp-build-dir cleanups this
    # attempt created before propagating (P0.3's rule) -- nothing left pending.
    assert pending == []


def test_seed_and_fold_corrupt_pack_degrades_to_empty(tmp_path):
    pack_dir = tmp_path / "pack"
    _write_corrupt_pack(pack_dir)
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")

    _incs, applied, _ctx, dirs = _seed_and_fold(headers=[header], sources=pack_dir)
    assert (applied, dirs) == (False, ())


# --- The legacy `-p`/`--compile-db` auto-match must not stack on the P0.3 fold ---
#
# CLI cleanup phase two, PR 3A. Both mechanisms are fed by the *same*
# `--build-info` compile database, so when the fold resolves a context for
# these headers, presenting the legacy match's own derived flags to it as
# though they were an explicit user choice records the same evidence twice --
# measured end to end as `macro_ops` == `[["D","FOO=1"],["D","FOO=1"]]` and
# `include_sequence` == `[]` where every other resolver records one entry and
# one slot, i.e. a `profile_fingerprint` a `scan --against` correctly refuses
# as NOT_COMPARABLE. These two pin the *decision* at the seam it is made,
# rather than only through a real toolchain (the end-to-end lens is
# `tests/test_dump_cli_typed_api_parity.py`, which is `integration`-marked and
# so cannot gate this in the default lane).

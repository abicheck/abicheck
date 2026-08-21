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

"""abicheck-internal-bugs finding 1: ``dump``'s own ``header_roots`` (fed to
``dumper_scoping.resolve_dependency_scope`` right before serialization) must
match ``dumper_scoping.apply_dependency_scope_to_run_dump_result``'s
computation -- the choke point ``compare``'s own live-binary dumping
(``service.run_dump``) uses for the identical dependency-scoping decision.

Before this fix, ``perform_elf_dump``'s (ELF) ``write_snapshot_output`` call
passed only ``headers`` (plus, for ELF, any ``--dump-manifest`` roots) as
``header_roots`` -- omitting ``public_headers``/``public_header_dirs``, which
``apply_dependency_scope_to_run_dump_result`` always folds in. A standalone
``dump`` of a library whose public surface is declared via ``public_headers``/
``public_header_dirs`` therefore scoped its dependency filtering differently
than ``compare``'s live dump of the identical input -- producing two
``dependency_scope: filtered`` snapshots whose actual filtered content (and
scope_fingerprint) silently disagreed, breaking every later ``compare``
between a ``dump``-produced baseline and a live candidate (reported:
"toolchain_matrix has been silently broken since it was created").

``handle_non_elf_dump`` (PE/Mach-O)'s identical fix is covered by
``tests/test_non_elf_dump_l2_seed.py::
test_non_elf_dump_header_roots_includes_public_headers_and_dirs`` instead --
that file already houses this path's other ``header_roots``/L2-seed tests,
and ``tests/test_cli_dump_helpers_coverage.py`` (the ELF path's usual home)
sits at the AI-readiness file-size hard cap, so this ELF-side test lives in
its own small file rather than growing that one further.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.cli_dump_helpers import perform_elf_dump
from abicheck.model import AbiSnapshot


def _elf_dump_callables():  # noqa: ANN202
    """Minimal (recorder, stamp, write, expand, populate) stub callables --
    a small local copy of ``test_cli_dump_helpers_coverage._elf_dump_
    callables``'s shape, since importing across test modules would couple
    this file to one that's already at its own line-count cap."""

    def _stamp(snap, *, git_tag, build_id, no_git):  # noqa: ANN001
        pass

    def _expand(inputs):  # noqa: ANN001
        return list(inputs)

    def _populate(snap, so_path, search_paths, sysroot, ld_library_path):  # noqa: ANN001
        pass

    return _stamp, _expand, _populate


def test_perform_elf_dump_header_roots_includes_public_headers_and_dirs(
    tmp_path: Path, monkeypatch
) -> None:
    so = tmp_path / "lib.so"
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")
    pub_header = Path("/usr/include/mylib/extra.h")
    pub_dir = Path("/usr/include/mylib")
    snap = AbiSnapshot(library="lib.so", version="1.0", from_headers=True)
    monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: snap)

    captured: dict[str, object] = {}

    def _write(*_a, header_roots=(), **_k):  # noqa: ANN002, ANN003
        captured["header_roots"] = header_roots

    _stamp, _expand, _populate = _elf_dump_callables()

    perform_elf_dump(
        so,
        (hdr,),
        (),
        "1.0",
        "c",
        None,
        None,
        None,
        (),
        None,
        True,
        False,
        None,
        (pub_header,),
        (pub_dir,),
        None,
        False,
        (),
        "",
        None,
        None,
        False,
        None,
        None,
        None,
        None,
        False,
        "off",
        _expand,
        _populate,
        _stamp,
        _write,
    )

    assert set(captured["header_roots"]) == {hdr, pub_header, pub_dir}

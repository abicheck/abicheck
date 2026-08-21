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

"""``scan``'s candidate resolution reaches the ADR-039 build-context collector.

CLI cleanup phase two, PR 3A (dump/scan resolver convergence). ``scan`` was the
one candidate resolver that never collected ADR-039 evidence at all: the ELF
``dump`` CLI has always called the collector, and the typed pipeline
(``compare``'s implicit-dump operand, ``dump``'s ``run_dump_request``) gained it
in PR #809 -- so ``scan --against`` a ``dump``-produced baseline compared a
candidate carrying no ``build_context_defines``/``conditional_fields`` against a
baseline carrying both, and the reconciler could not clear a context-free
header-parse false positive on the candidate side the way it already did on the
baseline's.

Every gate now lives in one shared function
(``header_conditionals.attach_build_context_for_parsed_headers``), so these
tests double as the executable statement of what that gate promises for the
``scan`` call site specifically.
"""

from __future__ import annotations

import json
from pathlib import Path

from abicheck.cli_scan import _build_new_snapshot
from abicheck.compile_context import CompileContext
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import AbiSnapshot

_GUARDED_HEADER = "struct Config {\n int v;\n#ifdef KEEP\n int legacy;\n#endif\n};\n"


def _project(tmp_path: Path, *, db_flags: str = "-DKEEP") -> tuple[Path, Path]:
    """A header with a ``#ifdef``-guarded field plus a compile DB defining it."""
    hdr = tmp_path / "config.h"
    hdr.write_text(_GUARDED_HEADER, encoding="utf-8")
    src = tmp_path / "config.c"
    src.write_text('#include "config.h"\n', encoding="utf-8")
    db = tmp_path / "compile_commands.json"
    db.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "command": f"cc {db_flags} -c {src}",
                }
            ]
        ),
        encoding="utf-8",
    )
    return hdr, db


def _stub_resolve(monkeypatch, snap: AbiSnapshot) -> None:
    monkeypatch.setattr("abicheck.service.resolve_input", lambda *a, **k: snap)
    monkeypatch.setattr(
        "abicheck.cli_buildsource.embed_build_source", lambda *a, **k: None
    )


def test_scan_candidate_collects_adr039_build_context(monkeypatch, tmp_path: Path):
    """The load-bearing case: a ``scan`` candidate with ``--build-info`` naming a
    real compile database now carries the build's active ``-D`` set and the
    guarded fields it explains.

    Confirmed to fail against the pre-fix code, where ``_build_new_snapshot``
    never called the collector at all and both fields stayed at their defaults.
    """
    hdr, db = _project(tmp_path)
    snap = AbiSnapshot(
        library="lib.so", version="1.0", from_headers=True, elf=ElfMetadata()
    )
    _stub_resolve(monkeypatch, snap)

    _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[hdr],
        includes=[],
        sources=None,
        collect_mode="off",
        lang="c++",
        allow_build_query=False,
        build_info=db,
        defer_cleanup=[],
    )

    assert snap.build_context_defines == {"KEEP"}
    assert snap.conditional_fields["Config"]["legacy"]["guard"] == "KEEP"


def test_scan_candidate_expands_a_header_directory_for_the_collector(
    monkeypatch, tmp_path: Path
):
    """``scan -H include/`` passes a *directory*; the collector reads each entry
    with ``read_text`` and silently skips a directory, so the shared gate must
    expand it first or ``conditional_fields`` stays empty for the single most
    common header-input shape."""
    inc = tmp_path / "include"
    inc.mkdir()
    hdr = inc / "config.h"
    hdr.write_text(_GUARDED_HEADER, encoding="utf-8")
    src = tmp_path / "config.c"
    src.write_text('#include "config.h"\n', encoding="utf-8")
    db = tmp_path / "compile_commands.json"
    db.write_text(
        json.dumps(
            [{"directory": str(tmp_path), "file": str(src), "command": f"cc -DKEEP -c {src}"}]
        ),
        encoding="utf-8",
    )
    snap = AbiSnapshot(
        library="lib.so", version="1.0", from_headers=True, elf=ElfMetadata()
    )
    _stub_resolve(monkeypatch, snap)

    _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[inc],
        includes=[],
        sources=None,
        collect_mode="off",
        lang="c++",
        allow_build_query=False,
        build_info=db,
        defer_cleanup=[],
    )

    assert snap.conditional_fields["Config"]["legacy"]["guard"] == "KEEP"


def test_scan_candidate_collector_sees_only_pre_fold_explicit_flags(
    monkeypatch, tmp_path: Path
):
    """The ninth finding on ``AGENTS.md``'s L3->L2-fold entry, for ``scan``.

    The auto-derived, per-header-matched build context must never be unioned
    snapshot-wide -- so the collector's ``extra_flags`` must carry only the
    caller's own explicit, *pre-fold* tokens, never what the P0.3 fold derived
    and wrote back over ``compile_context`` a few lines earlier.
    """
    from abicheck import header_conditionals as _hc

    hdr, db = _project(tmp_path)
    snap = AbiSnapshot(
        library="lib.so", version="1.0", from_headers=True, elf=ElfMetadata()
    )
    _stub_resolve(monkeypatch, snap)
    # Simulate the fold resolving a context carrying a derived define.
    monkeypatch.setattr(
        "abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context",
        lambda **kw: (
            list(kw["includes"]),
            True,
            CompileContext(gcc_option_tokens=("-DL3ONLY=1",)),
            (),
        ),
    )

    captured: dict[str, list[str]] = {}
    real_attach = _hc.attach_build_context

    def _spy(snap_arg, compile_db_arg, headers_arg, extra_flags, **kw):
        captured["extra_flags"] = list(extra_flags)
        return real_attach(snap_arg, compile_db_arg, headers_arg, extra_flags, **kw)

    monkeypatch.setattr(_hc, "attach_build_context", _spy)

    _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[hdr],
        includes=[],
        sources=None,
        collect_mode="off",
        lang="c++",
        allow_build_query=False,
        build_info=db,
        compile_context=CompileContext(gcc_option_tokens=("-DUSERONLY=1",)),
        defer_cleanup=[],
    )

    assert captured["extra_flags"] == ["-DUSERONLY=1"]
    assert "-DL3ONLY=1" not in captured["extra_flags"]


def test_scan_candidate_collector_skipped_when_headers_were_not_parsed(
    monkeypatch, tmp_path: Path
):
    """``from_headers=False`` (what a ``--dwarf-only``-style resolution leaves):
    a database matching the *requested* headers is not evidence about a snapshot
    that never parsed them -- the same gate the sibling
    ``parsed_with_build_context`` stamp already applies."""
    hdr, db = _project(tmp_path)
    snap = AbiSnapshot(
        library="lib.so", version="1.0", from_headers=False, elf=ElfMetadata()
    )
    _stub_resolve(monkeypatch, snap)

    _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[hdr],
        includes=[],
        sources=None,
        collect_mode="off",
        lang="c++",
        allow_build_query=False,
        build_info=db,
        defer_cleanup=[],
    )

    assert snap.build_context_defines == set()
    assert snap.conditional_fields == {}


def test_scan_candidate_collector_skipped_for_a_non_elf_snapshot(
    monkeypatch, tmp_path: Path
):
    """The ADR-039 collector is an established ELF-only mechanism -- the PE/Mach-O
    ``dump`` path never calls it -- so ``scan`` must not attach build-context
    evidence to a non-ELF snapshot and then silently disagree with it."""
    hdr, db = _project(tmp_path)
    snap = AbiSnapshot(library="lib.dll", version="1.0", from_headers=True)
    assert snap.elf is None
    _stub_resolve(monkeypatch, snap)

    _build_new_snapshot(
        binary=tmp_path / "lib.dll",
        headers=[hdr],
        includes=[],
        sources=None,
        collect_mode="off",
        lang="c++",
        allow_build_query=False,
        build_info=db,
        defer_cleanup=[],
    )

    assert snap.build_context_defines == set()


def test_scan_candidate_collector_skipped_for_a_loaded_json_snapshot(
    monkeypatch, tmp_path: Path
):
    """A *loaded* JSON snapshot round-trips ``elf``/``from_headers`` exactly as
    saved, so those signals alone also fire for an input that never touched the
    header parser. Running the collector there would scan the *current*
    filesystem and overwrite the loaded snapshot's own recorded evidence."""
    hdr, db = _project(tmp_path)
    binary = tmp_path / "baseline.abicheck.json"
    binary.write_text('{"schema_version": 25}\n', encoding="utf-8")
    snap = AbiSnapshot(
        library="lib.so", version="1.0", from_headers=True, elf=ElfMetadata()
    )
    _stub_resolve(monkeypatch, snap)

    _build_new_snapshot(
        binary=binary,
        headers=[hdr],
        includes=[],
        sources=None,
        collect_mode="off",
        lang="c++",
        allow_build_query=False,
        build_info=db,
        defer_cleanup=[],
    )

    assert snap.build_context_defines == set()
    assert snap.conditional_fields == {}


def test_scan_candidate_collector_skipped_without_build_info(
    monkeypatch, tmp_path: Path
):
    """A plain context-free scan is still the common case: no ``--build-info``
    means no compile database to resolve, and the gate must be a silent no-op
    rather than raising."""
    hdr, _db = _project(tmp_path)
    snap = AbiSnapshot(
        library="lib.so", version="1.0", from_headers=True, elf=ElfMetadata()
    )
    _stub_resolve(monkeypatch, snap)

    _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[hdr],
        includes=[],
        sources=None,
        collect_mode="off",
        lang="c++",
        allow_build_query=False,
        defer_cleanup=[],
    )

    assert snap.build_context_defines == set()

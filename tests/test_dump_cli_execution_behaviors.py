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

"""ADR-063 Track 1: the ``dump`` CLI execution behaviours that used to be
pinned against ``cli_dump_helpers.perform_elf_dump`` /
``cli_dump_non_elf.handle_non_elf_dump``, rehomed onto the path that
actually runs today.

Both of those functions lost their production caller when ADR-063 Phase 1
routed ``dump_cmd``'s real run (ELF *and* PE/Mach-O) through the shared
typed executor -- ``frontends.cli.dump_execute.execute_and_write_dump_cli_run``
-> :func:`abicheck.service_dump_pipeline.execute_dump_request` ->
:func:`abicheck.workflows.artifact.execute._resolve_side_snapshot_impl` ->
``service.resolve_input``. They stayed alive only through their own unit
tests, which is exactly the shape the duplication-and-convergence plan's
Track 1 retires (``docs/contribute/plans/
duplication-and-convergence-assessment.md``, "T1 — Dead-implementation
retirement").

**What is here and what is deliberately not.** The great majority of those
tests pinned behaviour that the shared pipeline already owns and already
tests at its own seam -- the ADR-039 build-context collector and the L2
seed/fold's cleanup ordering (``tests/test_typed_dump_request.py``), the
``parsed_with_build_context`` stamp and its unmatched-database negative
(``tests/test_header_compile_context.py``), the header-graph attach and its
``--dwarf-only``/``lang`` normalisation, the Python/NumPy surface attach and
the AST-memoize scope (``tests/test_service_unit.py``), the
explicit-``-I``-only provenance-widening rule
(``tests/test_service_input_resolution.py``), and the legacy
``-p``/``--compile-db`` token threading
(``tests/test_legacy_compile_db_typed_threading.py``). Re-asserting those
through a third call site would grow the suite without adding a fact.

This module holds the remainder: the assertions whose *only* home was one
of the two retired functions, restated against the real ``dump`` CLI, which
is now the single caller of that pipeline. Driving the CLI rather than
``execute_dump_request`` directly is deliberate for these particular
facts -- ``header_roots`` and the public-root forwarding are computed by
``frontends/cli/commands/dump.py`` and consumed by
``cli_buildsource._write_snapshot_output``, i.e. on either side of the
executor, so a test that called the executor alone could not see them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.errors import AbicheckError
from abicheck.model import AbiSnapshot


@pytest.fixture()
def elf_lib(tmp_path: Path) -> Path:
    """A file with real ELF magic bytes -- enough for ``detect_binary_format``
    to route the CLI down the ELF branch with the parse itself stubbed."""
    p = tmp_path / "lib.so"
    p.write_bytes(b"\x7fELF" + b"\x00" * 200)
    return p


@pytest.fixture()
def pe_lib(tmp_path: Path) -> Path:
    p = tmp_path / "foo.dll"
    p.write_bytes(b"MZ" + b"\x00" * 200)
    return p


def _stub_elf_parse(
    monkeypatch, snap: AbiSnapshot, capture: dict[str, Any] | None = None
):
    """Stub ``dumper.dump`` -- the ELF header/binary parse the CLI reaches
    through ``service.resolve_input`` -> ``service_dump_native._dump_elf``."""
    import abicheck.dumper as dumper

    def _fake_dump(**kwargs: Any) -> AbiSnapshot:
        if capture is not None:
            capture.update(kwargs)
        return snap

    monkeypatch.setattr(dumper, "dump", _fake_dump)


def _stub_native_parse(monkeypatch, snap: AbiSnapshot):
    """Stub the whole native extraction, below ``service.run_dump``'s own
    dependency-scope wrapper.

    Patching ``_run_dump_uncached`` rather than ``run_dump`` itself is what
    keeps :func:`~abicheck.dumper_scoping.
    apply_dependency_scope_to_run_dump_result` -- the choke point the
    ``header_roots`` equivalence tests below compare against -- in the call
    chain with nothing left to extract.
    """
    import abicheck.service_dump_native as native

    monkeypatch.setattr(native, "_run_dump_uncached", lambda *a, **k: snap)


def _spy_dependency_scope_roots(monkeypatch) -> list[tuple[Path, ...]]:
    """Record the header-root set ``service.run_dump``'s own dependency-scope
    choke point derives, so a test can compare ``dump``'s independently-built
    ``header_roots`` against it instead of restating its formula."""
    import abicheck.dumper_scoping as dumper_scoping

    seen: list[tuple[Path, ...]] = []
    real = dumper_scoping.resolve_dependency_scope

    def _spy(snap: AbiSnapshot, include_dependencies: bool, roots: Any) -> AbiSnapshot:
        seen.append(tuple(roots or ()))
        return real(snap, include_dependencies, roots)

    monkeypatch.setattr(dumper_scoping, "resolve_dependency_scope", _spy)
    return seen


def _capture_write(monkeypatch) -> dict[str, Any]:
    """Capture ``_write_snapshot_output``'s kwargs -- the CLI's own write
    step, which both retired functions called directly and
    ``execute_and_write_dump_cli_run`` now calls for either format."""
    captured: dict[str, Any] = {}

    def _write(snap: AbiSnapshot, *_a: Any, **kwargs: Any) -> None:
        captured.update(kwargs)
        captured["snapshot"] = snap

    import abicheck.cli_buildsource as cli_buildsource

    monkeypatch.setattr(cli_buildsource, "_write_snapshot_output", _write)
    return captured


def _run(*args: str):
    result = CliRunner().invoke(main, ["dump", *args])
    return result


# ── header_roots: the dependency-scoping parity invariant ───────────────────


def _assert_header_roots_match_the_scoping_choke_point(
    monkeypatch,
    elf: bool,
    binary: Path,
    operands: list[Path],
    manifest: Path | None = None,
) -> None:
    """One ``dump`` run: assert the ``header_roots`` the CLI hands its write
    step is exactly the root set ``service.run_dump``'s own dependency-scope
    choke point derived for the identical invocation."""
    snap = AbiSnapshot(library=binary.name, version="1.0", from_headers=True)
    if elf:
        _stub_elf_parse(monkeypatch, snap)
    else:
        _stub_native_parse(monkeypatch, snap)
    scope_roots = _spy_dependency_scope_roots(monkeypatch)
    captured = _capture_write(monkeypatch)

    header_args: list[str] = []
    for operand in operands:
        header_args += ["-H", str(operand)]
    if manifest is not None:
        header_args += ["--dump-manifest", str(manifest)]
    result = _run(str(binary), *header_args)
    assert result.exit_code == 0, result.output

    assert scope_roots, "the dependency-scope choke point never ran"
    assert set(captured["header_roots"]) == set(scope_roots[-1]), (
        operands,
        captured["header_roots"],
        scope_roots[-1],
    )


@pytest.mark.parametrize("fmt", ["elf", "pe"])
def test_dump_header_roots_match_the_dependency_scoping_choke_point(
    fmt: str, elf_lib: Path, pe_lib: Path, tmp_path: Path, monkeypatch
) -> None:
    """abicheck-internal-bugs finding 1, restated on the live path as the
    cross-path equivalence it actually is.

    ``dump``'s own ``header_roots`` (fed to
    ``dumper_scoping.resolve_dependency_scope`` right before serialization)
    must match ``dumper_scoping.apply_dependency_scope_to_run_dump_result``'s
    computation -- the choke point ``compare``'s own live-binary dumping uses
    for the identical dependency-scoping decision. Before the fix the
    ``dump`` path passed only ``headers`` (plus, for ELF, any
    ``--dump-manifest`` roots), omitting the public header files/directories
    that computation always folds in, so a ``dump``-produced baseline and a
    live ``compare`` candidate of the same input filtered differently and
    silently disagreed on ``scope_fingerprint`` ("toolchain_matrix has been
    silently broken since it was created").

    The oracle here is the choke point's *own* observed root set, captured
    from the same run -- not a second copy of its formula written into the
    test, which is what let the original two assertions (one per retired
    function) drift from it in the first place. Exercised over several
    independently-shaped ``-H`` inputs -- a bare file, a directory, both
    together, and neither -- and over both binary formats, which used to be
    two separate implementations and are now one call site.
    """
    binary = elf_lib if fmt == "elf" else pe_lib
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")
    pub_dir = tmp_path / "include"
    pub_dir.mkdir()
    (pub_dir / "api.h").write_text("int api(void);\n", encoding="utf-8")

    for operands in ([], [hdr], [pub_dir], [hdr, pub_dir]):
        _assert_header_roots_match_the_scoping_choke_point(
            monkeypatch, fmt == "elf", binary, operands
        )

    if fmt != "elf":
        # `--dump-manifest` is ELF-only (`dump_cmd` rejects it for PE/Mach-O).
        return
    # The manifest case is the one where the two computations are genuinely
    # independent rather than trivially equal: `--dump-manifest` is mutually
    # exclusive with `-H`, so `headers` is empty on both sides and the whole
    # root set comes from `dump_manifest_header_roots`. Dropping that term
    # from either side leaves a manifest project header installed under a
    # system-like prefix misclassified as a dependency.
    (tmp_path / "manifest_root.h").write_text("int f(void);\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "roots: [manifest_root.h]\n"
        "translation_units:\n"
        "  - name: main\n"
        "    forced_includes: [manifest_root.h]\n",
        encoding="utf-8",
    )
    _assert_header_roots_match_the_scoping_choke_point(
        monkeypatch, True, binary, [], manifest=manifest
    )


def test_dump_forwards_public_roots_to_the_write_time_embed(
    elf_lib: Path, tmp_path: Path, monkeypatch
) -> None:
    """The caller half of the write-time L4 public-root fix.

    ``_write_snapshot_output`` forwards ``public_headers``/
    ``public_header_dirs`` to ``embed_build_source`` (pinned in
    ``tests/test_dump_embed_idempotence.py``); this pins that the ``dump``
    CLI actually hands them over rather than letting them default to empty,
    which is what made a real ``dump -H api.h --depth source`` link nothing
    at L4.
    """
    hdr = tmp_path / "widget.h"
    hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
    incdir = tmp_path / "include"
    incdir.mkdir()
    (incdir / "api.h").write_text("int api(void);\n", encoding="utf-8")

    snap = AbiSnapshot(library="lib.so", version="1.0", from_headers=True)
    _stub_elf_parse(monkeypatch, snap)
    captured = _capture_write(monkeypatch)

    result = _run(str(elf_lib), "-H", str(hdr), "-H", str(incdir))
    assert result.exit_code == 0, result.output

    assert captured["public_headers"] == (hdr,)
    assert captured["public_header_dirs"] == (incdir,)


# ── evidence stamping ───────────────────────────────────────────────────────


def test_dump_dwarf_only_does_not_stamp_build_context(
    elf_lib: Path, tmp_path: Path, monkeypatch
) -> None:
    """``--dwarf-only`` explicitly ignores ``-H`` headers
    (``dumper._try_dwarf_snapshot`` warns "ignoring provided headers" and
    returns a DWARF-built snapshot with ``from_headers`` left ``False``), so
    a compile database matching the originally *requested* headers must not
    be recorded as real build-context evidence for a snapshot that never
    parsed them -- ``evidence_depth_label`` reads that flag as genuine
    "build" evidence for the strict ``--depth build`` gate.

    ``snap.from_headers`` is the gate that enforces it, so this states the
    rule over that whole axis rather than only the one failing combination:
    across each way of naming the same matching build evidence, the stamp
    tracks whether the snapshot actually parsed the headers, and an explicit
    ``--dwarf-only`` never stamps.

    The stamped object is read back off the *write* step, not off the
    snapshot the parse returned: the whole-snapshot cache
    (``service_dump_cache.cached_run_dump``) can hand the pipeline a copy, so
    an identity-based assertion would silently pass against an unstamped
    original.
    """
    hdr = tmp_path / "widget.h"
    hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "arguments": ["c++", "-c", str(src), "-o", "out.o", "-std=c++20"],
                }
            ]
        ),
        encoding="utf-8",
    )

    evidence_flags = (("--sources", str(tmp_path)), ("--build-info", str(tmp_path)))
    for flags in evidence_flags:
        for from_headers in (True, False):
            _stub_elf_parse(
                monkeypatch,
                AbiSnapshot(library="lib.so", version="1.0", from_headers=from_headers),
            )
            captured = _capture_write(monkeypatch)
            result = _run(str(elf_lib), "-H", str(hdr), *flags)
            assert result.exit_code == 0, result.output
            # Stamped exactly when the headers were really parsed -- the
            # build evidence matched identically either way.
            assert captured["snapshot"].parsed_with_build_context is from_headers

        # And the request that explicitly discards those headers never
        # stamps, even though the very same database matched them.
        _stub_elf_parse(
            monkeypatch,
            AbiSnapshot(library="lib.so", version="1.0", from_headers=False),
        )
        captured = _capture_write(monkeypatch)
        result = _run(str(elf_lib), "-H", str(hdr), *flags, "--dwarf-only")
        assert result.exit_code == 0, result.output
        assert captured["snapshot"].parsed_with_build_context is False


# ── header-input scope ──────────────────────────────────────────────────────


def test_dump_header_directory_reaches_the_extraction_scope(
    elf_lib: Path, tmp_path: Path, monkeypatch
) -> None:
    """A directory passed via ``-H``/``--header`` must reach the extraction
    contract's scope, not only its expanded per-file listing, so the
    resulting ``scope_fingerprint`` agrees with a live ``compare``-side
    extraction of the identical header set.

    Note the *channel* changed with the migration, deliberately, and this
    test records that: ``perform_elf_dump`` routed the directory into
    ``dumper.dump``'s ``scope_header_dirs`` (contract scope only, no ADR-015
    provenance tagging), while the typed request splits ``-H`` with
    ``header_utils.split_public_header_inputs`` and passes the directory as
    a real ``public_header_dirs`` entry -- the same thing ``compare`` has
    always done with its own ``-H`` list, and what ``dump``'s own
    ``--public-header-dir`` flag existed for before it was removed as a
    second way of saying it. ``scope_header_dirs`` now has no typed-request
    field populating it at all (``api_types.py`` says so where it excludes
    the field from the manifest conflict set). The scope half of the old
    assertion therefore still holds; the "provenance tagging stays off" half
    is no longer true of ``dump``, by the same design decision that made
    ``dump`` and ``compare`` agree.
    """
    include_dir = tmp_path / "include"
    include_dir.mkdir()
    (include_dir / "api.h").write_text("int api(void);\n", encoding="utf-8")

    captured: dict[str, Any] = {}
    _stub_elf_parse(
        monkeypatch,
        AbiSnapshot(library="lib.so", version="1.0", from_headers=True),
        captured,
    )
    _capture_write(monkeypatch)

    result = _run(str(elf_lib), "-H", str(include_dir))
    assert result.exit_code == 0, result.output

    assert captured["public_header_dirs"] == [include_dir]
    # The directory's own contents still reach the parse as headers.
    assert captured["headers"] == [include_dir / "api.h"]


# ── include-search ordering and cache keying ────────────────────────────────


def test_dump_inferred_header_root_ranks_below_the_compile_context(
    elf_lib: Path, tmp_path: Path, monkeypatch
) -> None:
    """``resolve_inferred_header_roots``' ``deferred`` tokens exist
    specifically to search *below* any existing build context (that is the
    whole point of their ``-isystem`` bucket) -- an inferred umbrella root
    outranking a real build's own generated-header directory would shadow it
    for a colliding header.

    On the retired path this was an ordering the caller had to maintain by
    hand (excluding ``deferred`` from the "explicit" side of the L3 merge and
    appending it back afterwards). The shared pipeline makes it structural:
    ``service_dump_native._dump_elf`` derives the inferred roots *after* the
    compile context is resolved and appends them (``eff_tokens =
    cc.gcc_option_tokens + tuple(deferred)``), so no caller can invert it.
    Asserted here over several independently-supplied context directories,
    since the claim is about relative order for any of them, not one path.
    """
    root = tmp_path / "include"
    (root / "oneapi").mkdir(parents=True)
    umbrella = root / "oneapi" / "tbb.h"
    umbrella.write_text("// umbrella\n", encoding="utf-8")

    for name in ("existingbuild", "generated", "vendor-sdk"):
        ctx_dir = tmp_path / name
        ctx_dir.mkdir(exist_ok=True)

        captured: dict[str, Any] = {}
        _stub_elf_parse(
            monkeypatch,
            AbiSnapshot(library="lib.so", version="1.0", from_headers=True),
            captured,
        )
        _capture_write(monkeypatch)

        result = _run(
            str(elf_lib),
            "-H",
            str(umbrella),
            "--compiler-option",
            "-isystem",
            "--compiler-option",
            str(ctx_dir),
        )
        assert result.exit_code == 0, result.output

        tokens = list(captured["gcc_option_tokens"])
        assert str(ctx_dir) in tokens, tokens
        assert str(root) in tokens, tokens
        assert tokens.index(str(ctx_dir)) < tokens.index(str(root)), tokens


def test_dump_hashes_context_include_dirs_into_the_ast_cache_key(
    elf_lib: Path, tmp_path: Path, monkeypatch
) -> None:
    """An include directory that reaches the header parse only as an opaque
    ``gcc_option_tokens`` string is invisible to ``extra_includes``' own
    directory-mtime hashing, so without folding it into ``extra_hash_dirs``
    too an edit under that directory would reuse a stale cached AST.

    Exercised over each spelling the token channel accepts, rather than the
    single ``-I <dir>`` shape the original report used: a joined ``-I<dir>``,
    a separated ``-I <dir>``, and an ``-isystem <dir>`` all name a directory
    whose contents the parse reads.
    """
    hdr = tmp_path / "widget.h"
    hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")

    for idx, spelling in enumerate(
        (["-I{dir}"], ["-I", "{dir}"], ["-isystem", "{dir}"])
    ):
        build_inc = tmp_path / f"buildinc{idx}"
        build_inc.mkdir()
        opts: list[str] = []
        for token in spelling:
            opts += ["--compiler-option", token.format(dir=str(build_inc))]

        captured: dict[str, Any] = {}
        _stub_elf_parse(
            monkeypatch,
            AbiSnapshot(library="lib.so", version="1.0", from_headers=True),
            captured,
        )
        _capture_write(monkeypatch)

        result = _run(str(elf_lib), "-H", str(hdr), *opts)
        assert result.exit_code == 0, result.output
        assert build_inc in tuple(captured["extra_hash_dirs"]), (
            spelling,
            captured["extra_hash_dirs"],
        )


# ── full-scope requests reach the parse ─────────────────────────────────────


@pytest.mark.parametrize("fmt", ["elf", "pe"])
def test_dump_include_system_declarations_suppresses_the_streaming_pruner(
    fmt: str, elf_lib: Path, pe_lib: Path, tmp_path: Path, monkeypatch
) -> None:
    """``dump --include-system-declarations`` must suppress the opt-in
    streaming pruner (``dumper_clang_streaming.py``) for the header-AST parse:
    the pruner has no visibility into that flag on its own, so leaving it
    active would silently drop the dependency-header declarations the flag
    explicitly asked to keep (Codex review, PR #840).

    What the retired functions did by opening their own
    ``suppress_streaming_prune()`` scope, ``service.run_dump``'s dependency-
    scope wrapper now does once for every caller
    (``dumper_scoping.py``; its two settings are pinned at that seam in
    ``tests/test_dumper_scoping.py``). What is asserted here is the half that
    only the ``dump`` CLI can be wrong about -- that the flag actually
    reaches it, and that the default really is the unsuppressed one, for
    either binary format.
    """
    from abicheck.dumper_clang_streaming import streaming_prune_suppressed

    binary = elf_lib if fmt == "elf" else pe_lib
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")

    import abicheck.service_dump_native as native

    for flags, expected in (([], False), (["--include-system-declarations"], True)):
        seen: list[bool] = []

        def _fake(*_a: Any, **_k: Any) -> AbiSnapshot:
            seen.append(streaming_prune_suppressed())
            return AbiSnapshot(library=binary.name, version="1.0", from_headers=True)

        monkeypatch.setattr(native, "_run_dump_uncached", _fake)
        _capture_write(monkeypatch)

        assert not streaming_prune_suppressed(), "leaked before the run"
        result = _run(str(binary), "-H", str(hdr), *flags)
        assert result.exit_code == 0, result.output
        assert seen == [expected], (flags, seen)
        assert not streaming_prune_suppressed(), "leaked after the run"


# ── format-specific CLI notices and failure translation ─────────────────────


def test_dump_pe_follow_deps_warns(pe_lib: Path, monkeypatch) -> None:
    """``--follow-deps`` resolves DT_NEEDED, which only ELF has -- the CLI
    says so instead of silently ignoring the flag."""
    _stub_native_parse(monkeypatch, AbiSnapshot(library="foo.dll", version="1.0"))
    _capture_write(monkeypatch)

    result = _run(str(pe_lib), "--follow-deps")
    assert result.exit_code == 0, result.output
    assert "--follow-deps is only supported for ELF binaries" in result.output


@pytest.mark.parametrize(
    "exc",
    [
        AbicheckError("boom"),
        RuntimeError("boom"),
        OSError("boom"),
        ValueError("boom"),
    ],
)
def test_dump_extraction_failure_is_reported_as_a_cli_error(
    elf_lib: Path, tmp_path: Path, monkeypatch, exc: Exception
) -> None:
    """Every extraction failure the pipeline lets through reaches the user as
    a Click error (exit 1) carrying the message, not a traceback -- the
    translation both retired functions performed and
    ``dump_execute.execute_dump_cli_run`` now performs for either format.
    Parameterized over the whole exception set that clause names, since the
    bug class is "one of these escapes the translation", not one instance.
    """
    hdr = tmp_path / "h.h"
    hdr.write_text("struct S { int x; };\n", encoding="utf-8")

    import abicheck.dumper as dumper

    def _raise(**_kwargs: Any) -> AbiSnapshot:
        raise exc

    monkeypatch.setattr(dumper, "dump", _raise)
    _capture_write(monkeypatch)

    result = _run(str(elf_lib), "-H", str(hdr))
    assert result.exit_code == 1, result.output
    assert "boom" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)

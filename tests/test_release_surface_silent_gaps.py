# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A release whose public contract was not read must never read as checked.

Two bug classes, each stated as an invariant over generated inputs rather
than the one reported repro:

* **The release surface parses under the include roots a member dump
  infers.** ``-H`` directories are their own include root for every member
  dump (``header_utils.resolve_inferred_header_roots``); the release-level
  header-only acquisition skipped that inference, so an umbrella header
  writing ``#include <pkg/detail.h>`` failed to parse there *only*, and the
  whole release's export obligations went unchecked.
* **An unresolved surface is an incomplete reconciliation.** It reported
  ``coverage_complete: true`` (the export index's completeness, not the
  reconciliation's) and contributed nothing to any gate, so a run that
  checked no obligation at all exited 0 clean.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from abicheck.compare.bundle_export_index import build_bundle_export_index
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model.release_surface import unresolved_surface
from abicheck.policy.release_contract_reconciliation import (
    reconcile_release,
    reconcile_side,
)
from abicheck.report.release_public_surface import (
    ReleasePublicSurfaceTerms,
    SharedFinding,
    render_release_public_surface_markdown,
)
from abicheck.workflows.release_assurance_members import (
    release_assurance_from_entries,
)
from abicheck.workflows.release_public_surface import ReleaseSurfaceStage
from abicheck.workflows.release_surface_acquisition import (
    SurfaceAcquisitionLedger,
    _with_inferred_header_roots,
)


@dataclass
class _Member:
    elf: ElfMetadata | None


def _member(*exports: str) -> _Member:
    return _Member(
        elf=ElfMetadata(
            soname="",
            needed=[],
            symbols=[ElfSymbol(name=n, is_default=True) for n in exports],
            imports=[],
        )
    )


def _index(side: str, *, complete: bool):
    return build_bundle_export_index(
        side,
        {"liba.so": _member("f"), "libb.so": _member("g")},
        failed_members=None if complete else {"libc.so": "dump failed"},
    )


# -- inferred include roots -------------------------------------------------

_LAYOUTS = [
    ("hdr",),
    ("hdr", "extra"),
    ("a/b/hdr",),
    ("x/hdr", "y/other"),
]


@pytest.mark.parametrize("layout", _LAYOUTS)
@pytest.mark.parametrize("user_includes", [(), ("inc",)])
def test_every_header_dir_is_a_search_root_without_build_context(
    tmp_path: Path, layout: tuple[str, ...], user_includes: tuple[str, ...]
) -> None:
    dirs = [tmp_path / d for d in layout]
    for d in dirs:
        d.mkdir(parents=True)
    user = [tmp_path / u for u in user_includes]
    for u in user:
        u.mkdir()
    includes, ctx = _with_inferred_header_roots(dirs, user, None)
    # The user's -I stay first and untouched; each -H dir is searched.
    assert includes[: len(user)] == user
    searched = {p.resolve() for p in includes}
    assert {d.resolve() for d in dirs} <= searched
    assert ctx is None


@pytest.mark.parametrize("layout", _LAYOUTS)
def test_with_build_context_the_roots_are_deferred_behind_it(
    tmp_path: Path, layout: tuple[str, ...]
) -> None:
    from abicheck.compile_context import CompileContext

    dirs = [tmp_path / d for d in layout]
    for d in dirs:
        d.mkdir(parents=True)
    base = CompileContext(gcc_option_tokens=("-I/build/include", "-DX"))
    includes, ctx = _with_inferred_header_roots(dirs, [], base)
    assert includes == []
    assert isinstance(ctx, CompileContext)
    tail = ctx.gcc_option_tokens[len(base.gcc_option_tokens) :]
    assert (
        ctx.gcc_option_tokens[: len(base.gcc_option_tokens)] == base.gcc_option_tokens
    )
    for d in dirs:
        assert str(d) in tail


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("castxml") is None, reason="needs castxml")
def test_an_umbrella_including_relative_to_its_header_dir_resolves(
    tmp_path: Path,
) -> None:
    """End to end: `-H hdr` whose umbrella writes `#include <pkg/detail.h>`
    (a directory not conventionally named `include`) acquires a surface."""
    from abicheck.model.release_surface import SurfaceAcquisitionIdentity
    from abicheck.workflows.release_surface_acquisition import (
        acquire_release_surface,
    )

    hdr = tmp_path / "hdr"
    (hdr / "pkg").mkdir(parents=True)
    (hdr / "pkg" / "a.h").write_text(
        "#pragma once\n#include <pkg/detail.h>\nint fa(int);\n", encoding="utf-8"
    )
    (hdr / "pkg" / "detail.h").write_text(
        "#pragma once\nint fb(void);\n", encoding="utf-8"
    )
    surface = acquire_release_surface(
        SurfaceAcquisitionIdentity(header_dirs=(str(hdr),), lang="c"),
        "new",
        ledger=SurfaceAcquisitionLedger(),
        headers=[hdr / "pkg" / "a.h", hdr / "pkg" / "detail.h"],
        includes=[],
        header_inputs=[hdr],
        public_headers=[],
        public_header_dirs=[hdr],
    )
    assert surface.resolvable, surface.unresolved_reason
    assert {"fa", "fb"} <= {o.symbol for o in surface.obligations}


# -- an unresolved surface is never a checked one ---------------------------


@pytest.mark.parametrize("index_complete", [True, False])
@pytest.mark.parametrize("side", ["old", "new"])
def test_an_unresolved_surface_is_never_coverage_complete(
    side: str, index_complete: bool
) -> None:
    surface = unresolved_surface(acquisition_key="k", side=side, reason="parse failed")
    rec = reconcile_side(surface, _index(side, complete=index_complete))
    assert rec.surface_resolvable is False
    assert rec.coverage_complete is False
    assert rec.to_dict()["coverage_complete"] is False


@pytest.mark.parametrize(
    "old_ok, new_ok", [(False, False), (True, False), (False, True)]
)
def test_each_unresolved_side_is_warned_and_is_an_assurance_shortfall(
    old_ok: bool, new_ok: bool
) -> None:
    from abicheck.model.release_surface import ReleasePublicSurface

    def surface(side: str, ok: bool):
        if ok:
            return ReleasePublicSurface(acquisition_key="k", side=side, resolvable=True)
        return unresolved_surface(acquisition_key="k", side=side, reason="boom")

    rec = reconcile_release(
        surface("new", new_ok),
        _index("new", complete=True),
        old_surface=surface("old", old_ok),
        old_index=_index("old", complete=True),
    )
    unresolved = {s for s, ok in (("old", old_ok), ("new", new_ok)) if not ok}
    for s in unresolved:
        assert any(
            f"{s} side's public surface could not be acquired" in w
            and "no release-level export obligation was checked" in w
            for w in rec.coverage_warnings
        )
    stage = ReleaseSurfaceStage(rec, SurfaceAcquisitionLedger(), None, None)
    rows = stage.assurance_shortfalls()
    assert {r.name for r in rows} == {
        f"<release public surface: {s}>" for s in unresolved
    }
    assert all(r.status == "partial" and "boom" in r.notes[0] for r in rows)

    # Gates under assurance.require_complete even when every member is clean.
    clean = [
        {"library": "liba.so", "analysis_assurance_status": "complete"},
        {"library": "libb.so", "analysis_assurance_status": "complete"},
    ]
    on = release_assurance_from_entries(
        clean, require_complete=True, release_shortfalls=rows
    )
    assert on.exit_contribution == 1 and on.status == "partial"
    # Additive (ADR-071 D4): without the setting nothing changes.
    off = release_assurance_from_entries(
        clean, require_complete=False, release_shortfalls=rows
    )
    assert off.exit_contribution == 0
    assert not any(m.name.startswith("<release") for m in off.members)


def test_a_stage_that_did_not_run_has_no_shortfall() -> None:
    stage = ReleaseSurfaceStage(None, SurfaceAcquisitionLedger(), None, None)
    assert stage.assurance_shortfalls() == ()


# -- the Markdown section is bounded -----------------------------------------


@pytest.mark.parametrize("count", [0, 1, 49, 50, 51, 500])
@pytest.mark.parametrize("limit", [1, 50])
def test_markdown_lists_are_bounded_and_state_the_remainder(
    count: int, limit: int
) -> None:
    missing = tuple(
        {"symbol": f"m{i}", "description": "d", "cross_source_evolution": "persistent"}
        for i in range(count)
    )
    shared = tuple(
        SharedFinding("k", f"s{i}", None, None, "d", None, ("a", "b"))
        for i in range(count)
    )
    text = render_release_public_surface_markdown(
        ReleasePublicSurfaceTerms(
            sides={"new": {"surface_resolvable": False}},
            missing_exports=missing,
            shared_findings=shared,
            markdown_item_limit=limit,
        )
    )
    shown = min(count, limit)
    assert sum(line.startswith("- `m") for line in text.splitlines()) == shown
    assert sum(line.startswith("- `s") for line in text.splitlines()) == shown
    more = [line for line in text.splitlines() if "more (the full list" in line]
    if count > limit:
        assert (
            more
            == [
                f"- … and {count - limit} more (the full list is in the JSON report, `-o json=...`)"
            ]
            * 2
        )
    else:
        assert more == []


def test_the_inferred_roots_reach_the_parse_without_castxml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unit-lane twin of the castxml test: the parser really receives
    each `-H` directory as a search root."""
    import abicheck.header_only_dump as header_only_dump
    from abicheck.model.release_surface import SurfaceAcquisitionIdentity
    from abicheck.workflows.release_surface_acquisition import (
        acquire_release_surface,
    )

    hdr = tmp_path / "hdr"
    hdr.mkdir()
    (hdr / "a.h").write_text("int fa(int);\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def _spy(**kwargs: object):
        seen.update(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr(header_only_dump, "build_header_only_snapshot", _spy)
    surface = acquire_release_surface(
        SurfaceAcquisitionIdentity(header_dirs=(str(hdr),), lang="c"),
        "new",
        ledger=SurfaceAcquisitionLedger(),
        headers=[hdr / "a.h"],
        includes=[],
        header_inputs=[hdr],
        public_headers=[],
        public_header_dirs=[hdr],
    )
    assert hdr in seen["extra_includes"]  # type: ignore[operator]
    assert surface.resolvable is False and "stop" in (surface.unresolved_reason or "")


def test_roots_on_two_drives_have_no_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    import abicheck.workflows.ownership_request as mod

    def _raise(_paths: object) -> str:
        raise ValueError("different drives")

    monkeypatch.setattr(mod.os.path, "commonpath", _raise)
    assert mod.operand_anchor(["C:/a", "D:/b"]) is None
    assert mod.operand_anchor([]) is None


def test_markdown_states_coverage_warnings_and_acquisitions() -> None:
    text = render_release_public_surface_markdown(
        ReleasePublicSurfaceTerms(
            sides={"new": {"surface_resolvable": False, "coverage_reason": "boom"}},
            coverage_warnings=("no release-level export obligation was checked",),
            acquisition={"acquisitions": 2, "reuses": 1},
        )
    )
    assert "public surface unresolved — boom" in text
    assert "- no release-level export obligation was checked" in text
    assert "Header acquisitions: 2 (reused 1 time(s))." in text


def test_an_incomplete_resolvable_side_keeps_its_own_warning() -> None:
    from abicheck.model.release_surface import ReleasePublicSurface

    rec = reconcile_release(
        ReleasePublicSurface(acquisition_key="k", side="new", resolvable=True),
        _index("new", complete=False),
    )
    assert rec.new.coverage_complete is False
    assert any(
        "reconciliation on the new side is incomplete" in w
        for w in rec.coverage_warnings
    )
    assert not any("could not be acquired" in w for w in rec.coverage_warnings)

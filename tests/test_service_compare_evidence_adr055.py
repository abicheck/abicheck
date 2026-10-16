"""Unit tests for abicheck.service_compare_evidence and evidence-depth gating in run_compare_request (ADR-055)."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.checker_types import DiffResult
from abicheck.errors import SnapshotError, ValidationError
from abicheck.model import AbiSnapshot
from abicheck.serialization import save_snapshot
from abicheck.service_compare_pipeline import (
    run_compare_request,
)
from abicheck.workflows.request_inputs import (
    InputSpec,
)
from abicheck.workflows.service_requests import (
    CompareRequest,
)


class TestCompareRequestAdr055Evidence:
    """ADR-055 D1: CompareRequest/InputSpec's depth/sources/build_info/compile/
    frontend_context/dump_manifest/public_header_dirs fields, wired into
    run_compare_request via service_compare_evidence.py."""

    def _make_snap_file(self, tmp_path, name, version):
        snap = AbiSnapshot(library=name, version=version)
        p = tmp_path / f"{name}_{version}.json"
        save_snapshot(snap, p)
        return p

    def _keep_raw_evidence_on_snapshot_operands(self, monkeypatch):
        """These cells pin how a side's ``sources``/``build_info`` are wired
        into extraction, using stored-snapshot operands for speed. A real run
        ignores a raw tree on a snapshot operand (nothing to re-extract it
        from -- ``test_compare_request_inline_evidence.py`` pins that rule),
        so it is switched off here to reach the wiring."""
        monkeypatch.setattr(
            "abicheck.service_compare_evidence.without_unusable_raw_evidence",
            lambda request, notify=None: request,
        )

    def _spy_resolve_input(self, monkeypatch):
        import abicheck.workflows.input_resolution as _input_resolution
        from abicheck import service as service_mod

        calls: list[dict] = []
        original_resolve = service_mod.resolve_input

        def _spy(path, headers, includes, version, lang, **kwargs):
            calls.append({"path": path, "headers": headers, **kwargs})
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(_input_resolution, "resolve_input", _spy)
        return calls

    def test_depth_binary_clears_headers(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        old_h = tmp_path / "old.h"
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, headers=[old_h]),
            new=InputSpec.of(new_p),
            depth="binary",
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["headers"] == []

    def test_public_header_dirs_unioned_into_resolve_input(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        extra_dir = tmp_path / "extra_public"
        extra_dir.mkdir()
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, public_header_dirs=[extra_dir]),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert extra_dir in calls_by_path[old_p]["public_header_dirs"]

    def test_dump_manifest_forwarded_to_resolve_input(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)
        sentinel = object()

        request = CompareRequest(
            old=InputSpec.of(old_p, dump_manifest=sentinel),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["dump_manifest"] is sentinel

    def test_depth_binary_also_clears_dump_manifest(self, tmp_path, monkeypatch):
        """Codex review (P2): depth="binary" clears headers but must also
        clear dump_manifest -- otherwise the manifest still drives its own
        multi-TU L2 header extraction despite the caller explicitly
        requesting binary-only evidence."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)
        sentinel = object()

        request = CompareRequest(
            old=InputSpec.of(old_p, dump_manifest=sentinel),
            new=InputSpec.of(new_p),
            depth="binary",
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["dump_manifest"] is None

    def test_per_side_compile_override_wins_over_pair_compile(
        self, tmp_path, monkeypatch
    ):
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)
        side_compile = CompileContext(sysroot=Path("/custom-sysroot"))

        request = CompareRequest(
            old=InputSpec.of(old_p, compile=side_compile),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["compile"] is side_compile

    def test_frontend_context_default_fills_missing_side_compile(
        self, tmp_path, monkeypatch
    ):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p),
            new=InputSpec.of(new_p),
            frontend_context="device",
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["compile"].frontend_context == "device"
        assert calls_by_path[new_p]["compile"].frontend_context == "device"

    def test_frontend_context_case_is_normalized(self, tmp_path, monkeypatch):
        """Codex review (P2): validate() accepts frontend_context case-
        insensitively, but every real consumer compares against the
        lowercase "host"/"device" literals -- an accepted "DEVICE" must
        still normalize to "device", not silently behave as neither."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p),
            new=InputSpec.of(new_p),
            frontend_context="DEVICE",
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["compile"].frontend_context == "device"

    def test_unrelated_side_override_still_picks_up_device_default(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P2, second round): CompileContext.frontend_context
        has no "unset" representation, so an unrelated per-side override
        (e.g. only a sysroot) that never touches frontend_context must still
        pick up the request-level frontend_context default -- it must not be
        silently mistaken for an explicit "host" pin just because "host" is
        also the field's own default value."""
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, compile=CompileContext(sysroot=Path("/sysroot"))),
            new=InputSpec.of(new_p),
            frontend_context="device",
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["compile"].sysroot == Path("/sysroot")
        assert calls_by_path[old_p]["compile"].frontend_context == "device"
        assert calls_by_path[new_p]["compile"].frontend_context == "device"

    def test_sources_triggers_embed_build_source(self, tmp_path, monkeypatch):
        self._keep_raw_evidence_on_snapshot_operands(monkeypatch)
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        embed_calls: list[dict] = []

        def _fake_embed(snap, **kwargs):
            embed_calls.append(kwargs)

        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source", _fake_embed
        )
        # The real diffing/pack-loading (prepare_embedded_build_source) is
        # exercised by the CLI-path tests already; here we're only asserting
        # that run_compare_request wires sources/collect_mode into
        # embed_build_source, so stub the diff step to a no-op.
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        assert len(embed_calls) == 1
        assert embed_calls[0]["sources"] == src_dir
        assert embed_calls[0]["collect_mode"] != "off"

    def test_no_evidence_fields_skips_embed_build_source(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )

        request = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))
        run_compare_request(request)
        assert embed_calls == []

    def test_embedded_evidence_is_diffed_into_extra_changes(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P1): embed_build_source alone only writes
        snap.build_source -- the checker never reads it directly, so it must
        also be diffed (prepare_embedded_build_source) and forwarded to
        compare_snapshots as extra_changes, or a source-only ABI change would
        silently produce an ordinary artifact-only compatible verdict."""
        import abicheck.workflows.compare_policy as compare_policy_mod
        from abicheck.checker_policy import ChangeKind
        from abicheck.model.change import Change

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source", lambda snap, **kwargs: None
        )
        sentinel_change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="source_only_fn",
            description="source-only ABI break injected by test",
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: ([sentinel_change], [], {}, [sentinel_change]),
        )

        captured = {}
        # classify_compare_pair (service_compare_pipeline.py) imports
        # compare_snapshots from its real owner (workflows.compare_policy,
        # ADR-061 gap A) rather than the flat abicheck.service facade, so the
        # patch target follows it.
        original_compare_snapshots = compare_policy_mod.compare_snapshots

        def _spy_compare_snapshots(old, new, *a, **kw):
            captured["extra_changes"] = kw.get("extra_changes")
            return original_compare_snapshots(old, new, *a, **kw)

        monkeypatch.setattr(
            compare_policy_mod, "compare_snapshots", _spy_compare_snapshots
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir), new=InputSpec.of(new_p)
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert captured["extra_changes"] == [sentinel_change]
        assert sentinel_change in result.changes

    def test_embedded_evidence_not_reloaded_as_out_of_band_pack(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P1): request.old/new.sources/build_info were already
        collected inline into old/new.build_source by embed_build_source, so
        prepare_embedded_build_source's own *_build_info/*_sources params
        (its out-of-band pack-directory override, distinct from the already-
        embedded facts) must be None here -- passing the same raw path again
        would make _resolve_side_pack try to reload it as an evidence pack
        (expecting a manifest.json) and raise ClickException."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source", lambda snap, **kwargs: None
        )
        captured_args = {}

        def _fake_prepare(old, new, collect_mode, extra_changes, *rest, **kwargs):
            captured_args["rest"] = rest
            return extra_changes, [], {}, []

        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            _fake_prepare,
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir), new=InputSpec.of(new_p)
        )
        run_compare_request(request)
        assert captured_args["rest"] == (None, None, None, None)

    def test_extractor_matches_effective_frontend(self, tmp_path, monkeypatch):
        """Codex review (P2): embed_build_source's extractor must match the
        same eff_backend resolve_input/run_dump use internally (an explicit
        compile.frontend wins over the bare header_backend), not silently
        default to "auto" while L2 used a different frontend."""
        self._keep_raw_evidence_on_snapshot_operands(monkeypatch)
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(
                old_p, sources=src_dir, compile=CompileContext(frontend="castxml")
            ),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        assert embed_calls[0]["extractor"] == "castxml"

    def test_default_auto_frontend_replays_l4_with_clang(self, tmp_path, monkeypatch):
        """The default frontend="auto" replays L4 with clang, L4's documented
        default -- not castxml, L2's ``auto``, which records no macros, so a
        `#define` change under ``depth="source"`` went unseen (the
        evidence-depth workflow example). Still forwarded resolved, never as
        the literal "auto" (Codex review, P2, second round);
        ``tests/test_source_replay_extractor.py`` pins the full rule."""
        monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
        self._keep_raw_evidence_on_snapshot_operands(monkeypatch)
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir), new=InputSpec.of(new_p)
        )
        run_compare_request(request)
        assert embed_calls[0]["extractor"] == "clang"

    def test_uppercase_auto_compile_frontend_is_not_treated_as_explicit(
        self, monkeypatch
    ):
        """Codex review, fresh evidence: `compile.frontend="AUTO"` is an
        accepted spelling (validated case-insensitively,
        `api_types.frontend_value_errors`), but `effective_frontend`'s own
        "is this an explicit override" check used to be case-sensitive
        (`compile.frontend != "auto"`) -- so "AUTO" read as an explicit
        override instead of the no-op it means, and its own `_resolve_header_
        backend` re-resolution then re-read `ABICHECK_AST_FRONTEND` live
        instead of honoring the already-resolved `header_backend` argument
        (the exact mechanism `service_dump_pipeline.ResolvedDumpRequest.
        effective_header_backend`'s pin relies on not happening)."""
        from abicheck.compile_context import CompileContext
        from abicheck.service_compare_evidence import effective_frontend

        monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
        assert effective_frontend(CompileContext(frontend="AUTO"), "clang") == "clang"
        assert effective_frontend(CompileContext(frontend="Auto"), "clang") == "clang"
        # An explicit non-auto override still wins, case as given.
        assert (
            effective_frontend(CompileContext(frontend="castxml"), "clang") == "castxml"
        )

    def test_public_headers_forwarded_to_embed_build_source(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P1): omitting the side's public-header roots from
        embed_build_source leaves source replay's own public-header set
        empty, so the L4 extractor can classify every declaration as non-API
        and emit an empty surface, silently hiding source-only breaks."""
        self._keep_raw_evidence_on_snapshot_operands(monkeypatch)
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        header = tmp_path / "api.h"
        header.write_text("")
        header_dir = tmp_path / "include"
        header_dir.mkdir()

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(
                old_p,
                sources=src_dir,
                headers=[header],
                public_header_dirs=[header_dir],
            ),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        assert str(header) in embed_calls[0]["public_headers"]
        assert str(header_dir) in embed_calls[0]["public_header_dirs"]

    def test_dump_manifest_public_roots_forwarded_to_embed_build_source(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P1, second round): a dump_manifest replaces
        `headers` entirely, so public_headers/public_header_dirs (both
        derived from `headers`) stay empty for a manifest-driven request --
        the manifest's own roots (public_header_paths/public_header_dirs)
        must still reach source replay, or it can classify the manifest's
        API declarations as non-public and omit source-only breaks."""
        self._keep_raw_evidence_on_snapshot_operands(monkeypatch)
        from types import SimpleNamespace

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        manifest = SimpleNamespace(
            roots=[],
            public_header_paths=["/proj/api.h"],
            public_header_dirs=["/proj/include"],
            translation_units=[],
        )

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir, dump_manifest=manifest),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        assert "/proj/api.h" in embed_calls[0]["public_header_dirs"]
        assert "/proj/include" in embed_calls[0]["public_header_dirs"]

    def test_dump_manifest_project_owned_includes_not_forwarded_to_replay(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P1, third round): a TU's project_owned include
        directories are private sibling/support roots used only to keep
        resolve_dependency_scope from misclassifying them as a toolchain
        dependency -- not declared public API surface. Forwarding them into
        L4 source replay's own public-header set would make the extractors
        treat every declaration under a private support dir as API-relevant,
        false-flagging private-header churn as a source break."""
        self._keep_raw_evidence_on_snapshot_operands(monkeypatch)
        from types import SimpleNamespace

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        private_inc = SimpleNamespace(path="/proj/private_support", project_owned=True)
        tu = SimpleNamespace(includes=[private_inc], forced_includes=())
        manifest = SimpleNamespace(
            roots=[],
            public_header_paths=[],
            public_header_dirs=[],
            translation_units=[tu],
        )

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir, dump_manifest=manifest),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        assert "/proj/private_support" not in embed_calls[0]["public_header_dirs"]

    def test_hybrid_frontend_rejected_for_explicit_source_depth(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P1): mirror cli.py's own --depth source +
        --ast-frontend hybrid UsageError -- L4 source-ABI replay has no
        dual-backend hybrid extractor, so this combination must be rejected
        rather than silently reach an artifact-only verdict."""
        self._keep_raw_evidence_on_snapshot_operands(monkeypatch)
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        request = CompareRequest(
            old=InputSpec.of(
                old_p, sources=src_dir, compile=CompileContext(frontend="hybrid")
            ),
            new=InputSpec.of(new_p),
            depth="source",
        )
        with pytest.raises(ValidationError, match="hybrid"):
            run_compare_request(request)

    def test_android_frontend_rejected_for_raw_source_tree(self, tmp_path, monkeypatch):
        """Codex review (P2): frontend="android" combined with a genuine raw
        source tree must be rejected -- embed_build_source's inline
        collection pipeline has no real Android extractor and would
        otherwise silently substitute Clang."""
        self._keep_raw_evidence_on_snapshot_operands(monkeypatch)
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir),
            new=InputSpec.of(new_p),
            frontend="android",
            has_sources=True,
        )
        with pytest.raises(ValidationError, match="android"):
            run_compare_request(request)

    def test_android_frontend_allowed_for_prebuilt_evidence_pack(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P2, second round): a prebuilt BuildSourcePack/inputs
        pack is loaded as pre-captured facts by embed_build_source -- no
        extractor ever runs for it, so frontend="android" combined with a
        genuine pack directory must be allowed, not rejected."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        # Real manifest, not a patched predicate: is_pack_dir only reads it.
        (pack_dir / "manifest.json").write_text('{"build_source_pack_version": 1}')

        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source", lambda snap, **kwargs: None
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=pack_dir),
            new=InputSpec.of(new_p),
            frontend="android",
            has_sources=True,
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)

    def test_android_frontend_allowed_with_build_info_only(self, tmp_path, monkeypatch):
        """build_info alone never drives L4 extraction (only L3 compile-DB
        resolution), so it plays no part in the android/hybrid extractor
        rejection -- must be allowed even as a raw (non-pack) directory."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        build_dir = tmp_path / "build"
        build_dir.mkdir()

        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source", lambda snap, **kwargs: None
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, build_info=build_dir),
            new=InputSpec.of(new_p),
            frontend="android",
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)

    def test_hybrid_frontend_allowed_without_explicit_source_depth(
        self, tmp_path, monkeypatch
    ):
        """The CLI's own implicit-default case (no explicit --depth) is
        allowed to honestly degrade -- only an *explicit* depth="source"
        request is rejected for hybrid."""
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source", lambda snap, **kwargs: None
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(
                old_p, sources=src_dir, compile=CompileContext(frontend="hybrid")
            ),
            new=InputSpec.of(new_p),
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)

    def test_manifest_forced_includes_feed_pair_wide_cxx20_detection(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P2): a dump_manifest replaces `headers` (empty
        tuple), so its own translation_units[].forced_includes must feed the
        same pair-wide C++20 heuristic or a manifest-only side's real C++20
        signal goes undetected, letting the pair disagree on dialect."""
        from types import SimpleNamespace

        from abicheck import dry_run_estimate

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        forced = tmp_path / "concepts.h"
        tu = SimpleNamespace(forced_includes=(forced,))
        manifest = SimpleNamespace(translation_units=[tu])

        captured = {}

        def _fake_override(lang, old_headers, new_headers, *a, **k):
            captured["old_headers"] = list(old_headers)
            return None

        # ADR-055 D1: the pair-wide scan runs in the shared
        # `service_compare_pipeline`, which imports this helper from the module
        # that defines it rather than through `service`'s re-export -- so the
        # spy belongs on `dry_run_estimate`, where it lives.
        monkeypatch.setattr(
            dry_run_estimate, "pair_wide_cxx20_std_override", _fake_override
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, dump_manifest=manifest), new=InputSpec.of(new_p)
        )
        run_compare_request(request)
        assert forced in captured["old_headers"]

    def test_binary_depth_clears_public_headers_for_scope_fingerprint(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P2): depth="binary" clears the actual header parse
        but a headerless dump still fingerprints public_headers/
        public_header_dirs for scope_fingerprint -- old/new sides with
        differing header lists must not spuriously ScopeMismatchError
        despite the request explicitly selecting binary-only evidence."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        old_h = tmp_path / "old.h"
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, headers=[old_h]),
            new=InputSpec.of(new_p),
            depth="binary",
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["public_headers"] == []
        assert calls_by_path[old_p]["public_header_dirs"] == []

    def test_request_frontend_case_is_normalized_for_extractor(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P2): request.frontend="CASTXML" validates case-
        insensitively, but the extractor forwarded to source replay must be
        lowercase -- _make_source_extractor only recognizes lowercase
        frontend names and silently falls back to Clang otherwise, making L2
        and L4 use different frontends."""
        self._keep_raw_evidence_on_snapshot_operands(monkeypatch)
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        embed_calls: list[dict] = []
        monkeypatch.setattr(
            "abicheck.buildsource.embed.embed_build_source",
            lambda snap, **kwargs: embed_calls.append(kwargs),
        )
        monkeypatch.setattr(
            "abicheck.buildsource.evidence_report.prepare_embedded_build_source",
            lambda *a, **k: (None, [], {}, []),
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=src_dir),
            new=InputSpec.of(new_p),
            frontend="CASTXML",
        )
        run_compare_request(request)
        assert embed_calls[0]["extractor"] == "castxml"

    def test_depth_is_case_insensitive_end_to_end(self, tmp_path, monkeypatch):
        """Codex review (P2): CompareRequest.validate() accepts a case-
        insensitive depth (e.g. "BUILD"), so the internal EvidenceDepth
        construction must not raise ValueError for it. The depth-satisfaction
        gate (a later Codex review) is mocked to "satisfied" here so this
        test stays scoped to its own original concern (case handling), not
        the separate depth-reached check covered by
        TestCompareRequestDepthSatisfaction below."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label", lambda *a, **k: "build"
        )

        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="BUILD"
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)

    def test_pair_wide_dialect_merges_into_unrelated_side_compile(
        self, tmp_path, monkeypatch
    ):
        """Codex review (P2): a side_compile override unrelated to the C++
        dialect (e.g. only a sysroot) must not silently discard the pair-wide
        C++20 heuristic's override for that side -- it should be merged in
        unless the side already pins its own explicit standard."""
        from abicheck import dry_run_estimate
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")

        # ADR-055 D1: spied on `dry_run_estimate` (where it is defined) rather than
        # `service` (which only re-exports it) -- the shared compare pipeline
        # imports it from the defining module.
        monkeypatch.setattr(
            dry_run_estimate,
            "pair_wide_cxx20_std_override",
            lambda *a, **k: ("-std=gnu++20",),
        )
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, compile=CompileContext(sysroot=Path("/sysroot"))),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        old_compile = calls_by_path[old_p]["compile"]
        assert old_compile.sysroot == Path("/sysroot")
        assert "-std=gnu++20" in old_compile.gcc_option_tokens

    def test_headers_alongside_dump_manifest_rejected(self, tmp_path):
        """Codex review: dump_manifest replaces `headers` for the primary
        AST -- dumper_manifest.resolve_header_ast_result ignores `headers`
        entirely once a manifest is given -- but this method still forwarded
        a non-empty `headers` into provenance tagging and dialect detection
        alongside it, mixing two declared surfaces. Mirrors the CLI's own
        --dump-manifest/-H UsageError (cli_compare_helpers.py)."""
        from abicheck.dump_manifest import DumpManifest, TranslationUnit

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        old_h = tmp_path / "old.h"
        old_h.write_text("void f();\n")
        dm = DumpManifest(
            base_dir=tmp_path, translation_units=(TranslationUnit(name="old.h"),)
        )

        request = CompareRequest(
            old=InputSpec.of(old_p, headers=[old_h], dump_manifest=dm),
            new=InputSpec.of(new_p),
        )
        with pytest.raises(ValidationError, match="mutually exclusive"):
            run_compare_request(request)

    def test_dump_manifest_alone_is_not_rejected(self, tmp_path, monkeypatch):
        """A dump_manifest with no ordinary headers on that side must not be
        caught by the new mutual-exclusivity guard above.

        CodeRabbit review: the previous try/except let this test pass while
        asserting nothing if run_compare_request raised no exception at all
        -- capture any ValidationError message unconditionally instead, so
        the guard-specific claim is always actually checked."""
        from abicheck.dump_manifest import DumpManifest, TranslationUnit

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        dm = DumpManifest(
            base_dir=tmp_path, translation_units=(TranslationUnit(name="old.h"),)
        )
        self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, dump_manifest=dm),
            new=InputSpec.of(new_p),
        )
        # No ValidationError raised for the mutual-exclusivity guard itself;
        # it may still fail later trying to actually resolve the manifest's
        # (nonexistent) header, which is unrelated to this guard.
        messages: list[str] = []
        try:
            run_compare_request(request)
        except ValidationError as exc:
            messages.append(str(exc))
        assert all("mutually exclusive" not in m for m in messages)

    def test_per_side_frontend_context_case_is_normalized(self, tmp_path, monkeypatch):
        """Codex review: request.frontend_context is normalized to lowercase,
        but a per-side InputSpec.compile.frontend_context passed straight
        through unchanged -- an accepted case-insensitive spelling like
        "DEVICE" then compared unequal to the lowercase literal every real
        consumer (e.g. sycl_context.py) checks against."""
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        calls = self._spy_resolve_input(monkeypatch)

        request = CompareRequest(
            old=InputSpec.of(old_p, compile=CompileContext(frontend_context="DEVICE")),
            new=InputSpec.of(new_p),
        )
        run_compare_request(request)
        calls_by_path = {c["path"]: c for c in calls}
        assert calls_by_path[old_p]["compile"].frontend_context == "device"

    def test_per_side_invalid_frontend_context_rejected(self, tmp_path):
        """A per-side compile.frontend_context bypassed api_types.py's enum
        check entirely (only the request-level default was validated) --
        validate() must now catch it too."""
        from abicheck.compile_context import CompileContext

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")

        request = CompareRequest(
            old=InputSpec.of(old_p, compile=CompileContext(frontend_context="bogus")),
            new=InputSpec.of(new_p),
        )
        errors = request.validation_errors()
        assert any("bogus" in e and "old" in e for e in errors)
        with pytest.raises(ValidationError, match="bogus"):
            run_compare_request(request)

    def test_malformed_evidence_pack_raises_snapshot_error_not_click_exception(
        self, tmp_path
    ):
        """Codex review: a malformed evidence pack (InputSpec.sources/build_info)
        raised click.ClickException deep inside embed_build_source's
        _load_pack_or_raise -- a CLI-framework exception this Tier-2 API's
        documented ValidationError/SnapshotError contract has no place for."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        bad_pack = tmp_path / "bad_pack"
        bad_pack.mkdir()
        # is_pack_dir() treats an unparseable manifest.json as a (corrupt) pack
        # so downstream loading raises loudly instead of silently collecting.
        (bad_pack / "manifest.json").write_text("{not valid json")

        request = CompareRequest(
            old=InputSpec.of(old_p, sources=bad_pack), new=InputSpec.of(new_p)
        )
        with pytest.raises(SnapshotError, match="Invalid evidence pack"):
            run_compare_request(request)

    def test_embedded_evidence_diffing_is_quiet(self, tmp_path, capsys):
        """Codex review: prepare_embedded_build_source's coverage-table echoes
        and attach_evidence_metrics' timing-summary echo previously printed
        CLI tables to stderr unconditionally -- polluting a non-CLI caller's
        stream with output it has no way to suppress. run_compare_request calls
        the engine (buildsource.evidence_report) directly and hands it no
        output sink, so nothing reaches either stream."""
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")

        request = CompareRequest(
            old=InputSpec.of(old_p, build_info=tmp_path), new=InputSpec.of(new_p)
        )
        run_compare_request(request)
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == ""


class TestCompareRequestDepthSatisfaction:
    """`compare`'s depth-shortfall contract at the typed-API boundary.

    Originally (Codex review, P1) this pinned a hard `ValidationError` from
    `run_compare_request`, mirroring `dump`'s own
    `check_requested_depth_satisfied`. **PR #1195 changed that contract**:
    a `compare` shortfall is ADR-064's exit-7 axis, *recorded* on the result
    (`policy.depth_evidence_contract`), never raised -- because the raise
    lived on only one of `compare`'s resolution paths and made the same
    comparison exit differently depending on which front end reached it.

    A pre-serialized snapshot side is carved out entirely (this run never
    extracted it), and only `build`/`source` can be outrun at all -- the
    liveness half is pinned across every side/liveness combination by
    `tests/test_depth_evidence_contract_liveness.py`. Still monkeypatches
    `gated_source_label` to simulate reached vs. not-reached without a real
    compile database or source tree.
    """

    def _make_snap_file(self, tmp_path, name, version):
        from abicheck.model import AbiSnapshot
        from abicheck.serialization import save_snapshot

        path = tmp_path / f"{name}_{version}.json"
        save_snapshot(AbiSnapshot(library=name, version=version), path)
        return path

    def test_depth_not_reached_is_recorded_not_raised(self, tmp_path, monkeypatch):
        """The behaviour change itself: no `ValidationError` any more.

        Both operands here are stored snapshots, so this also pins the
        live-only carve-out -- the axis is not merely unraised, it is
        correctly *not set*, because neither side was extracted by this run.
        """
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label",
            lambda *a, **k: "build",
        )

        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="source"
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)
        assert result.evidence_contract_error is False, result

    def test_depth_reached_passes(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label",
            lambda *a, **k: "source",
        )

        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="source"
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)

    def test_depth_binary_always_satisfied(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label",
            lambda *a, **k: "binary",
        )

        request = CompareRequest(
            old=InputSpec.of(old_p), new=InputSpec.of(new_p), depth="binary"
        )
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)

    def test_no_depth_skips_the_gate(self, tmp_path, monkeypatch):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        monkeypatch.setattr(
            "abicheck.evidence_depth.gated_source_label",
            lambda *a, **k: "binary",
        )

        request = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))
        result, _, _ = run_compare_request(request).as_tuple()
        assert isinstance(result, DiffResult)

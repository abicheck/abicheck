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

"""Tests for the public ``abicheck.appcompat`` facade (ADR-005).

End-to-end/public-API tests only: the facade's exports, the standalone
``check_appcompat`` orchestrator (patched at the dump/compare boundary), and
the ADR-057 consumer-impact enrichment helpers. Layered tests live next to
their owners:

* ``tests/unit/extract/test_consumer_imports.py`` -- application import parsing
* ``tests/unit/extract/test_library_export_facts.py`` -- library export facts
* ``tests/unit/policy/test_consumer_requirements*.py`` -- pure evaluation
* ``tests/unit/workflows/test_consumer_scope.py`` -- scoping workflow
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import abicheck.appcompat as appcompat
from abicheck.appcompat import (
    AppCompatResult,
    AppRequirements,
    ConsumerImportFacts,
    LibraryExportFacts,
    check_against,
    check_appcompat,
    parse_app_requirements,
)
from abicheck.checker import Change, DiffResult
from abicheck.checker_policy import ChangeKind, ReachabilityState, Verdict
from abicheck.model.consumer_spec import ConsumerUnreadableError
from abicheck.workflows.consumer_scope import scope_diff_to_consumer_facts

# ---------------------------------------------------------------------------
# Public data structures and facade surface
# ---------------------------------------------------------------------------


class TestDataStructures:
    def test_app_requirements_defaults(self):
        reqs = AppRequirements()
        assert reqs.needed_libs == []
        assert reqs.undefined_symbols == set()
        assert reqs.required_versions == {}

    def test_app_requirements_populated(self):
        reqs = AppRequirements(
            needed_libs=["libfoo.so.1", "libc.so.6"],
            undefined_symbols={"foo_init", "foo_process"},
            required_versions={"FOO_1.0": "libfoo.so.1"},
        )
        assert len(reqs.needed_libs) == 2
        assert "foo_init" in reqs.undefined_symbols
        assert reqs.required_versions["FOO_1.0"] == "libfoo.so.1"

    def test_app_compat_result_defaults(self):
        result = AppCompatResult(
            app_path="/usr/bin/myapp", old_lib_path="old.so", new_lib_path="new.so"
        )
        assert result.verdict == Verdict.COMPATIBLE
        assert result.symbol_coverage == 100.0
        assert result.missing_symbols == []
        assert result.breaking_for_app == []
        assert result.unreadable is False

    @pytest.mark.parametrize(
        "kwargs, verdict",
        [
            ({"missing_symbols": ["foo_init"]}, Verdict.BREAKING),
            ({"missing_versions": ["FOO_1.0"]}, Verdict.BREAKING),
            ({"required_symbol_count": 5}, Verdict.COMPATIBLE),
        ],
    )
    def test_result_carries_verdict(self, kwargs, verdict):
        result = AppCompatResult(
            app_path="/usr/bin/myapp",
            old_lib_path="old.so",
            new_lib_path="new.so",
            verdict=verdict,
            **kwargs,
        )
        assert result.verdict == verdict


class TestFacadeSurface:
    def test_all_names_resolve_and_no_private_names(self):
        assert set(appcompat.__all__) == {
            "AppCompatResult",
            "AppRequirements",
            "ConsumerImportFacts",
            "LibraryExportFacts",
            "PluginHostContractResult",
            "check_against",
            "check_appcompat",
            "check_plugin_host_contract",
            "parse_app_requirements",
            "scope_diff_to_app",
            "scope_diff_to_required_symbols",
            "uncovered_missing_symbols",
        }
        for name in appcompat.__all__:
            assert getattr(appcompat, name) is not None
        assert not [n for n in vars(appcompat) if n.startswith("_") and n[1] != "_"]


# ---------------------------------------------------------------------------
# parse_app_requirements / check_against on real files (public API)
# ---------------------------------------------------------------------------


class TestParseAppRequirements:
    def test_unknown_format_raises(self, tmp_path):
        f = tmp_path / "unknown.bin"
        f.write_bytes(b"\x00\x00\x00\x00")
        with pytest.raises(ValueError, match="Cannot detect binary format"):
            parse_app_requirements(f, "libfoo.so")

    def test_corrupt_elf_raises_unreadable(self, tmp_path):
        """Recognised format, unreadable import table: an error, never an
        empty requirement set that would read as "requires nothing"."""
        f = tmp_path / "app.elf"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)
        with pytest.raises(
            ConsumerUnreadableError, match="ELF import table unreadable"
        ):
            parse_app_requirements(f, "libfoo.so")

    def test_check_against_unknown_app_raises(self, tmp_path):
        app = tmp_path / "app"
        app.write_bytes(b"\x00\x00\x00\x00")
        with pytest.raises(ValueError, match="Cannot detect binary format"):
            check_against(app, tmp_path / "new.so")


# ---------------------------------------------------------------------------
# check_appcompat: standalone orchestrator, patched at dump/compare boundary
# ---------------------------------------------------------------------------

_STANDALONE = "abicheck.workflows.consumer_scope_standalone"


def _boundary(diff, scoped=None, fmt="elf"):
    """Patches for the dump/compare boundary plus the delegated scoping."""
    return (
        patch(
            "abicheck.workflows.input_resolution.detect_binary_format", return_value=fmt
        ),
        patch("abicheck.workflows.dump.native.run_dump", return_value=MagicMock()),
        patch("abicheck.workflows.compare_policy.compare_snapshots", return_value=diff),
        patch(
            f"{_STANDALONE}.scope_diff_to_app",
            return_value=scoped
            or AppCompatResult(app_path="app", old_lib_path="o", new_lib_path="n"),
        ),
    )


def _paths(tmp_path):
    return tmp_path / "app", tmp_path / "old.so", tmp_path / "new.so"


def _run_dump_calls(tmp_path, **kwargs):
    diff = DiffResult(old_version="1", new_version="2", library="libfoo")
    detect, run_dump, compare, scope = _boundary(diff)
    with detect, run_dump as mock_run_dump, compare, scope:
        check_appcompat(*_paths(tmp_path), **kwargs)
    assert mock_run_dump.call_count == 2
    return mock_run_dump.call_args_list


class TestCheckAppcompat:
    def test_unrecognised_binary_format_raises(self, tmp_path):
        """check_appcompat resolves each side's format itself (T5) ahead of
        run_dump -- an unresolvable format must raise, not reach run_dump
        with binary_fmt=None."""
        from abicheck.errors import ValidationError

        with (
            patch(
                "abicheck.workflows.input_resolution.detect_binary_format",
                return_value=None,
            ),
            pytest.raises(ValidationError, match="Unrecognised binary format"),
        ):
            check_appcompat(*_paths(tmp_path))

    def test_headers_passed_as_public_headers(self, tmp_path):
        """-H/--header is a public header list: the per-side paths must reach
        run_dump() as public_headers."""
        old_hdr, new_hdr = tmp_path / "old.h", tmp_path / "new.h"
        old_call, new_call = _run_dump_calls(
            tmp_path, old_headers=[old_hdr], new_headers=[new_hdr]
        )
        assert old_call.kwargs["public_headers"] == [old_hdr]
        assert new_call.kwargs["public_headers"] == [new_hdr]

    def test_both_dumps_leave_include_dependencies_at_default(self, tmp_path):
        """run_dump's wrapper defaults include_dependencies to True (Codex
        review, PR #840) -- check_appcompat must not override it."""
        for call in _run_dump_calls(tmp_path):
            assert "include_dependencies" not in call.kwargs

    def test_includes_passed_as_public_include_search_dirs(self, tmp_path):
        """Explicit per-side -I lists reach run_dump()'s
        public_include_search_dirs (Codex review, PR #839 round 7)."""
        old_inc, new_inc = tmp_path / "old_include", tmp_path / "new_include"
        old_call, new_call = _run_dump_calls(
            tmp_path,
            old_headers=[tmp_path / "old.h"],
            new_headers=[tmp_path / "new.h"],
            old_includes=[old_inc],
            new_includes=[new_inc],
        )
        assert old_call.kwargs["public_include_search_dirs"] == [old_inc]
        assert new_call.kwargs["public_include_search_dirs"] == [new_inc]

    def test_shared_headers_and_includes_fall_back_to_both_sides(self, tmp_path):
        hdr, inc = tmp_path / "foo.h", tmp_path / "include"
        for call in _run_dump_calls(tmp_path, headers=[hdr], includes=[inc]):
            assert call.kwargs["public_headers"] == [hdr]
            assert call.kwargs["public_include_search_dirs"] == [inc]

    def test_lang_c(self, tmp_path):
        """lang='c' reaches run_dump()'s positional `lang` slot unchanged."""
        for call in _run_dump_calls(tmp_path, lang="c"):
            assert call.args[5] == "c"

    @pytest.mark.parametrize(
        "required, exports, changes, verdict, missing",
        [
            (
                {"foo_init", "foo_process"},
                {"foo_init", "foo_process"},
                [],
                Verdict.COMPATIBLE,
                [],
            ),
            (
                {"foo_init", "foo_gone"},
                {"foo_init"},
                [],
                Verdict.BREAKING,
                ["foo_gone"],
            ),
            (
                {"foo_init"},
                {"foo_init"},
                [ChangeKind.FUNC_REMOVED],
                Verdict.BREAKING,
                [],
            ),
            (
                {"foo_init"},
                {"foo_init", "bar_new"},
                [ChangeKind.FUNC_ADDED],
                Verdict.COMPATIBLE,
                [],
            ),
            (set(), {"foo_init"}, [], Verdict.NO_CHANGE, []),
        ],
    )
    def test_delegates_compare_diff_to_scoping_and_closes_ledger(
        self, tmp_path, required, exports, changes, verdict, missing
    ):
        """The diff check_appcompat computed is the one it scopes (with the
        dumped OLD snapshot for the ADR-057 join), and the scoped verdict is
        returned unchanged after the single closing ledger call."""
        app, old_lib, new_lib = _paths(tmp_path)
        symbol = {ChangeKind.FUNC_REMOVED: "foo_init", ChangeKind.FUNC_ADDED: "bar_new"}
        diff = DiffResult(
            old_version="1",
            new_version="2",
            library="libfoo",
            changes=[
                Change(kind=k, symbol=symbol[k], description="d") for k in changes
            ],
        )
        consumer = ConsumerImportFacts(
            path=app,
            binary_format="elf",
            target_library="libfoo.so.1",
            requirements=AppRequirements(undefined_symbols=set(required)),
        )
        lib = LibraryExportFacts(
            label="lib",
            binary_format="elf",
            soname="libfoo.so.1",
            export_names=frozenset(),
        )
        new = LibraryExportFacts(
            label="new",
            binary_format="elf",
            soname="libfoo.so.1",
            export_names=frozenset(exports),
        )
        seen = {}

        def fake_scope(d, app_path, old, new_path, **kw):
            seen.update(diff=d, app=app_path, old=old, new=new_path, **kw)
            return scope_diff_to_consumer_facts(
                d, consumer, lib, new, policy=kw["policy"]
            )

        detect, run_dump, compare, _ = _boundary(diff)
        with (
            detect,
            run_dump,
            compare,
            patch(f"{_STANDALONE}.scope_diff_to_app", side_effect=fake_scope),
            patch(f"{_STANDALONE}.close_consumer_scope") as close,
        ):
            result = check_appcompat(app, old_lib, new_lib)

        assert seen["diff"] is diff
        assert (seen["app"], seen["old"], seen["new"]) == (app, old_lib, new_lib)
        assert seen["old_snapshot"] is not None
        assert result.verdict == verdict
        assert result.missing_symbols == missing
        close.assert_called_once()
        assert close.call_args.kwargs["gating"] == result.breaking_for_app

    def test_policy_file_reaches_scoping(self, tmp_path):
        pf = MagicMock()
        diff = DiffResult(old_version="1", new_version="2", library="libfoo")
        detect, run_dump, compare, scope = _boundary(diff)
        with detect, run_dump, compare as mock_compare, scope as mock_scope:
            check_appcompat(*_paths(tmp_path), policy_file=pf, policy="sdk_vendor")
        assert mock_compare.call_args.kwargs["policy_file"] is pf
        assert mock_scope.call_args.kwargs["policy_file"] is pf
        assert mock_scope.call_args.kwargs["policy"] == "sdk_vendor"


class TestConsumerOverlayImpactAssessment:
    def test_consumer_required_symbol_removed_carries_impact_assessment(self):
        """ADR-052 D2 follow-up (G29 Phase 3): the consumer overlay caches its
        own ImpactAssessment at construction time, and
        impact.engine.assess_change() must reuse it unchanged."""
        from abicheck.impact.engine import assess_change

        consumer = ConsumerImportFacts(
            path=Path("myapp"),
            binary_format="elf",
            target_library="libfoo.so.1",
            requirements=AppRequirements(undefined_symbols={"foo_process"}),
        )

        def lib(label, exports):
            return LibraryExportFacts(
                label=label,
                binary_format="elf",
                soname="libfoo.so.1",
                export_names=frozenset(exports),
            )

        diff = DiffResult(old_version="1.0", new_version="2.0", library="libfoo.so.1")
        result = scope_diff_to_consumer_facts(
            diff,
            consumer,
            lib("old", {"foo_init", "foo_process"}),
            lib("new", {"foo_init"}),
        )
        (overlay,) = [
            c
            for c in result.breaking_for_app
            if c.kind == ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED
        ]
        assert overlay.impact_assessment is not None
        assert overlay.impact_assessment.public_reachable is True
        assert (
            overlay.impact_assessment.reachability_state
            == ReachabilityState.PROVEN_REACHABLE
        )
        assert overlay.impact_assessment.reachability_kind == "consumer_proven"
        # Mutating the flat fields after caching must not change what
        # assess_change() serves -- proves the cache is actually used.
        overlay.public_reachable = False
        overlay.reachability_state = ReachabilityState.UNKNOWN
        reassessed = assess_change(overlay)
        assert reassessed.public_reachable is True
        assert reassessed.reachability_state == ReachabilityState.PROVEN_REACHABLE
        assert reassessed.reachability_kind == "consumer_proven"

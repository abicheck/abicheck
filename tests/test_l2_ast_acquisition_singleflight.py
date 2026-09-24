# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Request-scoped coordination for repeated L2 frontend acquisition."""

from __future__ import annotations

import contextvars
import json
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from xml.etree import ElementTree

import pytest

from abicheck import dumper
from abicheck.deadline import DeadlineExceeded, deadline_scope
from abicheck.dumper_cache import (
    _ast_memo_slot,
    ast_acquisition_active,
    ast_acquisition_scope,
    retain_ast_context_object,
    run_ast_acquisition,
)
from abicheck.errors import SnapshotError
from abicheck.extract.header_ast_fields import parse_header_ast_fields
from abicheck.model import Function, Visibility
from abicheck.model.identity import entity_id_for_function


def _run_in_context(ctx: contextvars.Context, key: str, producer: object) -> object:
    assert callable(producer)
    return ctx.run(run_ast_acquisition, "clang", key, producer)


def test_identical_concurrent_contexts_have_one_producer() -> None:
    calls = 0
    entered = threading.Event()
    release = threading.Event()
    result = {"kind": "TranslationUnitDecl"}

    def produce() -> object:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return result

    with ast_acquisition_scope():
        parent = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(_run_in_context, parent.copy(), "same", produce)
            assert entered.wait(timeout=2)
            second = pool.submit(_run_in_context, parent.copy(), "same", produce)
            release.set()
            assert first.result(timeout=2) is result
            assert second.result(timeout=2) is result
    assert calls == 1


def test_distinct_contexts_do_not_serialize() -> None:
    barrier = threading.Barrier(2)

    def produce() -> str:
        barrier.wait(timeout=2)
        return "complete"

    with ast_acquisition_scope():
        parent = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(_run_in_context, parent.copy(), key, produce)
                for key in ("a", "b")
            ]
            assert [future.result(timeout=2) for future in futures] == [
                "complete",
                "complete",
            ]


def test_failed_production_is_retryable_without_retaining_result() -> None:
    calls = 0

    def produce() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("incomplete AST")
        return "complete"

    with ast_acquisition_scope():
        with pytest.raises(RuntimeError, match="incomplete AST"):
            run_ast_acquisition("clang", "same", produce)
        assert run_ast_acquisition("clang", "same", produce) == "complete"
    assert calls == 2


def test_scope_does_not_retain_results_after_request() -> None:
    calls = 0

    def produce() -> object:
        nonlocal calls
        calls += 1
        return object()

    with ast_acquisition_scope():
        assert run_ast_acquisition("clang", "same", produce) is run_ast_acquisition(
            "clang", "same", produce
        )
    with ast_acquisition_scope():
        run_ast_acquisition("clang", "same", produce)
    assert calls == 2


def test_scope_helpers_are_request_local_and_nested() -> None:
    assert ast_acquisition_active() is False
    assert run_ast_acquisition("clang", "outside", lambda: "direct") == "direct"
    retained = object()
    with ast_acquisition_scope() as outer:
        assert ast_acquisition_active() is True
        retain_ast_context_object(retained)
        with ast_acquisition_scope() as inner:
            assert inner is outer
    assert ast_acquisition_active() is False


def test_waiter_deadline_does_not_cancel_shared_producer() -> None:
    entered = threading.Event()
    release = threading.Event()

    def produce() -> str:
        entered.set()
        assert release.wait(timeout=2)
        return "complete"

    with ast_acquisition_scope():
        parent = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=2) as pool:
            producer = pool.submit(_run_in_context, parent.copy(), "same", produce)
            assert entered.wait(timeout=2)

            def wait_with_deadline() -> object:
                with deadline_scope(0.02):
                    return run_ast_acquisition("clang", "same", produce)

            waiter = pool.submit(parent.copy().run, wait_with_deadline)
            time.sleep(0.04)
            with pytest.raises(DeadlineExceeded):
                waiter.result(timeout=2)
            assert not producer.done()
            release.set()
            assert producer.result(timeout=2) == "complete"


def test_waiter_preserves_completed_producer_timeout_error() -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def produce() -> str:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=2)
        raise TimeoutError("compiler-owned timeout")

    with ast_acquisition_scope():
        parent = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=2) as pool:
            owner = pool.submit(_run_in_context, parent.copy(), "same", produce)
            assert entered.wait(timeout=2)
            waiter = pool.submit(_run_in_context, parent.copy(), "same", produce)
            time.sleep(0.02)
            release.set()
            with pytest.raises(TimeoutError, match="compiler-owned timeout"):
                owner.result(timeout=2)
            with pytest.raises(TimeoutError, match="compiler-owned timeout"):
                waiter.result(timeout=2)
            assert calls == 1


def test_normalized_header_evidence_is_shared_after_per_binary_parsing() -> None:
    root: dict[str, object] = {"kind": "TranslationUnitDecl"}
    calls = 0

    class Parser:
        _root = root
        _pub_header_segs = ("api.h",)
        _pub_dir_segs: tuple[str, ...] = ()
        _target_triple = "x86_64-linux-gnu"
        _is_cxx = True
        _no_binary_evidence = False

        def __init__(self, exports: set[str], *, neutral: bool = False) -> None:
            self._exported_dynamic = exports
            self._exported_static = set(exports)
            self._neutral = neutral
            self._abicheck_neutral_factory = lambda: Parser(set(), neutral=True)

        def parse_functions(self) -> list[Function]:
            nonlocal calls
            calls += 1
            return [
                Function(
                    name="api",
                    mangled="_Z3apiv",
                    return_type=(
                        "int" if self._neutral or self._exported_dynamic else "long"
                    ),
                    visibility=Visibility.PUBLIC,
                    entity_id=entity_id_for_function((), "api", mangled_name="_Z3apiv"),
                )
            ]

        def parse_variables(self) -> list[object]:
            return []

        def parse_types(self) -> list[object]:
            return []

        def parse_enums(self) -> list[object]:
            return []

        def parse_typedefs(self) -> dict[str, str]:
            return {}

        def parse_typedefs_qualified(self) -> dict[str, str]:
            return {}

        def parse_constants(self) -> dict[str, str]:
            return {}

        def parse_typedef_entity_ids(self) -> dict[str, object]:
            return {}

        def parse_constant_entity_ids(self) -> dict[str, object]:
            return {}

    with ast_acquisition_scope():
        exporting = parse_header_ast_fields(Parser({"_Z3apiv"}), producer="clang")
        hidden = parse_header_ast_fields(Parser(set()), producer="clang")
        c_linkage = parse_header_ast_fields(Parser({"api"}), producer="castxml")

    # Legacy declaration construction remains per binary because the concrete
    # parser owns nuanced export/fallback binding. Canonical normalization is
    # shared for the two clang consumers, while a distinct producer stays
    # isolated.
    # Three member-specific parses plus one neutral parse for each producer.
    assert calls == 5
    assert exporting.functions[0] is not hidden.functions[0]
    assert exporting.functions[0].return_type == "int"
    assert hidden.functions[0].return_type == "long"
    assert exporting.semantic_ir.occurrences
    assert exporting.semantic_ir is hidden.semantic_ir
    assert c_linkage.semantic_ir is not exporting.semantic_ir


@pytest.mark.integration
def test_mutated_header_is_not_published_under_pre_acquisition_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the mutation control")
    header = tmp_path / "api.hpp"
    header.write_text("int api();\n", encoding="utf-8")
    cache = tmp_path / "ast.json"
    original = dumper.run_clang_to_ast_file

    def mutate_after_compile(*args: object, **kwargs: object) -> object:
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        header.write_text("int api();\nint appeared();\n", encoding="utf-8")
        return result

    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setattr(dumper, "run_clang_to_ast_file", mutate_after_compile)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    with pytest.raises(SnapshotError, match="header inputs changed"):
        dumper._clang_header_dump([header], [], compiler="c++", lang="c++")
    assert not cache.exists()


def test_clang_singleflight_binds_producer_to_registered_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = tmp_path / "api.hpp"
    header.write_text("int api();\n", encoding="utf-8")

    def mutate_then_run(
        backend: str, key: str, producer: object, group: object = None
    ) -> object:
        assert callable(producer)
        header.write_text("int api();\nint appeared();\n", encoding="utf-8")
        return producer()

    monkeypatch.setattr(dumper.dumper_cache, "run_ast_acquisition", mutate_then_run)
    monkeypatch.setattr(dumper, "_cache_path", lambda *args, **kwargs: tmp_path / "c")
    with pytest.raises(SnapshotError, match="before clang acquisition started"):
        with ast_acquisition_scope():
            dumper._clang_header_dump([header], [], compiler="c++", lang="c++")


def test_castxml_singleflight_binds_producer_to_registered_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = tmp_path / "api.hpp"
    header.write_text("int api();\n", encoding="utf-8")
    monkeypatch.setattr(dumper, "_resolve_gated_castxml_bin", lambda value: "castxml")

    def mutate_then_run(
        backend: str, key: str, producer: object, group: object = None
    ) -> object:
        assert callable(producer)
        header.write_text("int api();\nint appeared();\n", encoding="utf-8")
        return producer()

    monkeypatch.setattr(dumper.dumper_cache, "run_ast_acquisition", mutate_then_run)
    monkeypatch.setattr(dumper, "_cache_path", lambda *args, **kwargs: tmp_path / "c")
    with pytest.raises(SnapshotError, match="before CastXML acquisition started"):
        with ast_acquisition_scope():
            dumper._castxml_dump([header], [], compiler="c++", lang="c++")


# --------------------------------------------------------------------------
# Warm-cache coordination: a disk hit is an acquisition too (PR #1305).
#
# The invariant under test is not "the second call is fast" but "one request
# plus one effective acquisition key means exactly one decoded AST object,
# whatever produced it".  Counting decodes rather than asserting equality is
# deliberate: two separately-decoded roots compare equal, so an equality
# assertion passes on the defect these tests exist to catch.
# --------------------------------------------------------------------------


def _stub_clang_resolution(monkeypatch: pytest.MonkeyPatch, cache: Path) -> None:
    """Pin every input of the clang cache key so the test owns the key."""
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang")
    monkeypatch.setattr(
        dumper, "_resolve_dpcpp_acquisition", lambda *a, **k: (False, False)
    )
    monkeypatch.setattr(
        dumper, "_resolve_clang_langmode", lambda *a, **k: (True, False, False, "clang")
    )
    monkeypatch.setattr(dumper, "_resolve_clang_system_includes", lambda *a, **k: ())
    monkeypatch.setattr(dumper, "_tool_identity", lambda *a, **k: "stable")
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)


def _count_json_decodes(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    decodes = 0
    original = dumper.dumper_cache.json.loads

    def counted(value: object, *args: object, **kwargs: object) -> object:
        nonlocal decodes
        decodes += 1
        return original(value, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(dumper.dumper_cache.json, "loads", counted)
    return lambda: decodes


def test_warm_clang_disk_hit_is_decoded_once_per_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = tmp_path / "api.hpp"
    header.write_text("int api();\n", encoding="utf-8")
    cache = tmp_path / "ast.json"
    cache.write_text('{"kind": "TranslationUnitDecl"}', encoding="utf-8")
    _stub_clang_resolution(monkeypatch, cache)
    monkeypatch.setattr(dumper, "_cache_key", lambda *a, **k: "same")
    decodes = _count_json_decodes(monkeypatch)

    with ast_acquisition_scope():
        first = dumper._clang_header_dump([header], [], lang="c++")
        second = dumper._clang_header_dump([header], [], lang="c++")
        # A later graph-shaped consumer (``memoize=False``, as
        # ``service._attach_header_graph`` passes) must not re-read the cache.
        graph = dumper._clang_header_dump([header], [], lang="c++", memoize=False)

    assert decodes() == 1
    assert second[0] is first[0] and graph[0] is first[0]
    # The whole acquisition result travels, not just the root: resolved DPC++
    # context kind and post-retry language mode alike.
    assert second == first == graph
    # No unconsumed legacy handoff is left behind holding the tree.
    assert _ast_memo_slot.get() is None


def test_warm_clang_disk_hit_keeps_legacy_handoff_without_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside an acquisition scope the pre-existing memo behaviour stands."""
    header = tmp_path / "api.hpp"
    header.write_text("int api();\n", encoding="utf-8")
    cache = tmp_path / "ast.json"
    cache.write_text('{"kind": "TranslationUnitDecl"}', encoding="utf-8")
    _stub_clang_resolution(monkeypatch, cache)
    monkeypatch.setattr(dumper, "_cache_key", lambda *a, **k: "same")
    decodes = _count_json_decodes(monkeypatch)

    assert ast_acquisition_active() is False
    root, _kind, _force_cpp = dumper._clang_header_dump(
        [header], [], lang="c++", memoize=True
    )
    slot = _ast_memo_slot.get()
    assert slot is not None and slot[0] == "clang" and slot[2] is root
    # ... and the pending handoff is what the next same-thread caller gets.
    again = dumper._clang_header_dump([header], [], lang="c++", memoize=True)
    assert again[0] is root
    assert decodes() == 1
    assert _ast_memo_slot.get() is None


def test_warm_clang_distinct_contexts_are_not_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: two effective contexts stay two acquisitions."""
    header = tmp_path / "api.hpp"
    header.write_text("int api();\n", encoding="utf-8")
    host_cache = tmp_path / "host.json"
    device_cache = tmp_path / "device.json"
    host_cache.write_text('{"kind": "TranslationUnitDecl", "ctx": "host"}', "utf-8")
    device_cache.write_text('{"kind": "TranslationUnitDecl", "ctx": "device"}', "utf-8")
    _stub_clang_resolution(monkeypatch, host_cache)
    monkeypatch.setattr(
        dumper,
        "_cache_key",
        lambda *a, **k: f"key-{k.get('frontend_context')}",
    )
    monkeypatch.setattr(
        dumper,
        "_cache_path",
        lambda key, **k: host_cache if key.endswith("host") else device_cache,
    )
    decodes = _count_json_decodes(monkeypatch)

    with ast_acquisition_scope():
        host = dumper._clang_header_dump([header], [], lang="c++")
        device = dumper._clang_header_dump(
            [header], [], lang="c++", frontend_context="device"
        )

    assert decodes() == 2
    assert host[0] is not device[0]
    assert host[0]["ctx"] == "host" and device[0]["ctx"] == "device"


def test_warm_clang_edited_header_is_not_served_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: the cache key still follows header content."""
    header = tmp_path / "api.hpp"
    header.write_text("int api();\n", encoding="utf-8")
    _stub_clang_resolution(monkeypatch, tmp_path / "unused.json")
    monkeypatch.setattr(
        dumper,
        "_cache_key",
        lambda headers, *a, **k: headers[0].read_text(encoding="utf-8"),
    )
    monkeypatch.setattr(
        dumper,
        "_cache_path",
        lambda key, **k: tmp_path / f"{len(key)}.json",
    )
    (tmp_path / "11.json").write_text('{"decls": ["api"]}', encoding="utf-8")
    (tmp_path / "27.json").write_text(
        '{"decls": ["api", "appeared"]}', encoding="utf-8"
    )

    with ast_acquisition_scope():
        before = dumper._clang_header_dump([header], [], lang="c++")
        header.write_text("int api();\nint appeared();\n", encoding="utf-8")
        after = dumper._clang_header_dump([header], [], lang="c++")

    assert before[0]["decls"] == ["api"]
    assert after[0]["decls"] == ["api", "appeared"]


def test_warm_clang_failed_acquisition_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: a producer failure does not poison the request."""
    header = tmp_path / "api.hpp"
    header.write_text("int api();\n", encoding="utf-8")
    cache = tmp_path / "ast.json"
    _stub_clang_resolution(monkeypatch, cache)
    monkeypatch.setattr(dumper, "_cache_key", lambda *a, **k: "same")
    attempts = 0

    def flaky(*args: object, **kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        raise SnapshotError("clang exploded")

    monkeypatch.setattr(dumper, "run_clang_to_ast_file", flaky)
    with ast_acquisition_scope():
        with pytest.raises(SnapshotError, match="clang exploded"):
            dumper._clang_header_dump([header], [], lang="c++")
        # The failed entry was dropped rather than published, so a later
        # attempt in the SAME request really re-enters the acquisition (and
        # now finds the warm cache that meanwhile appeared) instead of
        # replaying the stored failure forever.
        cache.write_text('{"kind": "TranslationUnitDecl"}', encoding="utf-8")
        root, _kind, _force = dumper._clang_header_dump([header], [], lang="c++")
    assert attempts == 1
    assert root == {"kind": "TranslationUnitDecl"}
    assert _ast_memo_slot.get() is None


def test_warm_castxml_disk_hit_shares_producer_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = tmp_path / "api.hpp"
    header.write_text("int api();\n", encoding="utf-8")
    cache = tmp_path / "ast.xml"
    cache.write_text("<GCC_XML/>", encoding="utf-8")
    monkeypatch.setattr(dumper, "_resolve_gated_castxml_bin", lambda value: "castxml")
    monkeypatch.setattr(dumper, "_resolve_force_cpp", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_detect_cpp20_headers", lambda *a, **k: False)
    monkeypatch.setattr(
        dumper, "_resolve_compiler_binary", lambda *a, **k: ("c++", "compiler")
    )
    monkeypatch.setattr(dumper.shutil, "which", lambda value: value)
    monkeypatch.setattr(dumper, "_tool_identity", lambda *a, **k: "stable")
    monkeypatch.setattr(dumper, "_cache_key", lambda *a, **k: "same")
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    decodes = 0
    original = dumper._read_castxml_cache

    def counted(*args: object, **kwargs: object) -> object:
        nonlocal decodes
        decodes += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(dumper, "_read_castxml_cache", counted)
    first_meta: list[tuple[str, bool]] = []
    second_meta: list[tuple[str, bool]] = []
    with ast_acquisition_scope():
        first = dumper._castxml_dump(
            [header], [], lang="c++", _selected_meta_out=first_meta
        )
        second = dumper._castxml_dump(
            [header], [], lang="c++", _selected_meta_out=second_meta
        )

    assert decodes == 1
    assert second is first
    # The producer's own (compiler, force_cpp) selection reaches the waiter
    # rather than each consumer re-deriving it.
    assert first_meta == second_meta == [("c++", True)]


def test_streaming_prune_is_disabled_inside_an_acquisition_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared root is walked raw by the graph consumer, so never pruned.

    ``ast_memoize_scope`` already gated this for the thread-local handoff; the
    request-keyed table is the second way a parse's exact root reaches that
    consumer, and the two scopes are opened independently.
    """
    from abicheck import dumper_clang_errors

    monkeypatch.setenv(dumper_clang_errors.STREAM_PRUNE_DEPENDENCY_DECLS_ENV_VAR, "1")
    assert dumper_clang_errors._streaming_prune_enabled() is True
    with ast_acquisition_scope():
        assert dumper_clang_errors._streaming_prune_enabled() is False


# --------------------------------------------------------------------------
# Real directory L2 compare: one shared public header, six separately
# compiled DSOs per side.  Driving the public CLI (rather than calling the
# dumper helper directly) is the point -- it is the only way to prove the
# whole-snapshot cache did not simply skip the AST path being measured, and
# the only shape in which "the next group of release workers" exists at all.
# --------------------------------------------------------------------------

_FIXTURE_HEADER = """#pragma once
namespace demo {
struct Config {
  int width;
  int height;
  double scale;
};
class Engine {
 public:
  Engine();
  virtual ~Engine();
  virtual int run(const Config& cfg);
  int cached() const;
 private:
  int state_;
};
int helper_alpha(const Config& cfg);
int helper_beta(int value);
long helper_gamma(const Config& cfg, int value);
}  // namespace demo
"""

_FIXTURE_SOURCE = """#include "api.hpp"
namespace demo {{
Engine::Engine() : state_({n}) {{}}
Engine::~Engine() {{}}
int Engine::run(const Config& cfg) {{ return cfg.width + state_; }}
int Engine::cached() const {{ return state_; }}
int helper_alpha(const Config& cfg) {{ return cfg.height; }}
int helper_beta(int value) {{ return value + {n}; }}
{gamma}
}}  // namespace demo
extern "C" int mod{n}_entry(int v) {{ return v + {n}; }}
"""

_FIXTURE_GAMMA = (
    "long helper_gamma(const Config& cfg, int value) { return cfg.width + value; }"
)

#: The member whose ``helper_gamma`` definition the new side drops -- one real
#: breaking finding that must survive every reuse change below unchanged.
_BROKEN_MEMBER = 3


def _build_release_tree(root: Path, *, drop_gamma_in: int | None) -> Path:
    include = root / "include"
    include.mkdir(parents=True)
    (include / "api.hpp").write_text(_FIXTURE_HEADER, encoding="utf-8")
    libs = root / "lib"
    libs.mkdir()
    for n in range(6):
        src = root / f"mod{n}.cpp"
        src.write_text(
            _FIXTURE_SOURCE.format(
                n=n, gamma="" if drop_gamma_in == n else _FIXTURE_GAMMA
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                "g++",
                "-shared",
                "-fPIC",
                "-g",
                "-O0",
                f"-I{include}",
                str(src),
                "-o",
                str(libs / f"libmod{n}.so"),
            ],
            check=True,
            capture_output=True,
        )
    return root


@pytest.fixture(scope="module")
def six_dso_release(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One public header plus six separately compiled DSOs on each side."""
    if shutil.which("g++") is None:
        pytest.skip("g++ is required to build the six-DSO L2 fixture")
    root = tmp_path_factory.mktemp("six_dso_release")
    _build_release_tree(root / "old", drop_gamma_in=None)
    _build_release_tree(root / "new", drop_gamma_in=_BROKEN_MEMBER)
    return root


def _is_raw_ast_acquisition(backend: str) -> bool:
    """Whether an acquisition-table entry is a real raw-AST acquisition.

    The partition here used to be implicit -- "anything whose backend does not
    end in ``-normalized`` is a raw AST key" -- which held only while the
    shared table carried exactly two kinds of entry. It is a catch-all, so the
    moment a third kind joined it was silently counted as an AST acquisition:
    ``dumper_clang``'s template-parameter index bundle (one per raw AST, its
    own key namespace) is neither an AST nor a normalization, and counting it
    as a raw key broke "one decode per distinct raw key" below with no decode
    to match it.

    Stated as an explicit exclusion list rather than an allow-list of AST
    backends, so a frontend this file does not know about still counts as raw
    (the conservative direction), and imported from the owning module rather
    than spelled again here, so renaming the namespace cannot leave this
    stale.
    """

    from abicheck.dumper_clang import _TEMPLATE_PARAM_INDEX_NAMESPACE

    if backend.endswith("-normalized"):
        return False
    return backend != _TEMPLATE_PARAM_INDEX_NAMESPACE


class _AcquisitionCounters:
    """Real call counts around the real frontends -- never a substitute for
    them: every compiler, parser and normalizer below is the production one,
    wrapped only to be counted."""

    def __init__(self) -> None:
        self.requested_keys: list[tuple[str, str]] = []
        self.compiler = 0
        self.raw_decodes: list[str] = []
        self.normalizations = 0

    @property
    def raw_ast_keys(self) -> set[str]:
        return {backend for backend, _ in self.requested_keys}

    def reset(self) -> None:
        self.requested_keys.clear()
        self.compiler = 0
        self.raw_decodes.clear()
        self.normalizations = 0


@pytest.fixture
def acquisition_counters(monkeypatch: pytest.MonkeyPatch) -> _AcquisitionCounters:
    from abicheck import dumper_cache
    from abicheck.extract import header_ast_fields

    counters = _AcquisitionCounters()
    scope_run = dumper_cache.AstAcquisitionScope.run

    def counted_run(
        self, backend: str, key: str, producer: object, group: object = None
    ):  # type: ignore[no-untyped-def]
        # *group* is forwarded verbatim: it is what retains the AST root
        # while a key derived from its ``id()`` lives, so a double that
        # dropped it would disable the retention this test drives through.
        counters.requested_keys.append((backend, key))
        assert callable(producer)
        return scope_run(self, backend, key, producer, group=group)

    monkeypatch.setattr(dumper_cache.AstAcquisitionScope, "run", counted_run)

    def _count(mod: object, name: str, bump: object) -> None:
        original = getattr(mod, name)

        def wrapper(*args: object, **kwargs: object) -> object:
            bump()  # type: ignore[operator]
            return original(*args, **kwargs)

        monkeypatch.setattr(mod, name, wrapper)

    def _bump_compiler() -> None:
        counters.compiler += 1

    def _bump_normalize() -> None:
        counters.normalizations += 1

    _count(dumper, "run_clang_to_ast_file", _bump_compiler)
    _count(dumper, "_run_castxml_attempt", _bump_compiler)
    _count(dumper, "_read_castxml_cache", lambda: counters.raw_decodes.append("xml"))
    # A projection sidecar is a derived artifact, not a raw AST decode: a
    # retained-table hit offers the cache entry to the header-graph attach
    # (`run_ast_acquisition_offering_entry`), which then reads the sidecar
    # instead of re-projecting the tree. Those reads are not counted here.
    in_sidecar = [0]
    _count(
        dumper_cache.json,
        "loads",
        lambda: in_sidecar[0] or counters.raw_decodes.append("json"),
    )
    from abicheck.buildsource import header_graph_projection_cache as _proj

    original_load = _proj.load_cached_projection

    def _load_sidecar(path: Path):  # type: ignore[no-untyped-def]
        in_sidecar[0] += 1
        try:
            return original_load(path)
        finally:
            in_sidecar[0] -= 1

    monkeypatch.setattr(_proj, "load_cached_projection", _load_sidecar)
    _count(header_ast_fields, "_normalize_header_ast_fields", _bump_normalize)
    return counters


def _findings(report: Path) -> list[str]:
    payload = json.loads(report.read_text(encoding="utf-8"))
    return sorted(
        "|".join(
            str(x)
            for x in (
                library.get("library"),
                change.get("kind"),
                change.get("symbol"),
                change.get("name"),
                change.get("old_value"),
                change.get("new_value"),
            )
        )
        for library in payload.get("libraries", [])
        for change in (library.get("findings") or [])
    )


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason=(
        "ELF release fan-out shape: this asserts that a directory compare's "
        "per-member L2 header acquisitions coordinate. On the macOS and "
        "Windows CI runners the fixture's header parse degrades before "
        "`_clang_header_dump`/`_castxml_dump` reach the acquisition at all "
        "(zero keys registered, an honest ADR-028 D3 degrade unrelated to "
        "this coordination), so the invariant has nothing to observe there "
        "-- the same reason this lane already skips the ELF/DWARF suites. "
        "The deterministic tests above run on every platform."
    ),
)
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_directory_l2_compare_acquires_one_ast_per_key(
    backend: str,
    six_dso_release: Path,
    acquisition_counters: _AcquisitionCounters,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cold start, warm cache, later worker groups and the graph pass all
    share one acquisition per (backend, key) in one request."""
    if shutil.which(backend) is None:
        pytest.skip(f"{backend} backend is not available on this host")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", backend)

    # Six members against two workers, so members 3-6 run in a later group
    # than the producer's own -- the shape a per-call disk decode escaped
    # request coordination in.
    from abicheck.workflows import release_jobs

    monkeypatch.setattr(
        release_jobs, "resolve_release_worker_count", lambda *a, **k: (2, None, 0.0)
    )

    from click.testing import CliRunner

    from abicheck.cli import main

    def run(tag: str) -> Path:
        report = tmp_path / f"{tag}.json"
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(six_dso_release / "old" / "lib"),
                str(six_dso_release / "new" / "lib"),
                "-H",
                str(six_dso_release / "old" / "include"),
                "-o",
                f"json={report}",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 4, result.output
        return report

    cold = run("cold")
    cold_keys = list(acquisition_counters.requested_keys)
    cold_compiler = acquisition_counters.compiler
    cold_decodes = len(acquisition_counters.raw_decodes)
    cold_normalizations = acquisition_counters.normalizations

    # Every member really entered the shared table (twelve dumps, six per
    # side), so the assertions below are about coordination, not about a
    # path that quietly did not run.
    assert len(cold_keys) >= 12
    raw_keys = {(b, k) for b, k in cold_keys if _is_raw_ast_acquisition(b)}
    normalized_keys = {(b, k) for b, k in cold_keys if b.endswith("-normalized")}
    assert cold_compiler >= 1
    # One decode/compile per distinct raw key, one neutral normalization per
    # distinct normalization scope -- not one per member.
    assert cold_decodes <= len(raw_keys)
    assert cold_normalizations == len(normalized_keys)

    acquisition_counters.reset()
    warm = run("warm")

    # The whole-snapshot cache must not have skipped the path under test.
    assert acquisition_counters.requested_keys, "warm run never reached L2 acquisition"
    warm_raw_keys = {
        (b, k)
        for b, k in acquisition_counters.requested_keys
        if _is_raw_ast_acquisition(b)
    }
    warm_normalized_keys = {
        (b, k)
        for b, k in acquisition_counters.requested_keys
        if b.endswith("-normalized")
    }
    assert acquisition_counters.compiler == 0
    # At most one decode per key: a key whose final consumer takes a stored
    # derived artifact (the header-graph projection sidecar) decodes nothing.
    assert len(acquisition_counters.raw_decodes) <= len(warm_raw_keys)
    assert acquisition_counters.normalizations == len(warm_normalized_keys)
    assert warm_raw_keys == raw_keys

    # Correctness is the real gate: the full canonical finding set, not just
    # the verdict or a count, is identical cold and warm -- and still holds
    # the member-local break.
    cold_findings = _findings(cold)
    assert cold_findings == _findings(warm)
    assert any(
        f"libmod{_BROKEN_MEMBER}.so" in finding and "helper_gamma" in finding
        for finding in cold_findings
    )
    # Member binding stays per member: each DSO keeps its own export.
    assert sum(1 for f in cold_findings if "mod0_entry" in f) >= 1


# --------------------------------------------------------------------------
# The small decision functions the reordering above rests on, covered
# directly rather than only through a full acquisition.
# --------------------------------------------------------------------------


def test_read_cached_castxml_returns_the_parsed_root(tmp_path: Path) -> None:
    from abicheck.dumper_cache import read_cached_castxml

    cached = tmp_path / "ast.xml"
    cached.write_text("<GCC_XML><Namespace name='demo'/></GCC_XML>", encoding="utf-8")
    root = read_cached_castxml(cached)
    assert root is not None
    assert root.tag == "GCC_XML"
    assert cached.exists()


def test_read_cached_castxml_evicts_an_unusable_entry(tmp_path: Path) -> None:
    """A torn/corrupt cache file is discarded, not raised on."""
    from abicheck.dumper_cache import read_cached_castxml

    cached = tmp_path / "ast.xml"
    cached.write_text("<GCC_XML><unclosed>", encoding="utf-8")
    assert read_cached_castxml(cached) is None
    assert not cached.exists()  # evicted so the caller falls through to a run


@pytest.mark.parametrize(
    ("memoize", "memo_scope", "acquisition", "expected"),
    [
        (None, False, False, False),
        (None, True, False, True),
        (None, True, True, False),
        (None, False, True, False),
        (True, False, False, True),
        (True, True, True, False),
        (False, True, False, False),
        (False, False, True, False),
    ],
)
def test_resolve_request_memoization_matrix(
    memoize: bool | None, memo_scope: bool, acquisition: bool, expected: bool
) -> None:
    """The memo is written only when a same-thread consumer will pop it.

    Oracle stated independently of the implementation: the explicit argument
    (or the memo scope when it is ``None``) says whether a handoff was wanted,
    and an active acquisition scope means the request-keyed table is already
    that handoff -- so the answer is ``wanted and not acquisition``.
    """
    from abicheck import dumper_cache

    def check() -> None:
        assert dumper_cache.resolve_request_memoization(memoize) is expected

    def with_acquisition() -> None:
        if acquisition:
            with ast_acquisition_scope():
                check()
        else:
            check()

    if memo_scope:
        from abicheck.dumper_cache import ast_memoize_scope

        with ast_memoize_scope():
            with_acquisition()
    else:
        with_acquisition()


@pytest.mark.parametrize(
    ("env_on", "scope", "expected"),
    [
        (False, None, False),
        (True, None, True),
        (True, "acquisition", False),
        (True, "memoize", False),
        (True, "suppressed", False),
        (False, "acquisition", False),
    ],
)
def test_streaming_prune_gate_matrix(
    env_on: bool, scope: str | None, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pruning runs only when nothing else will reuse this exact root.

    Oracle: ``env_on and no sharing/suppression scope is active`` -- derived
    from what each scope means, not from the function's own conditions.
    """
    from abicheck import dumper_clang_errors
    from abicheck.dumper_cache import ast_memoize_scope
    from abicheck.dumper_clang_streaming import suppress_streaming_prune

    monkeypatch.setenv(
        dumper_clang_errors.STREAM_PRUNE_DEPENDENCY_DECLS_ENV_VAR,
        "1" if env_on else "0",
    )
    scopes = {
        None: nullcontext,
        "acquisition": ast_acquisition_scope,
        "memoize": ast_memoize_scope,
        "suppressed": suppress_streaming_prune,
    }
    with scopes[scope]():
        assert dumper_clang_errors._streaming_prune_enabled() is expected


def test_cold_castxml_acquisition_shares_the_producer_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The miss branch: one real run, its metadata reaching both consumers."""
    header = tmp_path / "api.hpp"
    header.write_text("int api();\n", encoding="utf-8")
    cache = tmp_path / "ast.xml"  # deliberately absent: this is the cold path
    monkeypatch.setattr(dumper, "_resolve_gated_castxml_bin", lambda value: "castxml")
    monkeypatch.setattr(dumper, "_resolve_force_cpp", lambda *a, **k: False)
    monkeypatch.setattr(dumper, "_detect_cpp20_headers", lambda *a, **k: False)
    monkeypatch.setattr(
        dumper, "_resolve_compiler_binary", lambda *a, **k: ("cc", "compiler")
    )
    monkeypatch.setattr(dumper.shutil, "which", lambda value: value)
    monkeypatch.setattr(dumper, "_tool_identity", lambda *a, **k: "stable")
    monkeypatch.setattr(dumper, "_cache_key", lambda *a, **k: "same")
    monkeypatch.setattr(dumper, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setattr(dumper, "_write_castxml_cache", lambda *a, **k: None)
    produced = ElementTree.Element("GCC_XML")
    runs = 0

    def one_run(*args: object, **kwargs: object) -> object:
        nonlocal runs
        runs += 1
        return produced

    monkeypatch.setattr(dumper, "_run_castxml_attempt", one_run)
    first_meta: list[tuple[str, bool]] = []
    second_meta: list[tuple[str, bool]] = []
    with ast_acquisition_scope():
        first = dumper._castxml_dump(
            [header], [], lang="c", _selected_meta_out=first_meta
        )
        second = dumper._castxml_dump(
            [header], [], lang="c", _selected_meta_out=second_meta
        )

    assert runs == 1
    assert first is second is produced
    # `cc`, not the requested `c++`: the producer's own C-mode compiler
    # selection is what both consumers are told.
    assert first_meta == second_meta == [("cc", False)]

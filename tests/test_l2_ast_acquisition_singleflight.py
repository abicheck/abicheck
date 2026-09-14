# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Request-scoped coordination for repeated L2 frontend acquisition."""

from __future__ import annotations

import contextvars
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from abicheck import dumper
from abicheck.deadline import DeadlineExceeded, deadline_scope
from abicheck.dumper_cache import (
    ast_acquisition_active,
    ast_acquisition_scope,
    retain_ast_context_object,
    run_ast_acquisition,
)
from abicheck.errors import SnapshotError
from abicheck.extract.header_ast_fields import parse_header_ast_fields
from abicheck.model import Function, Visibility


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

    def produce() -> str:
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

        def __init__(self, exports: set[str]) -> None:
            self._exported_dynamic = exports
            self._exported_static = set(exports)

        def parse_functions(self) -> list[Function]:
            nonlocal calls
            calls += 1
            return [
                Function(
                    name="api",
                    mangled="_Z3apiv",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
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
    assert calls == 3
    assert exporting.functions[0] is not hidden.functions[0]
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

    def mutate_then_run(backend: str, key: str, producer: object) -> object:
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

    def mutate_then_run(backend: str, key: str, producer: object) -> object:
        assert callable(producer)
        header.write_text("int api();\nint appeared();\n", encoding="utf-8")
        return producer()

    monkeypatch.setattr(dumper.dumper_cache, "run_ast_acquisition", mutate_then_run)
    monkeypatch.setattr(dumper, "_cache_path", lambda *args, **kwargs: tmp_path / "c")
    with pytest.raises(SnapshotError, match="before CastXML acquisition started"):
        with ast_acquisition_scope():
            dumper._castxml_dump([header], [], compiler="c++", lang="c++")

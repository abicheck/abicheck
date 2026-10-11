"""The AST memo slot stays per-thread even when a thread inherits context.

Free-threaded CPython (3.14t+) starts a ``threading.Thread`` with a copy of
the parent's context. A ``ContextVar``-backed slot then shows the parent's
pending AST to every worker, so both compare sides could pop it -- the
cross-side steal ``header_ast_cache._ast_memo_slot`` must rule out. Passing
``context=`` explicitly reproduces that start-up on any 3.14+ interpreter.
"""

from __future__ import annotations

import contextvars
import sys
import threading

import pytest

from abicheck.storage import header_ast_cache


@pytest.mark.skipif(
    sys.version_info < (3, 14), reason="threading.Thread(context=...) is 3.14+"
)
@pytest.mark.parametrize("workers", [1, 2, 4])
def test_workers_with_inherited_context_never_see_parent_slot(workers: int) -> None:
    header_ast_cache.store_cached_ast("same-key", "clang", {"side": "parent"})
    seen: list[object] = []
    lock = threading.Lock()

    def _worker(i: int) -> None:
        before = header_ast_cache._ast_memo_slot.value
        header_ast_cache.store_cached_ast("same-key", "clang", {"side": i})
        with lock:
            seen.append((before, header_ast_cache._ast_memo_slot.value))

    threads = [
        threading.Thread(target=_worker, args=(i,), context=contextvars.copy_context())
        for i in range(workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(seen, key=lambda p: p[1][2]["side"]) == [  # type: ignore[index]
        (None, ("clang", "same-key", {"side": i})) for i in range(workers)
    ]
    assert header_ast_cache._ast_memo_slot.value == (
        "clang",
        "same-key",
        {"side": "parent"},
    )
    header_ast_cache._ast_memo_slot.value = None

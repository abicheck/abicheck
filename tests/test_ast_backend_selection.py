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

"""ADR-063 Track T4 ("Dump request contract"), item 1's first still-open
clause: splitting header-AST **backend selection** from **fallback policy**.

Before this change, ``dumper._header_ast_parser`` computed its own dispatch
inline (``_resolve_single_ast_backend`` then ``if resolved == "clang" or
frontend_context != "host": ...``), and
``service_dump_pipeline.resolve_dump_request`` independently re-derived the
identical prediction for its own reporting-only ``effective_header_backend``
field -- re-deriving both the override-precedence rule (an explicit
``compile.frontend`` beats the bare ``header_backend`` default, unless it is
itself the no-op spelling ``"auto"``) and the "any non-host context forces
clang" rule a second time. Two Codex-review-caught regressions in this exact
spot (the first missing case-insensitivity, the second conflating an
explicit pinned castxml with an auto request) are why the docstrings in
``dumper.py``/``service_dump_pipeline.py`` are as detailed as they are; both
are preserved by tests below.

The fix: ``dumper._resolve_effective_ast_backend`` is now the one "selection"
function -- pure, callable with no execution -- both ``_header_ast_parser``'s
own dispatch and ``service_compare_evidence.effective_frontend_for_context``
(``resolve_dump_request``'s new call) delegate to. This file states that as
an executable contract: the prediction is *identical* regardless of which
caller asks, over a range of inputs, not just the one case each existing
test file happened to already cover.

Fallback *policy* (``dumper._castxml_fallback_reason`` -- only discoverable
by actually attempting castxml and observing a specific failure signature)
is a deliberately separate concern and is untouched by this split; it has
its own tests elsewhere (``test_clang_castxml_origin_parity.py`` and
siblings) and is not exercised here.
"""

from __future__ import annotations

import pytest

from abicheck.errors import AstContextMissingError, ValidationError


class TestResolveEffectiveAstBackend:
    """Direct contract of the new pure selection function."""

    def test_hybrid_is_never_selectable_regardless_of_context(self):
        """No single parser can satisfy "hybrid" -- selection must raise for
        it under every context, matching ``_resolve_single_ast_backend``'s
        own hybrid rejection (see ``dumper_hybrid.run_hybrid_dump`` for the
        only place that combination is actually resolved)."""
        from abicheck.dumper import _resolve_effective_ast_backend

        for context in ("host", "device"):
            with pytest.raises(ValidationError):
                _resolve_effective_ast_backend("hybrid", context)

    def test_explicit_castxml_under_non_host_context_raises(self):
        """An explicit ``castxml`` request combined with a non-"host" context
        is a hard error (castxml has no SYCL/DPC++ host/device concept) --
        selection must raise, not silently predict a backend that would
        never actually run without the same request also raising."""
        from abicheck.dumper import _resolve_effective_ast_backend

        with pytest.raises(AstContextMissingError):
            _resolve_effective_ast_backend("castxml", "device")

    @pytest.mark.parametrize("requested", ["auto", "clang"])
    def test_non_host_context_always_predicts_clang(self, requested):
        """Every request that is NOT an explicit/pinned castxml resolves to
        clang under a non-"host" context -- mirrors
        ``_header_ast_parser``'s own ``if resolved == "clang" or
        frontend_context != "host": return _run_clang()``."""
        from abicheck.dumper import _resolve_effective_ast_backend

        assert _resolve_effective_ast_backend(requested, "device") == "clang"

    @pytest.mark.parametrize(
        "requested,expected",
        [("auto", "castxml"), ("castxml", "castxml"), ("clang", "clang")],
    )
    def test_host_context_resolves_normally(self, requested, expected):
        """Under the default "host" context, selection is just the ordinary
        auto/explicit backend resolution -- no context override applies."""
        from abicheck.dumper import _resolve_effective_ast_backend

        assert _resolve_effective_ast_backend(requested, "host") == expected


class TestEffectiveFrontendForContextParity:
    """``service_compare_evidence.effective_frontend_for_context`` must never
    diverge from ``dumper._resolve_effective_ast_backend`` -- the exact
    "callable identically from either module" contract ADR-063 T4 asks for.
    """

    @pytest.mark.parametrize(
        "compile_frontend,header_backend,context",
        [
            ("auto", "auto", "device"),
            ("auto", "clang", "device"),
            ("clang", "auto", "device"),
            ("AUTO", "clang", "device"),  # case-insensitive no-op override
            ("auto", "auto", "host"),
        ],
    )
    def test_agrees_with_the_shared_selection_function(
        self, compile_frontend, header_backend, context
    ):
        from abicheck.compile_context import CompileContext
        from abicheck.dumper import _resolve_effective_ast_backend
        from abicheck.service_compare_evidence import (
            effective_frontend_for_context,
            requested_frontend_override,
        )

        compile_ctx = CompileContext(
            frontend=compile_frontend, frontend_context=context
        )
        requested = requested_frontend_override(compile_ctx, header_backend)

        if context.lower() == "host":
            # No context to predict -- the function must say so explicitly
            # rather than silently reusing the host-only `effective_frontend`
            # answer itself (that split is `effective_frontend`'s own job).
            assert effective_frontend_for_context(compile_ctx, header_backend) is None
        else:
            expected = _resolve_effective_ast_backend(requested, context.lower())
            assert (
                effective_frontend_for_context(compile_ctx, header_backend) == expected
            )

    def test_none_compile_context_predicts_nothing(self):
        from abicheck.service_compare_evidence import effective_frontend_for_context

        assert effective_frontend_for_context(None, "auto") is None

    def test_unsatisfiable_combination_predicts_nothing(self):
        """An explicit castxml under a non-host context can't be satisfied by
        any single parser -- the context-aware prediction must answer
        `None` (meaning: "leave whatever `effective_frontend` already
        reported unchanged"), not raise past its own caller
        (`resolve_dump_request` is a best-effort preview that must never
        raise)."""
        from abicheck.compile_context import CompileContext
        from abicheck.service_compare_evidence import effective_frontend_for_context

        compile_ctx = CompileContext(frontend="castxml", frontend_context="device")
        assert effective_frontend_for_context(compile_ctx, "auto") is None

    def test_hybrid_under_non_host_context_predicts_nothing(self):
        from abicheck.compile_context import CompileContext
        from abicheck.service_compare_evidence import effective_frontend_for_context

        compile_ctx = CompileContext(frontend="hybrid", frontend_context="device")
        assert effective_frontend_for_context(compile_ctx, "auto") is None


class TestResolveDumpRequestUsesTheSharedSelector:
    """End-to-end: ``resolve_dump_request``'s own ``effective_header_backend``
    for a non-host ``frontend_context`` must equal what the shared selector
    predicts -- not a second, independently-derived answer.
    """

    def test_matches_shared_selector_for_device_context(self, tmp_path):
        from abicheck.dumper import _resolve_effective_ast_backend
        from abicheck.model import AbiSnapshot
        from abicheck.serialization import snapshot_to_json
        from abicheck.service import DumpRequest, InputSpec
        from abicheck.service_dump_pipeline import resolve_dump_request

        snap_path = tmp_path / "lib.abi.json"
        snap_path.write_text(
            snapshot_to_json(AbiSnapshot(library="lib", version="1.0")),
            encoding="utf-8",
        )
        request = DumpRequest(
            input=InputSpec(path=snap_path),
            frontend="auto",
            frontend_context="device",
        )
        resolved = resolve_dump_request(request)
        assert resolved.effective_header_backend == _resolve_effective_ast_backend(
            "auto", "device"
        )

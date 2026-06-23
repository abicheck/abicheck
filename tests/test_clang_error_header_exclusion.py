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

"""Graceful #error header handling: when ``-H`` expands to a public include dir,
headers not meant for direct inclusion (preview / internal ``detail`` headers
that raise ``#error``) must be excluded with a diagnostic rather than aborting
the whole L2 parse. Pure-parser tests (no compiler needed)."""

from __future__ import annotations

from pathlib import Path

from abicheck.dumper_clang_errors import (
    _headers_failing_in_aggregate,
    retry_excluding_error_headers,
)

AGG = Path("/tmp/agg12345.hpp")


def test_direct_error_header_attributed_to_aggregate_line():
    # A direct-inclusion guard that #errors directly from the aggregate line.
    stderr = (
        f"In file included from {AGG}:10:\n"
        "/x/_detail.h:21:6: error: do not #include this internal header directly\n"
        "   21 |     #error do not #include this internal header directly\n"
    )
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == {9}


def test_preview_macro_gate_is_not_excluded():
    # Codex P2: "Set MACRO to include X" is a config / feature-macro gate, not a
    # direct-inclusion guard — it must surface (so the user defines the macro)
    # rather than be silently dropped from the L2 surface.
    stderr = (
        f"In file included from {AGG}:10:\n"
        "/x/concurrent_lru_cache.h:21:6: error: Set TBB_PREVIEW_CONCURRENT_LRU_CACHE "
        "to include concurrent_lru_cache.h\n"
        "   21 |     #error Set TBB_PREVIEW_CONCURRENT_LRU_CACHE to include ...\n"
    )
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == set()


def test_transitive_chain_attributes_to_outermost_aggregate_frame():
    # A deeper include chain: the aggregate frame is printed first and is the
    # one that identifies the offending top-level header (line 30 -> index 29).
    stderr = (
        f"In file included from {AGG}:30:\n"
        "In file included from /x/_flow_graph.h:5:\n"
        "/x/detail/_flow_graph_body_impl.h:21:2: error: Do not #include this "
        "internal header directly\n"
        "   21 |     #error Do not #include this internal header directly\n"
        "      |      ^\n"
    )
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == {29}


def test_multiple_offending_headers_collected():
    stderr = (
        f"In file included from {AGG}:10:\n"
        "/x/a.h:21:6: error: a.h must not be included directly\n"
        "   21 |     #error a.h must not be included directly\n"
        f"In file included from {AGG}:30:\n"
        "/x/b.h:21:2: error: do not #include this internal header directly\n"
        "   21 |     #error do not #include this internal header directly\n"
    )
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == {9, 29}


def test_real_compile_error_in_public_header_is_not_excluded():
    # Codex review: a genuine syntax / missing-flag error reached through the
    # aggregate must NOT be dropped — there is no rendered `#error` directive, so
    # the header stays in and the hard parse failure surfaces (L2 stays complete).
    stderr = (
        f"In file included from {AGG}:7:\n"
        "/x/public.h:42:1: error: unknown type name 'frobnicate'\n"
        "   42 | frobnicate int x;\n"
        "      | ^\n"
    )
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == set()


def test_config_macro_error_in_public_header_is_not_excluded():
    # Codex P2: a #error that reports a missing config macro / unsupported target
    # on a public header is NOT a direct-inclusion guard — it must surface (fail)
    # so the user passes the build flag, not be silently dropped.
    stderr = (
        f"In file included from {AGG}:5:\n"
        '/x/public.h:3:2: error: "define MYLIB_CONFIG before using this library"\n'
        '    3 | #error "define MYLIB_CONFIG before using this library"\n'
        "      |  ^\n"
    )
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == set()


def test_error_in_aggregate_itself_is_not_excludable():
    # A genuinely broken umbrella header (error in the aggregate TU, no include
    # chain) must NOT cause a header to be dropped — that would hide a real bug.
    stderr = f"{AGG}:3:1: error: unknown type name 'foo'\n"
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == set()


def test_warning_chain_is_not_treated_as_failure():
    # An include chain that only produces a warning attributes nothing.
    stderr = f"In file included from {AGG}:5:\n/x/c.h:9:1: warning: deprecated\n"
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == set()


def test_out_of_range_line_ignored():
    # A line number beyond the header count is ignored (defensive).
    stderr = f"In file included from {AGG}:99:\n/x/d.h:1:1: error: boom\n"
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == set()


class _Proc:
    def __init__(self, returncode, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def test_retry_excludes_then_succeeds(tmp_path):
    # First parse fails with a #error from header index 1; after exclusion the
    # retry succeeds. The driver rewrites the aggregate and returns the rc=0 result.
    agg = tmp_path / "agg.hpp"
    headers = [tmp_path / "a.h", tmp_path / "bad.h", tmp_path / "c.h"]
    written: list[list] = []
    fail = _Proc(
        1,
        stderr=(
            f"In file included from {agg}:2:\n"
            "/x/bad.h:5:1: error: do not #include this internal header directly\n"
            "    5 | #error do not #include this internal header directly\n"
        ),
    )
    calls = {"n": 0}

    def run_clang():
        calls["n"] += 1
        return _Proc(0)  # retry succeeds

    out = retry_excluding_error_headers(
        result=fail,
        run_clang=run_clang,
        write_agg=written.append,
        agg_path=agg,
        active_headers=list(headers),
    )
    assert out.returncode == 0
    assert calls["n"] == 1  # one retry
    assert written and headers[1] not in written[-1]  # bad.h was dropped


def test_nondigit_aggregate_frame_is_ignored():
    # A malformed include frame (non-numeric line) leaves no attributable root,
    # so a following error attributes nothing (defensive parse robustness).
    stderr = (
        f"In file included from {AGG}:notanumber:\n"
        "/x/e.h:1:1: error: do not include directly\n"
        "    1 | #error do not include directly\n"
    )
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == set()


def test_retry_breaks_when_no_header_is_excludable(tmp_path):
    # Multi-header parse fails on a genuine error (no #error guard), so nothing is
    # excludable: the driver breaks immediately, never rewrites or re-runs, and the
    # original failure stands (the hard parse error must surface).
    agg = tmp_path / "agg.hpp"
    fail = _Proc(
        1,
        stderr=(
            f"In file included from {agg}:2:\n"
            "/x/real.h:9:1: error: unknown type name 'frobnicate'\n"
            "    9 | frobnicate int x;\n"
        ),
    )
    ran = {"n": 0}

    def run_clang():  # pragma: no cover - must not be called
        ran["n"] += 1
        return _Proc(0)

    out = retry_excluding_error_headers(
        result=fail,
        run_clang=run_clang,
        write_agg=lambda _h: None,
        agg_path=agg,
        active_headers=[tmp_path / "a.h", tmp_path / "real.h", tmp_path / "c.h"],
    )
    assert out.returncode == 1  # failure preserved
    assert ran["n"] == 0  # broke before any retry


def test_retry_single_header_not_reduced(tmp_path):
    # A single-header -H (umbrella the user chose) is never reduced; failure stands.
    agg = tmp_path / "agg.hpp"
    fail = _Proc(1, stderr=f"In file included from {agg}:1:\n/x/a.h:1:1: error: x\n")
    out = retry_excluding_error_headers(
        result=fail,
        run_clang=lambda: _Proc(0),
        write_agg=lambda _h: None,
        agg_path=agg,
        active_headers=[tmp_path / "a.h"],
    )
    assert out.returncode == 1  # not retried

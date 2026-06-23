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

from abicheck.dumper_clang_errors import _headers_failing_in_aggregate

AGG = Path("/tmp/agg12345.hpp")


def test_direct_error_header_attributed_to_aggregate_line():
    # oneTBB shape: a preview header #errors directly from the aggregate.
    stderr = (
        f"In file included from {AGG}:10:\n"
        "/x/concurrent_lru_cache.h:21:6: error: Set TBB_PREVIEW_CONCURRENT_LRU_CACHE "
        "to include concurrent_lru_cache.h\n"
        "   21 |     #error Set ...\n"
    )
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == {9}


def test_transitive_chain_attributes_to_outermost_aggregate_frame():
    # A deeper include chain: the aggregate frame is printed first and is the
    # one that identifies the offending top-level header (line 30 -> index 29).
    stderr = (
        f"In file included from {AGG}:30:\n"
        "In file included from /x/_flow_graph.h:5:\n"
        "/x/detail/_flow_graph_body_impl.h:21:2: error: Do not #include this "
        "internal header directly\n"
    )
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == {29}


def test_multiple_offending_headers_collected():
    stderr = (
        f"In file included from {AGG}:10:\n"
        "/x/a.h:21:6: error: Set FOO\n"
        f"In file included from {AGG}:30:\n"
        "/x/b.h:21:2: error: Do not #include\n"
    )
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == {9, 29}


def test_error_in_aggregate_itself_is_not_excludable():
    # A genuinely broken umbrella header (error in the aggregate TU, no include
    # chain) must NOT cause a header to be dropped — that would hide a real bug.
    stderr = f"{AGG}:3:1: error: unknown type name 'foo'\n"
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == set()


def test_warning_chain_is_not_treated_as_failure():
    # An include chain that only produces a warning attributes nothing.
    stderr = (
        f"In file included from {AGG}:5:\n"
        "/x/c.h:9:1: warning: deprecated\n"
    )
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == set()


def test_out_of_range_line_ignored():
    # A line number beyond the header count is ignored (defensive).
    stderr = (
        f"In file included from {AGG}:99:\n"
        "/x/d.h:1:1: error: boom\n"
    )
    assert _headers_failing_in_aggregate(stderr, AGG, 40) == set()

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

"""``sources.method`` ownership in sources-root promotion (ADR-091 R13).

The retired singular ``source:`` block was promoted from the sources root only
in dump/scan shapes, and never in a compare shape, so a checkout's method
survived compare-shaped runs. ``sources.method`` now lives inside ``sources:``,
which is promoted whole, so ``sources_method_from_sources_root=False`` keeps the
checkout's method and drops the sources root's. The expected dicts below are
written out by hand, not derived from the function.
"""

from __future__ import annotations

import pytest

from abicheck.action_config_overlay import apply_sources_root_config_blocks

_CHECKOUT = {
    "sources": {"method": "headers", "graph": "summary"},
    "build": {"system": "bazel"},
}


@pytest.mark.parametrize(
    ("sources_doc", "expected_sources"),
    [
        # Sources root names its own method: the checkout's wins, the root's is dropped.
        (
            {"sources": {"method": "s6", "public_headers": ["inc/a.h"]}},
            {
                "method": "headers",
                "public_headers": ["inc/a.h"],
            },
        ),
        # Sources root names no method: the checkout's method is still kept.
        (
            {"sources": {"public_headers": ["inc/a.h"]}},
            {
                "method": "headers",
                "public_headers": ["inc/a.h"],
            },
        ),
        # No sources root at all: the checkout keeps only its method.
        (None, {"method": "headers"}),
    ],
)
def test_compare_shape_keeps_the_checkout_method(sources_doc, expected_sources) -> None:
    out = apply_sources_root_config_blocks(
        _CHECKOUT,
        sources_doc,
        blocks=("build", "sources"),
        sources_method_from_sources_root=False,
    )
    assert out["sources"] == expected_sources
    assert "build" not in out


def test_dump_shape_replaces_the_checkout_method_by_default() -> None:
    out = apply_sources_root_config_blocks(
        _CHECKOUT,
        {"sources": {"method": "s6"}},
        blocks=("build", "sources"),
    )
    assert out["sources"] == {"method": "s6"}


def test_compare_shape_with_no_checkout_method_and_no_root_method_is_clear() -> None:
    base = {"sources": {"graph": "summary"}}
    out = apply_sources_root_config_blocks(
        base,
        {"sources": {"method": "s6"}},
        blocks=("sources",),
        sources_method_from_sources_root=False,
    )
    assert "sources" not in out
    assert base == {"sources": {"graph": "summary"}}

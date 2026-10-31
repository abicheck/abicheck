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


def test_sources_root_method_is_dropped_when_the_checkout_has_none() -> None:
    """The sources root's method never reaches a compare-shaped overlay, even
    when the checkout names none: the other sources keys still promote."""
    out = apply_sources_root_config_blocks(
        {"build": {"system": "bazel"}},
        {"sources": {"method": "s6", "graph": "summary"}},
        blocks=("build", "sources"),
        sources_method_from_sources_root=False,
    )
    assert out["sources"] == {"graph": "summary"}


def test_non_mapping_sources_root_keeps_only_the_checkout_method() -> None:
    out = apply_sources_root_config_blocks(
        _CHECKOUT,
        ["not", "a", "mapping"],
        blocks=("sources",),
        sources_method_from_sources_root=False,
    )
    assert out["sources"] == {"method": "headers"}


def test_acknowledgment_findings_name_the_current_block_key() -> None:
    from abicheck.buildsource.build_config_acknowledgment import (
        acknowledgment_findings,
    )

    findings = acknowledgment_findings(
        {"file": 7, "unacknowledged_additions": "nope", "breaks": "nope", "bogus": 1}
    )
    assert any(f.startswith("acknowledgments.file must be") for f in findings)
    assert any(
        f.startswith("acknowledgments.unacknowledged_additions must be one of")
        for f in findings
    )
    assert any(f.startswith("acknowledgments.breaks must be one of") for f in findings)
    assert any(
        "unknown .abicheck.yml key acknowledgments.'bogus'" in f for f in findings
    )
    assert not any(
        "acknowledgment." in f and "acknowledgments." not in f for f in findings
    )


def test_invalid_sources_method_names_the_current_key() -> None:
    from abicheck.workflows.compare_configuration import (
        CompareSettingsError,
        resolve_compare_collect_mode,
    )

    with pytest.raises(CompareSettingsError, match="sources.method in .abicheck.yml"):
        resolve_compare_collect_mode(None, "s9", None, None, None, None)


def test_valid_sources_method_labels_its_current_key() -> None:
    from abicheck.workflows.compare_configuration import resolve_compare_collect_mode

    _mode, label = resolve_compare_collect_mode(None, "s1", None, None, None, None)
    assert label == "sources.method=s1 (.abicheck.yml)"

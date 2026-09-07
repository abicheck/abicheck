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

"""Workstream G S1: the "what changed / review actions" surface-first
section (``report.surface_changes``).

Report invariant under test (``docs/contribute/plans/
vision-api-abi-evolution.md``, workstream G): *"Compatible additions are
visible changes: a compatible run still itemizes what was added; '0
breaking' is not 'nothing happened'."* The section groups every detected
finding into additions/removals/modifications regardless of severity, so a
fully-compatible run still lists its additions, and a reviewer can read each
entry's old/new declaration directly rather than opening the raw JSON
``changes`` array.
"""

from __future__ import annotations

import json

from abicheck.checker import compare
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.report.surface_changes import (
    SurfaceChangeSection,
    compute_surface_changes,
)


def _snapshots() -> tuple[AbiSnapshot, AbiSnapshot]:
    """One addition, one removal, one signature-changed function, one
    unchanged function."""
    old = AbiSnapshot(library="libfoo", version="1.0")
    new = AbiSnapshot(library="libfoo", version="2.0")

    gone = Function(
        name="foo::gone",
        mangled="_ZN3foo4goneEv",
        return_type="void",
        visibility=Visibility.PUBLIC,
    )
    old.functions.append(gone)

    stay = Function(
        name="foo::stay",
        mangled="_ZN3foo4stayEv",
        return_type="void",
        visibility=Visibility.PUBLIC,
    )
    old.functions.append(stay)
    new.functions.append(stay)

    changed_old = Function(
        name="foo::changed",
        mangled="_ZN3foo7changedEv",
        return_type="int",
        visibility=Visibility.PUBLIC,
    )
    changed_new = Function(
        name="foo::changed",
        mangled="_ZN3foo7changedEv",
        return_type="double",
        visibility=Visibility.PUBLIC,
    )
    old.functions.append(changed_old)
    new.functions.append(changed_new)

    new_fn = Function(
        name="foo::brand_new",
        mangled="_ZN3foo9brand_newEv",
        return_type="void",
        visibility=Visibility.PUBLIC,
    )
    new.functions.append(new_fn)

    return old, new


def test_compute_surface_changes_groups_by_review_action() -> None:
    old, new = _snapshots()
    result = compare(old, new)
    section = compute_surface_changes(result)

    assert {e.symbol for e in section.additions} == {"_ZN3foo9brand_newEv"}
    assert {e.symbol for e in section.removals} == {"_ZN3foo4goneEv"}
    assert {e.symbol for e in section.modifications} == {"_ZN3foo7changedEv"}
    assert section.total == 3


def test_removal_entry_carries_its_old_declaration() -> None:
    old, new = _snapshots()
    result = compare(old, new)
    section = compute_surface_changes(result)
    (removal,) = section.removals
    assert removal.old_declaration is not None
    assert removal.new_declaration is None


def test_modification_entry_carries_both_declarations() -> None:
    old, new = _snapshots()
    result = compare(old, new)
    section = compute_surface_changes(result)
    (modification,) = section.modifications
    assert modification.old_declaration is not None
    assert modification.new_declaration is not None
    assert modification.old_declaration != modification.new_declaration


def test_compatible_additions_are_visible_even_on_a_clean_run() -> None:
    """The report invariant, exercised directly: a run with only an
    addition (no breaking/removed/modified finding at all) still lists it,
    the way a severity-grouped summary might collapse it into "0 breaking"
    and stop there."""
    old = AbiSnapshot(library="libfoo", version="1.0")
    new = AbiSnapshot(library="libfoo", version="2.0")
    new.functions.append(
        Function(
            name="foo::only_addition",
            mangled="_ZN3foo13only_additionEv",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
    )
    result = compare(old, new)
    assert result.verdict.value.lower() == "compatible"
    section = compute_surface_changes(result)
    assert section.removals == ()
    assert section.modifications == ()
    assert len(section.additions) == 1
    assert section.additions[0].symbol == "_ZN3foo13only_additionEv"


def test_surface_change_section_round_trips_through_to_dict() -> None:
    old, new = _snapshots()
    result = compare(old, new)
    section = compute_surface_changes(result)
    rebuilt = SurfaceChangeSection.from_dict(section.to_dict())
    assert rebuilt == section


def test_json_report_carries_the_surface_changes_block() -> None:
    from abicheck import reporter

    old, new = _snapshots()
    result = compare(old, new)
    report = json.loads(reporter.to_json(result))
    block = report["surface_changes"]
    assert block["total"] == 3
    assert len(block["additions"]) == 1
    assert len(block["removals"]) == 1
    assert len(block["modifications"]) == 1


def test_review_digest_and_full_markdown_itemize_every_group() -> None:
    from abicheck.reporter_markdown import (
        _to_markdown_leaf,
        _to_markdown_root_cause,
        to_markdown,
        to_review_digest,
    )

    old, new = _snapshots()
    result = compare(old, new)

    digest = to_review_digest(result)
    assert "## Surface changes" not in digest  # digest has no section heading
    assert "Additions" in digest and "Removals" in digest
    assert "_ZN3foo9brand_newEv" in digest
    assert "_ZN3foo4goneEv" in digest

    for render in (to_markdown, _to_markdown_leaf, _to_markdown_root_cause):
        text = render(result)
        assert "## Surface changes" in text, render.__name__
        assert "_ZN3foo9brand_newEv" in text, render.__name__
        assert "_ZN3foo4goneEv" in text, render.__name__


def test_surface_changes_honors_show_only_like_every_other_section() -> None:
    """A finding ``--show-only`` filtered out of the severity groups must
    not reappear in ``surface_changes`` -- the section must group the same
    *displayed* findings the rest of the view shows, not the raw
    ``result.changes``."""
    from abicheck import reporter
    from abicheck.reporter_markdown import (
        _to_markdown_leaf,
        _to_markdown_root_cause,
        to_markdown,
    )

    old, new = _snapshots()
    result = compare(old, new)

    for payload_fn in (
        lambda: reporter.to_json(result, show_only="breaking"),
        lambda: reporter.to_json(result, report_mode="leaf", show_only="breaking"),
        lambda: reporter.to_json(
            result, report_mode="root-cause", show_only="breaking"
        ),
    ):
        block = json.loads(payload_fn())["surface_changes"]
        assert block["additions"] == [], payload_fn

    for render in (to_markdown, _to_markdown_leaf, _to_markdown_root_cause):
        text = render(result, show_only="breaking")
        assert "_ZN3foo9brand_newEv" not in text, render.__name__


def test_a_run_with_no_changes_renders_no_surface_changes_section() -> None:
    old = AbiSnapshot(library="libfoo", version="1.0")
    new = AbiSnapshot(library="libfoo", version="1.0")
    result = compare(old, new)
    section = compute_surface_changes(result)
    assert section.total == 0

    from abicheck.reporter_markdown import to_markdown

    assert "## Surface changes" not in to_markdown(result)

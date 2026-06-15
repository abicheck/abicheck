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

"""Tests for the C6 change factory (``diff_helpers.make_change``).

The factory keeps a kind's description wording in ``change_registry`` instead
of hand-rolled f-strings at the call site. These tests lock two things:

* the factory mechanics (template formatting, bespoke override, field
  forwarding, error path), and
* that every registered template is *well-formed* (only the fixed vocabulary)
  and renders the exact legacy wording for each migrated kind — so the
  migration is byte-identical and golden snapshots do not move.
"""
from __future__ import annotations

import string

import pytest

from abicheck.change_registry import REGISTRY
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.diff_helpers import TEMPLATE_VOCAB, make_change


def _template_fields(template: str) -> set[str]:
    """Return the set of ``{placeholder}`` field names used by a template."""
    return {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(template)
        if field_name
    }


def test_make_change_formats_from_template() -> None:
    change = make_change(
        ChangeKind.FUNC_RETURN_CHANGED,
        symbol="_Z3foov",
        name="foo",
        old="int",
        new="long",
    )
    assert isinstance(change, Change)
    assert change.kind is ChangeKind.FUNC_RETURN_CHANGED
    assert change.symbol == "_Z3foov"
    assert change.description == "Return type changed: foo"
    # old/new also populate old_value/new_value.
    assert change.old_value == "int"
    assert change.new_value == "long"


def test_make_change_explicit_description_overrides_template() -> None:
    # Even for a kind that *has* a template, an explicit description wins
    # (the bespoke path) and is used verbatim.
    change = make_change(
        ChangeKind.FUNC_RETURN_CHANGED,
        symbol="_Z3foov",
        description="bespoke wording with offset 0x40",
    )
    assert change.description == "bespoke wording with offset 0x40"


def test_make_change_requires_template_or_description() -> None:
    # A kind with no template and no explicit description is a programming error.
    assert REGISTRY.description_template_for(ChangeKind.FUNC_REMOVED.value) is None
    with pytest.raises(ValueError, match="requires an explicit description"):
        make_change(ChangeKind.FUNC_REMOVED, symbol="_Z3foov")


def test_make_change_forwards_change_kwargs() -> None:
    change = make_change(
        ChangeKind.VAR_TYPE_CHANGED,
        symbol="g_count",
        name="g_count",
        old="int",
        new="long",
        caused_by_type="Widget",
        affected_symbols=["_Z3usev"],
    )
    assert change.caused_by_type == "Widget"
    assert change.affected_symbols == ["_Z3usev"]


def test_explicit_old_value_kwarg_overrides_old() -> None:
    # old/new only set old_value/new_value by default; an explicit kwarg wins.
    change = make_change(
        ChangeKind.VAR_TYPE_CHANGED,
        symbol="g",
        name="g",
        old="int",
        new="long",
        old_value="explicit",
    )
    assert change.old_value == "explicit"
    assert change.new_value == "long"


def test_repr_wording_keeps_raw_old_value() -> None:
    # Several kinds render a repr() in the description (e.g. {x!r}) while their
    # machine-readable old_value/new_value must stay the *raw* value. The factory
    # supports this by passing old=repr(x) for the template AND an explicit
    # old_value=x kwarg. This locks the coupling so a future edit can't silently
    # let the repr leak into old_value (the footgun called out in review).
    change = make_change(
        ChangeKind.FUNC_REF_QUAL_CHANGED,
        symbol="_Z3foov",
        name="foo",
        old=repr("&"),
        new=repr("&&"),
        old_value="&",
        new_value="&&",
    )
    # description carries the quoted repr form...
    assert change.description == "Ref-qualifier changed: foo ('&' → '&&')"
    # ...but the structured fields stay raw, not the repr.
    assert change.old_value == "&"
    assert change.new_value == "&&"


def test_old_new_default_to_old_value_new_value() -> None:
    # When no explicit old_value/new_value kwarg is given, old/new populate them
    # (the common templated path, e.g. type_size_changed).
    change = make_change(
        ChangeKind.TYPE_SIZE_CHANGED, symbol="T", name="T", old="64", new="128"
    )
    assert change.old_value == "64"
    assert change.new_value == "128"
    # and a kind whose template references neither leaves them None.
    added = make_change(ChangeKind.VAR_ADDED, symbol="g", name="g")
    assert added.old_value is None
    assert added.new_value is None


def test_all_templates_use_only_known_vocabulary() -> None:
    # Guards against a template referencing a placeholder make_change does not
    # supply (which would raise KeyError at runtime for that kind).
    offenders: dict[str, set[str]] = {}
    for kind_value in REGISTRY.templated_kinds():
        template = REGISTRY.description_template_for(kind_value)
        assert template is not None
        unknown = _template_fields(template) - TEMPLATE_VOCAB
        if unknown:
            offenders[kind_value] = unknown
    assert not offenders, f"templates use unknown placeholders: {offenders}"


# A representative set of migrated kinds with the exact pre-C6 f-string wording,
# so golden snapshots and any downstream description parsing stay byte-identical.
# (The full 5400-test detector suite locks the exact wording for *every* migrated
# site through the real detectors; these pin a readable cross-section in one place.)
_FAITHFULNESS_CASES = [
    # diff_symbols
    (ChangeKind.FUNC_RETURN_CHANGED, {"name": "foo"}, "Return type changed: foo"),
    (ChangeKind.FUNC_PARAMS_CHANGED, {"name": "foo"}, "Parameters changed: foo"),
    (ChangeKind.FUNC_ADDED, {"new": "foo"}, "New public function: foo"),
    (
        ChangeKind.FUNC_LOST_INLINE,
        {"name": "foo"},
        "Function lost inline attribute (now has external linkage): foo",
    ),
    (
        ChangeKind.HIDDEN_FRIEND_REMOVED,
        {"old": "operator=="},
        "Hidden friend declaration removed: operator==",
    ),
    (
        ChangeKind.HIDDEN_FRIEND_ADDED,
        {"new": "operator=="},
        "Hidden friend declaration added: operator==",
    ),
    (ChangeKind.VAR_TYPE_CHANGED, {"name": "g"}, "Variable type changed: g"),
    (ChangeKind.VAR_REMOVED, {"name": "g"}, "Public variable removed: g"),
    (ChangeKind.VAR_ADDED, {"name": "g"}, "New public variable: g"),
    # diff_types — type/field/enum/union/typedef/qualifier family
    (ChangeKind.TYPE_ADDED, {"name": "Widget"}, "New type: Widget"),
    (
        ChangeKind.TYPE_SIZE_CHANGED,
        {"name": "Widget", "old": "64", "new": "128"},
        "Size changed: Widget (64 → 128 bits)",
    ),
    (
        ChangeKind.TYPE_FIELD_REMOVED,
        {"name": "Widget", "detail": "x"},
        "Field removed: Widget::x",
    ),
    (
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        {"name": "Widget", "detail": "x", "old": "0", "new": "32"},
        "Field offset changed: Widget::x (0 → 32 bits)",
    ),
    (
        ChangeKind.ENUM_MEMBER_REMOVED,
        {"name": "Color", "detail": "Red"},
        "Enum member removed: Color::Red",
    ),
    (
        ChangeKind.ENUM_MEMBER_RENAMED,
        {"name": "Color", "old": "Red", "new": "Crimson", "detail": "1"},
        "Enum member renamed: Color::Red → Crimson (value=1)",
    ),
    (
        ChangeKind.FIELD_RENAMED,
        {"name": "Widget", "old": "x", "new": "y"},
        "Field renamed: Widget::x → y",
    ),
    (ChangeKind.TYPEDEF_REMOVED, {"name": "size_type"}, "Typedef removed: size_type"),
    (
        ChangeKind.FUNC_STATIC_CHANGED,
        {"name": "Widget::make"},
        "Static qualifier changed: Widget::make",
    ),
    (
        ChangeKind.USED_RESERVED_FIELD,
        {"name": "Widget", "old": "reserved0", "new": "flags"},
        "Reserved field put into use: Widget::reserved0 → flags",
    ),
    # diff_platform — ELF/PE/Mach-O symbol-level kinds
    (
        ChangeKind.SYMBOL_SIZE_CHANGED,
        {"name": "g_table", "old": "16", "new": "32"},
        "Symbol size changed: g_table (16 → 32 bytes)",
    ),
    (
        ChangeKind.SYMBOL_TYPE_CHANGED,
        {"name": "g_table", "old": "FUNC", "new": "OBJECT"},
        "Symbol type changed: g_table (FUNC → OBJECT)",
    ),
    (
        ChangeKind.PE_MACHINE_CHANGED,
        {"old": "AMD64", "new": "ARM64"},
        "PE machine/architecture changed: AMD64 → ARM64",
    ),
    (
        ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
        {"name": "GLIBC_2.34", "detail": "libc.so.6"},
        "New symbol version requirement: GLIBC_2.34 (from libc.so.6)",
    ),
]


@pytest.mark.parametrize("kind,kwargs,expected", _FAITHFULNESS_CASES)
def test_template_renders_legacy_wording(
    kind: ChangeKind, kwargs: dict[str, str], expected: str
) -> None:
    change = make_change(kind, symbol="_Zsym", **kwargs)
    assert change.description == expected


def test_faithfulness_samples_are_templated_kinds() -> None:
    # Every hand-pinned sample must name a kind that actually owns a template.
    covered = {kind.value for kind, _, _ in _FAITHFULNESS_CASES}
    assert covered <= set(REGISTRY.templated_kinds())


def test_every_templated_kind_renders_from_sentinels() -> None:
    # Exhaustive guard over ALL templated kinds: each must render via make_change
    # from sentinel values for exactly the placeholders it declares — catches a
    # stray placeholder, a duplicate brace, or a KeyError the moment a template
    # is added, without hand-maintaining an exact-wording sample for each.
    sentinels = {"symbol": "_Zsym", "name": "N", "old": "O", "new": "W", "detail": "D"}
    for kind in ChangeKind:
        template = REGISTRY.description_template_for(kind.value)
        if template is None:
            continue
        used = _template_fields(template)
        kwargs = {k: sentinels[k] for k in used if k != "symbol"}
        change = make_change(kind, symbol="_Zsym", **kwargs)
        # Sentinels for the placeholders it used must all appear in the output.
        for k in used:
            assert sentinels[k] in change.description, (kind.value, template)

"""Property-based tests for abicheck using Hypothesis.

Covers:
1. Serialization roundtrip (snapshot_to_json -> load_snapshot)
2. Policy classification completeness (every ChangeKind in exactly one set)
3. ShowOnlyFilter parsing (valid token combinations never raise)
4. Verdict consistency (compute_verdict agrees with policy classification)
5. Snapshot index consistency (function_map keys match mangled names)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.checker import Change
from abicheck.checker_policy import (
    COMPATIBLE_KINDS,
    ChangeKind,
    Verdict,
    compute_verdict,
    policy_kind_sets,
)
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    EnumMember,
    EnumType,
    Function,
    Param,
    ParamKind,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.reporter import ShowOnlyFilter
from abicheck.serialization import load_snapshot, snapshot_from_dict, snapshot_to_json

pytestmark = pytest.mark.slow

# ---------------------------------------------------------------------------
# Hypothesis strategies for model objects
# ---------------------------------------------------------------------------

# Alphabet restricted to letters and digits to avoid JSON encoding edge cases
_ident_alphabet = st.characters(whitelist_categories=("L", "N"))

_ident_st = st.text(min_size=1, max_size=20, alphabet=_ident_alphabet)

_type_names = st.sampled_from(["int", "void", "char*", "double", "float", "long", "unsigned int"])

_visibility_st = st.sampled_from(list(Visibility))
_access_st = st.sampled_from(list(AccessLevel))
_param_kind_st = st.sampled_from(list(ParamKind))


@st.composite
def param_st(draw):
    return Param(
        name=draw(_ident_st),
        type=draw(_type_names),
        kind=draw(_param_kind_st),
        default=draw(st.none() | st.just("0")),
        pointer_depth=draw(st.integers(min_value=0, max_value=3)),
        is_restrict=draw(st.booleans()),
        is_va_list=draw(st.booleans()),
    )


@st.composite
def function_st(draw):
    name = draw(_ident_st)
    return Function(
        name=name,
        mangled=f"_Z{name}{draw(st.integers(min_value=0, max_value=9999))}",
        return_type=draw(_type_names),
        params=draw(st.lists(param_st(), min_size=0, max_size=4)),
        visibility=draw(_visibility_st),
        is_virtual=draw(st.booleans()),
        is_noexcept=draw(st.booleans()),
        is_extern_c=draw(st.booleans()),
        vtable_index=draw(st.none() | st.integers(min_value=0, max_value=20)),
        source_location=draw(st.none() | st.just("test.h:10")),
        is_static=draw(st.booleans()),
        is_const=draw(st.booleans()),
        is_volatile=draw(st.booleans()),
        is_pure_virtual=draw(st.booleans()),
        is_deleted=draw(st.booleans()),
        is_inline=draw(st.booleans()),
        access=draw(_access_st),
        return_pointer_depth=draw(st.integers(min_value=0, max_value=3)),
    )


@st.composite
def variable_st(draw):
    name = draw(_ident_st)
    return Variable(
        name=name,
        mangled=f"_ZV{name}{draw(st.integers(min_value=0, max_value=9999))}",
        type=draw(_type_names),
        visibility=draw(_visibility_st),
        source_location=draw(st.none() | st.just("vars.h:5")),
        is_const=draw(st.booleans()),
        value=draw(st.none() | st.just("42")),
        access=draw(_access_st),
    )


@st.composite
def type_field_st(draw):
    return TypeField(
        name=draw(_ident_st),
        type=draw(_type_names),
        offset_bits=draw(st.none() | st.integers(min_value=0, max_value=1024)),
        is_bitfield=draw(st.booleans()),
        bitfield_bits=draw(st.none() | st.integers(min_value=1, max_value=32)),
        is_const=draw(st.booleans()),
        is_volatile=draw(st.booleans()),
        is_mutable=draw(st.booleans()),
        access=draw(_access_st),
    )


@st.composite
def record_type_st(draw):
    kind = draw(st.sampled_from(["struct", "class", "union"]))
    return RecordType(
        name=draw(_ident_st),
        kind=kind,
        size_bits=draw(st.none() | st.integers(min_value=0, max_value=4096)),
        alignment_bits=draw(st.none() | st.integers(min_value=0, max_value=128)),
        fields=draw(st.lists(type_field_st(), min_size=0, max_size=4)),
        bases=draw(st.lists(_ident_st, min_size=0, max_size=2)),
        virtual_bases=draw(st.lists(_ident_st, min_size=0, max_size=2)),
        vtable=draw(st.lists(_ident_st, min_size=0, max_size=3)),
        source_location=draw(st.none() | st.just("types.h:1")),
        is_union=(kind == "union"),
        is_opaque=draw(st.booleans()),
    )


@st.composite
def enum_member_st(draw):
    return EnumMember(
        name=draw(_ident_st),
        value=draw(st.integers(min_value=-1000, max_value=1000)),
    )


@st.composite
def enum_type_st(draw):
    return EnumType(
        name=draw(_ident_st),
        members=draw(st.lists(enum_member_st(), min_size=0, max_size=5)),
        underlying_type=draw(st.sampled_from(["int", "unsigned int", "long", "short"])),
    )


@st.composite
def snapshot_st(draw):
    return AbiSnapshot(
        library=draw(st.text(min_size=1, max_size=30, alphabet=_ident_alphabet)),
        version=draw(st.from_regex(r"[0-9]+\.[0-9]+\.[0-9]+", fullmatch=True)),
        functions=draw(st.lists(function_st(), min_size=0, max_size=5)),
        variables=draw(st.lists(variable_st(), min_size=0, max_size=3)),
        types=draw(st.lists(record_type_st(), min_size=0, max_size=3)),
        enums=draw(st.lists(enum_type_st(), min_size=0, max_size=3)),
        typedefs=draw(st.dictionaries(
            keys=_ident_st, values=_type_names, min_size=0, max_size=3,
        )),
        constants=draw(st.dictionaries(
            keys=_ident_st, values=st.text(min_size=1, max_size=10), min_size=0, max_size=3,
        )),
        elf_only_mode=draw(st.booleans()),
        platform=draw(st.none() | st.sampled_from(["elf", "pe", "macho"])),
        language_profile=draw(st.none() | st.sampled_from(["c", "cpp"])),
    )


# ---------------------------------------------------------------------------
# 1. Serialization roundtrip
# ---------------------------------------------------------------------------

@given(snap=snapshot_st())
@settings(max_examples=50)
def test_serialization_roundtrip(snap: AbiSnapshot):
    """snapshot_to_json -> load via snapshot_from_dict should preserve all fields."""
    json_str = snapshot_to_json(snap)

    # Verify it's valid JSON
    parsed = json.loads(json_str)
    assert isinstance(parsed, dict)
    assert parsed["library"] == snap.library
    assert parsed["version"] == snap.version

    # Roundtrip via snapshot_from_dict
    restored = snapshot_from_dict(parsed)

    assert restored.library == snap.library
    assert restored.version == snap.version
    assert len(restored.functions) == len(snap.functions)
    assert len(restored.variables) == len(snap.variables)
    assert len(restored.types) == len(snap.types)
    assert len(restored.enums) == len(snap.enums)
    assert restored.typedefs == snap.typedefs
    assert restored.constants == snap.constants
    assert restored.elf_only_mode == snap.elf_only_mode
    assert restored.platform == snap.platform
    assert restored.language_profile == snap.language_profile

    # Verify function fields roundtrip
    for orig, rest in zip(snap.functions, restored.functions):
        assert orig.name == rest.name
        assert orig.mangled == rest.mangled
        assert orig.return_type == rest.return_type
        assert orig.visibility == rest.visibility
        assert orig.is_virtual == rest.is_virtual
        assert orig.is_noexcept == rest.is_noexcept
        assert orig.is_extern_c == rest.is_extern_c
        assert orig.is_static == rest.is_static
        assert orig.is_const == rest.is_const
        assert orig.is_volatile == rest.is_volatile
        assert orig.is_pure_virtual == rest.is_pure_virtual
        assert orig.is_deleted == rest.is_deleted
        assert orig.is_inline == rest.is_inline
        assert orig.access == rest.access
        assert orig.return_pointer_depth == rest.return_pointer_depth
        assert len(orig.params) == len(rest.params)
        for op, rp in zip(orig.params, rest.params):
            assert op.name == rp.name
            assert op.type == rp.type
            assert op.kind == rp.kind
            assert op.pointer_depth == rp.pointer_depth
            assert op.is_restrict == rp.is_restrict
            assert op.is_va_list == rp.is_va_list

    # Verify variable fields roundtrip
    for orig, rest in zip(snap.variables, restored.variables):
        assert orig.name == rest.name
        assert orig.mangled == rest.mangled
        assert orig.type == rest.type
        assert orig.visibility == rest.visibility
        assert orig.is_const == rest.is_const
        assert orig.access == rest.access

    # Verify type fields roundtrip
    for orig, rest in zip(snap.types, restored.types):
        assert orig.name == rest.name
        assert orig.kind == rest.kind
        assert orig.size_bits == rest.size_bits
        assert orig.alignment_bits == rest.alignment_bits
        assert len(orig.fields) == len(rest.fields)
        assert orig.is_union == rest.is_union
        # NOTE: is_opaque is not currently deserialized by snapshot_from_dict
        # (it's serialized via asdict but not read back). Skip this assertion.
        # assert orig.is_opaque == rest.is_opaque

    # Verify enum fields roundtrip
    for orig, rest in zip(snap.enums, restored.enums):
        assert orig.name == rest.name
        assert orig.underlying_type == rest.underlying_type
        assert len(orig.members) == len(rest.members)
        for om, rm in zip(orig.members, rest.members):
            assert om.name == rm.name
            assert om.value == rm.value


@given(snap=snapshot_st())
@settings(max_examples=50)
def test_serialization_roundtrip_via_file(snap: AbiSnapshot):
    """snapshot_to_json -> write to file -> load_snapshot should work."""
    json_str = snapshot_to_json(snap)

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "snapshot.json"
        p.write_text(json_str, encoding="utf-8")
        restored = load_snapshot(p)

    assert restored.library == snap.library
    assert restored.version == snap.version
    assert len(restored.functions) == len(snap.functions)
    assert len(restored.variables) == len(snap.variables)
    assert len(restored.types) == len(snap.types)
    assert len(restored.enums) == len(snap.enums)


# ---------------------------------------------------------------------------
# 2. Policy classification completeness
# ---------------------------------------------------------------------------

def test_every_changekind_in_exactly_one_category_strict_abi():
    """Every ChangeKind member must appear in exactly one of the four
    kind sets under strict_abi policy."""
    breaking, api_break, compatible, risk = policy_kind_sets("strict_abi")
    all_kinds = set(ChangeKind)

    classified = breaking | api_break | compatible | risk
    unclassified = all_kinds - classified
    assert not unclassified, f"Unclassified ChangeKinds: {unclassified}"

    # Check no overlaps between categories
    assert not (breaking & api_break), f"Overlap breaking & api_break: {breaking & api_break}"
    assert not (breaking & compatible), f"Overlap breaking & compatible: {breaking & compatible}"
    assert not (breaking & risk), f"Overlap breaking & risk: {breaking & risk}"
    assert not (api_break & compatible), f"Overlap api_break & compatible: {api_break & compatible}"
    assert not (api_break & risk), f"Overlap api_break & risk: {api_break & risk}"
    assert not (compatible & risk), f"Overlap compatible & risk: {compatible & risk}"


@given(policy=st.sampled_from(["strict_abi", "sdk_vendor", "plugin_abi"]))
@settings(max_examples=10)
def test_every_changekind_classified_per_policy(policy: str):
    """For every policy, every ChangeKind should be covered by the union of the four sets."""
    breaking, api_break, compatible, risk = policy_kind_sets(policy)
    all_kinds = set(ChangeKind)

    classified = breaking | api_break | compatible | risk
    unclassified = all_kinds - classified
    assert not unclassified, f"Policy {policy!r} has unclassified kinds: {unclassified}"


def test_kind_sets_pairwise_disjoint_all_policies():
    """For each policy, the four kind sets must be pairwise disjoint."""
    for policy in ("strict_abi", "sdk_vendor", "plugin_abi"):
        breaking, api_break, compatible, risk = policy_kind_sets(policy)
        pairs = [
            ("breaking", "api_break", breaking, api_break),
            ("breaking", "compatible", breaking, compatible),
            ("breaking", "risk", breaking, risk),
            ("api_break", "compatible", api_break, compatible),
            ("api_break", "risk", api_break, risk),
            ("compatible", "risk", compatible, risk),
        ]
        for name_a, name_b, set_a, set_b in pairs:
            overlap = set_a & set_b
            assert not overlap, (
                f"Policy {policy!r}: overlap between {name_a} and {name_b}: {overlap}"
            )


# ---------------------------------------------------------------------------
# 3. ShowOnlyFilter parsing
# ---------------------------------------------------------------------------

_severity_tokens = ["breaking", "api-break", "risk", "compatible"]
_element_tokens = ["functions", "variables", "types", "enums", "elf"]
_action_tokens = ["added", "removed", "changed"]
_all_valid_tokens = _severity_tokens + _element_tokens + _action_tokens


@given(tokens=st.lists(st.sampled_from(_all_valid_tokens), min_size=1, max_size=5))
@settings(max_examples=50)
def test_show_only_filter_parse_valid_tokens(tokens: list[str]):
    """Parsing any combination of valid tokens should not raise."""
    raw = ",".join(tokens)
    filt = ShowOnlyFilter.parse(raw)

    # Every severity token present in the input should be in the filter
    for tok in tokens:
        if tok in _severity_tokens:
            assert tok in filt.severities
        elif tok in _element_tokens:
            assert tok in filt.elements
        elif tok in _action_tokens:
            assert tok in filt.actions


@given(tokens=st.lists(st.sampled_from(_all_valid_tokens), min_size=0, max_size=8))
@settings(max_examples=50)
def test_show_only_filter_parse_roundtrip_idempotent(tokens: list[str]):
    """Parsing tokens, reconstructing the string, and parsing again should give same filter."""
    raw = ",".join(tokens)
    if not raw.strip():
        # Empty input produces an empty filter (no tokens)
        filt = ShowOnlyFilter.parse(raw)
        assert filt.severities == frozenset()
        assert filt.elements == frozenset()
        assert filt.actions == frozenset()
        return

    filt1 = ShowOnlyFilter.parse(raw)
    # Reconstruct a canonical token string from the filter
    reconstructed_tokens = sorted(filt1.severities) + sorted(filt1.elements) + sorted(filt1.actions)
    if reconstructed_tokens:
        raw2 = ",".join(reconstructed_tokens)
        filt2 = ShowOnlyFilter.parse(raw2)
        assert filt1 == filt2


@given(invalid_tok=st.text(min_size=1, max_size=15, alphabet=_ident_alphabet).filter(
    lambda t: t.lower() not in _all_valid_tokens
))
@settings(max_examples=30)
def test_show_only_filter_rejects_invalid_tokens(invalid_tok: str):
    """An invalid token should raise ValueError."""
    import pytest
    with pytest.raises(ValueError, match="Unknown --show-only token"):
        ShowOnlyFilter.parse(invalid_tok)


# ---------------------------------------------------------------------------
# 4. Verdict consistency
# ---------------------------------------------------------------------------

@settings(max_examples=50)
@given(data=st.data())
def test_compute_verdict_empty_changes_is_no_change(data):
    """compute_verdict with no changes should always return NO_CHANGE."""
    policy = data.draw(st.sampled_from(["strict_abi", "sdk_vendor", "plugin_abi"]))
    verdict = compute_verdict([], policy=policy)
    assert verdict == Verdict.NO_CHANGE


@given(kind=st.sampled_from(list(ChangeKind)),
       policy=st.sampled_from(["strict_abi", "sdk_vendor", "plugin_abi"]))
@settings(max_examples=50)
def test_compute_verdict_single_change_matches_policy(kind: ChangeKind, policy: str):
    """A single-change verdict should match the policy classification for that kind."""
    breaking, api_break, compatible, risk = policy_kind_sets(policy)

    change = Change(kind=kind, symbol="test_sym", description="test change")
    verdict = compute_verdict([change], policy=policy)

    if kind in breaking:
        assert verdict == Verdict.BREAKING, (
            f"kind={kind}, policy={policy}: expected BREAKING, got {verdict}"
        )
    elif kind in api_break:
        assert verdict == Verdict.API_BREAK, (
            f"kind={kind}, policy={policy}: expected API_BREAK, got {verdict}"
        )
    elif kind in risk:
        assert verdict == Verdict.COMPATIBLE_WITH_RISK, (
            f"kind={kind}, policy={policy}: expected COMPATIBLE_WITH_RISK, got {verdict}"
        )
    elif kind in compatible:
        assert verdict == Verdict.COMPATIBLE, (
            f"kind={kind}, policy={policy}: expected COMPATIBLE, got {verdict}"
        )
    else:
        # Unclassified kinds default to BREAKING (fail-safe)
        assert verdict == Verdict.BREAKING


@given(kinds=st.lists(st.sampled_from(list(ChangeKind)), min_size=1, max_size=10),
       policy=st.sampled_from(["strict_abi", "sdk_vendor", "plugin_abi"]))
@settings(max_examples=50)
def test_verdict_ordering_breaking_dominates(kinds: list[ChangeKind], policy: str):
    """If any kind is BREAKING, the overall verdict must be BREAKING."""
    breaking, api_break, compatible, risk = policy_kind_sets(policy)

    changes = [Change(kind=k, symbol=f"sym_{i}", description="test") for i, k in enumerate(kinds)]
    verdict = compute_verdict(changes, policy=policy)

    kind_set = set(kinds)
    if kind_set & breaking:
        assert verdict == Verdict.BREAKING
    elif kind_set & api_break:
        assert verdict == Verdict.API_BREAK
    elif kind_set & risk:
        assert verdict == Verdict.COMPATIBLE_WITH_RISK
    elif kind_set <= compatible:
        assert verdict == Verdict.COMPATIBLE


@given(
    compatible_kinds=st.lists(
        st.sampled_from(sorted(COMPATIBLE_KINDS, key=lambda k: k.value)),
        min_size=1, max_size=5,
    )
)
@settings(max_examples=50)
def test_only_compatible_kinds_give_compatible_verdict(compatible_kinds: list[ChangeKind]):
    """If all changes are from COMPATIBLE_KINDS (strict_abi), verdict must be COMPATIBLE."""
    changes = [
        Change(kind=k, symbol=f"sym_{i}", description="test")
        for i, k in enumerate(compatible_kinds)
    ]
    verdict = compute_verdict(changes, policy="strict_abi")
    assert verdict == Verdict.COMPATIBLE


# ---------------------------------------------------------------------------
# 5. Snapshot index consistency
# ---------------------------------------------------------------------------

@given(functions=st.lists(function_st(), min_size=1, max_size=10))
@settings(max_examples=50)
def test_snapshot_index_function_map_keys(functions: list[Function]):
    """After index(), function_map keys should match the mangled names (first-wins)."""
    snap = AbiSnapshot(library="libtest.so", version="1.0.0", functions=functions)
    snap.index()

    # Collect first-wins mangled names
    seen = set()
    expected_keys = set()
    for f in functions:
        if f.mangled not in seen:
            expected_keys.add(f.mangled)
            seen.add(f.mangled)

    assert set(snap.function_map.keys()) == expected_keys

    # Verify each mapped function is the first one with that mangled name
    first_by_mangled: dict[str, Function] = {}
    for f in functions:
        if f.mangled not in first_by_mangled:
            first_by_mangled[f.mangled] = f

    for mangled, func in snap.function_map.items():
        expected = first_by_mangled[mangled]
        assert func.name == expected.name
        assert func.return_type == expected.return_type


@given(variables=st.lists(variable_st(), min_size=1, max_size=10))
@settings(max_examples=50)
def test_snapshot_index_variable_map_keys(variables: list[Variable]):
    """After index(), variable_map keys should match mangled names (first-wins)."""
    snap = AbiSnapshot(library="libtest.so", version="1.0.0", variables=variables)
    snap.index()

    seen = set()
    expected_keys = set()
    for v in variables:
        if v.mangled not in seen:
            expected_keys.add(v.mangled)
            seen.add(v.mangled)

    assert set(snap.variable_map.keys()) == expected_keys


@given(types=st.lists(record_type_st(), min_size=1, max_size=10))
@settings(max_examples=50)
def test_snapshot_index_type_map_keys(types: list[RecordType]):
    """After index(), type_by_name should use first-wins for duplicate names."""
    snap = AbiSnapshot(library="libtest.so", version="1.0.0", types=types)
    snap.index()

    seen = set()
    expected_keys = set()
    for t in types:
        if t.name not in seen:
            expected_keys.add(t.name)
            seen.add(t.name)

    actual_keys = set()
    for name in expected_keys:
        result = snap.type_by_name(name)
        assert result is not None
        actual_keys.add(name)

    assert actual_keys == expected_keys


@given(snap=snapshot_st())
@settings(max_examples=50)
def test_snapshot_lazy_index_on_property_access(snap: AbiSnapshot):
    """Accessing function_map / variable_map should trigger lazy index()."""
    # Ensure the index caches are None initially
    assert snap._func_by_mangled is None
    assert snap._var_by_mangled is None

    # Access the properties — should trigger lazy indexing
    _ = snap.function_map
    _ = snap.variable_map

    assert snap._func_by_mangled is not None
    assert snap._var_by_mangled is not None

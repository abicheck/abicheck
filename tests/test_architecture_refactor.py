"""Tests for the architecture refactoring (Problems A, B, C).

Covers:
- A: ChangeKindRegistry — single-declaration metadata, derived sets
- B: DetectorRegistry — self-registering detectors
- C: PostProcessingPipeline — explicit step pipeline
"""

from __future__ import annotations

import pytest

from abicheck.change_registry import (
    REGISTRY,
    ChangeKindMeta,
    ChangeKindRegistry,
    Verdict,
)
from abicheck.checker_policy import (
    ADDITION_KINDS,
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    IMPACT_TEXT,
    PLUGIN_ABI_DOWNGRADED_KINDS,
    QUALITY_KINDS,
    RISK_KINDS,
    SDK_VENDOR_COMPAT_KINDS,
    ChangeKind,
)

# ─── Part A: ChangeKindRegistry tests ────────────────────────────────────────


class TestChangeKindRegistry:
    """Single-declaration registry replaces scattered metadata."""

    def test_registry_has_all_changekind_members(self):
        """Every ChangeKind enum member has a registry entry."""
        for kind in ChangeKind:
            assert kind.value in REGISTRY, f"{kind.value} missing from registry"

    def test_registry_no_extra_entries(self):
        """Registry has no entries beyond ChangeKind enum members."""
        kind_values = {k.value for k in ChangeKind}
        for entry_key in REGISTRY.entries:
            assert entry_key in kind_values, f"Extra registry entry: {entry_key}"

    def test_breaking_kinds_derived_from_registry(self):
        """BREAKING_KINDS matches registry entries with BREAKING verdict."""
        registry_breaking = {
            ChangeKind(v) for v in REGISTRY.kinds_for_verdict(Verdict.BREAKING)
        }
        assert BREAKING_KINDS == registry_breaking

    def test_compatible_kinds_derived_from_registry(self):
        """COMPATIBLE_KINDS matches registry entries with COMPATIBLE verdict."""
        registry_compat = {
            ChangeKind(v) for v in REGISTRY.kinds_for_verdict(Verdict.COMPATIBLE)
        }
        assert COMPATIBLE_KINDS == registry_compat

    def test_api_break_kinds_derived_from_registry(self):
        """API_BREAK_KINDS matches registry entries with API_BREAK verdict."""
        registry_api = {
            ChangeKind(v) for v in REGISTRY.kinds_for_verdict(Verdict.API_BREAK)
        }
        assert API_BREAK_KINDS == registry_api

    def test_risk_kinds_derived_from_registry(self):
        """RISK_KINDS matches registry entries with COMPATIBLE_WITH_RISK verdict."""
        registry_risk = {
            ChangeKind(v)
            for v in REGISTRY.kinds_for_verdict(Verdict.COMPATIBLE_WITH_RISK)
        }
        assert RISK_KINDS == registry_risk

    def test_addition_kinds_derived_from_registry(self):
        """ADDITION_KINDS matches registry entries with is_addition=True."""
        registry_additions = {ChangeKind(v) for v in REGISTRY.addition_kinds()}
        assert ADDITION_KINDS == registry_additions

    def test_quality_kinds_is_compatible_minus_additions(self):
        """QUALITY_KINDS = COMPATIBLE_KINDS - ADDITION_KINDS."""
        assert QUALITY_KINDS == frozenset(COMPATIBLE_KINDS - ADDITION_KINDS)

    def test_impact_text_derived_from_registry(self):
        """IMPACT_TEXT dict matches registry impact fields."""
        registry_impact = {ChangeKind(k): v for k, v in REGISTRY.impact_text().items()}
        assert IMPACT_TEXT == registry_impact

    def test_sdk_vendor_overrides_from_registry(self):
        """SDK_VENDOR_COMPAT_KINDS matches registry policy_overrides."""
        registry_sdk = {
            ChangeKind(v) for v in REGISTRY.policy_overrides_for("sdk_vendor")
        }
        assert SDK_VENDOR_COMPAT_KINDS == registry_sdk

    def test_plugin_abi_overrides_from_registry(self):
        """PLUGIN_ABI_DOWNGRADED_KINDS matches registry policy_overrides."""
        registry_plugin = {
            ChangeKind(v) for v in REGISTRY.policy_overrides_for("plugin_abi")
        }
        assert PLUGIN_ABI_DOWNGRADED_KINDS == registry_plugin

    def test_duplicate_entry_raises(self):
        """Duplicate kind values in ChangeKindRegistry raise ValueError."""
        import pytest

        entries = [
            ChangeKindMeta("test_kind", Verdict.BREAKING, impact="x"),
            ChangeKindMeta("test_kind", Verdict.COMPATIBLE, impact="x"),
        ]
        with pytest.raises(ValueError, match="Duplicate"):
            ChangeKindRegistry(entries)

    def test_unknown_policy_override_raises(self):
        """A policy_overrides key naming an unrecognized policy is rejected.

        ADR-061 D9's "valid references" catalog-validation property.
        """
        import pytest

        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                policy_overrides={"not_a_real_policy": Verdict.COMPATIBLE},
            ),
        ]
        with pytest.raises(ValueError, match="unknown policy"):
            ChangeKindRegistry(entries)

    def test_strict_abi_policy_override_raises(self):
        """A policy_overrides entry targeting 'strict_abi' is rejected.

        strict_abi's verdict is already default_verdict itself — an
        override under that key would be a second, competing source of
        truth for the same policy (ADR-061 D9's "non-contradictory
        defaults").
        """
        import pytest

        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                policy_overrides={"strict_abi": Verdict.COMPATIBLE},
            ),
        ]
        with pytest.raises(ValueError, match="strict_abi"):
            ChangeKindRegistry(entries)

    def test_redundant_policy_override_raises(self):
        """A policy_overrides value equal to default_verdict is rejected.

        Restating the default under a policy key is not an override — it's
        either stale or was never needed (ADR-061 D9's "non-contradictory
        defaults").
        """
        import pytest

        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                policy_overrides={"plugin_abi": Verdict.BREAKING},
            ),
        ]
        with pytest.raises(ValueError, match="== default_verdict"):
            ChangeKindRegistry(entries)

    def test_addition_kind_must_default_to_compatible(self):
        """is_addition=True with a non-COMPATIBLE default_verdict is rejected.

        addition_kinds() is documented as a subset of COMPATIBLE_KINDS
        (ADR-061 D9's "non-contradictory defaults").
        """
        import pytest

        entries = [
            ChangeKindMeta("test_kind", Verdict.BREAKING, impact="x", is_addition=True),
        ]
        with pytest.raises(ValueError, match="is_addition=True"):
            ChangeKindRegistry(entries)

    @pytest.mark.parametrize("policy", ["sdk_vendor", "plugin_abi"])
    def test_verdict_blind_policy_override_must_be_compatible(self, policy):
        """A non-COMPATIBLE override for sdk_vendor/plugin_abi is rejected.

        checker_policy.policy_kind_sets() classifies every kind carrying a
        'sdk_vendor'/'plugin_abi' override as Verdict.COMPATIBLE
        unconditionally — the declared verdict is never consulted at
        runtime, only the key's presence. A declared value other than
        Verdict.COMPATIBLE would pass the redundant-override check (it
        differs from default_verdict) while silently disagreeing with
        actual runtime behavior, so it must be rejected on its own.

        Parametrized (CodeRabbit review, PR #882) rather than a manual
        loop, so each policy gets its own independent test result.
        """
        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                policy_overrides={policy: Verdict.API_BREAK},
            ),
        ]
        with pytest.raises(ValueError, match="unconditionally"):
            ChangeKindRegistry(entries)

    def test_verdict_blind_policy_matches_runtime_behavior(self):
        """Regression guard: _VERDICT_BLIND_POLICIES must track policy_kind_sets().

        Directly exercises checker_policy.policy_kind_sets() for every
        non-strict_abi built-in policy and confirms the declared override
        verdict genuinely has no effect on classification for each policy
        this module treats as verdict-blind — the empirical fact the new
        validator's rejection rests on, not just an assertion about it.
        """
        from abicheck.checker_policy import policy_kind_sets
        from abicheck.model.change_catalog.registry import (
            _VERDICT_BLIND_POLICIES,
            VALID_BASE_POLICIES,
        )

        for policy in VALID_BASE_POLICIES - {"strict_abi"}:
            if policy not in _VERDICT_BLIND_POLICIES:
                continue
            _breaking, _api, compat, _risk = policy_kind_sets(policy)
            overridden = set(REGISTRY.policy_overrides_for(policy))
            assert overridden, f"{policy!r} has no real overrides to check against"
            assert overridden <= {k.value for k in compat}, (
                f"{policy!r} is in _VERDICT_BLIND_POLICIES but "
                f"policy_kind_sets() doesn't classify its overridden kinds "
                f"as COMPATIBLE — the constant is stale"
            )

    def test_valid_policy_override_is_accepted(self):
        """A genuinely different, known-policy override passes construction."""
        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                policy_overrides={"plugin_abi": Verdict.COMPATIBLE},
            ),
            ChangeKindMeta(
                "compatible_addition", Verdict.COMPATIBLE, impact="x", is_addition=True
            ),
        ]
        registry = ChangeKindRegistry(entries)
        assert len(registry) == 2

    def test_policy_overrides_is_immutable_after_construction(self):
        """policy_overrides can't be mutated post-construction, dict or dataclass.

        ``frozen=True`` only stops reassigning the attribute — it does not
        stop mutating the dict object itself, and a caller can also keep a
        live reference to the dict it passed in. Either path would silently
        invalidate the reference/default checks already run at construction
        without re-running them (Codex review, PR #882).
        """
        import pytest

        source = {"plugin_abi": Verdict.COMPATIBLE}
        entry = ChangeKindMeta("test_kind", Verdict.BREAKING, policy_overrides=source)

        # The dataclass's own copy can't be mutated in place.
        with pytest.raises(TypeError):
            entry.policy_overrides["plugin_abi"] = Verdict.BREAKING

        # Mutating the caller's original dict after construction doesn't
        # reach through to the stored copy either.
        source["plugin_abi"] = Verdict.BREAKING
        assert entry.policy_overrides["plugin_abi"] == Verdict.COMPATIBLE

    def test_policy_overrides_blocks_augmented_union_assignment(self):
        """`entry.policy_overrides |= {...}` cannot silently corrupt the entry.

        ``|=`` is sugar for ``entry.policy_overrides =
        entry.policy_overrides.__ior__({...})`` — dict's own ``__ior__``
        mutates in place and returns self *before* Python attempts the
        reassignment, so on a frozen dataclass the mutation had already
        happened by the time ``FrozenInstanceError`` aborted the (redundant)
        assignment. Every other mutator was already blocked; this inherited
        path was not (Codex review, PR #882, fresh evidence).
        """
        import pytest

        entry = ChangeKindMeta(
            "test_kind", Verdict.BREAKING, impact="x",
            policy_overrides={"plugin_abi": Verdict.COMPATIBLE},
        )
        with pytest.raises(TypeError):
            entry.policy_overrides |= {"unknown": Verdict.API_BREAK}
        assert dict(entry.policy_overrides) == {"plugin_abi": Verdict.COMPATIBLE}

    def test_policy_overrides_blocks_reinit(self):
        """Directly re-invoking the inherited dict.__init__ is blocked too.

        Overriding __setitem__/update/etc. doesn't stop a caller from
        re-invoking dict's own __init__ directly —
        ``entry.policy_overrides.__init__({"unknown": ...})`` mutated the
        already-constructed dict in place via the same C-level population
        path a fresh construction uses, bypassing every overridden mutator
        (Codex review, PR #882, fresh evidence).
        """
        import pytest

        entry = ChangeKindMeta(
            "test_kind", Verdict.BREAKING, impact="x",
            policy_overrides={"plugin_abi": Verdict.COMPATIBLE},
        )
        with pytest.raises(TypeError):
            entry.policy_overrides.__init__({"unknown": Verdict.API_BREAK})
        assert dict(entry.policy_overrides) == {"plugin_abi": Verdict.COMPATIBLE}

    def test_policy_overrides_blocks_base_dict_setitem_bypass(self):
        """Calling ``dict.__setitem__`` directly on the mapping cannot mutate it.

        An earlier ``dict``-subclass design blocked every mutator Python
        reaches through normal attribute/operator resolution
        (``entry.policy_overrides["x"] = y``, ``.update(...)``, ``|=``,
        etc.) — but being a genuine ``dict`` instance meant its storage was
        still reachable through ``dict``'s own *unbound* methods called
        directly: ``dict.__setitem__(entry.policy_overrides, "unknown",
        Verdict.API_BREAK)`` mutated the underlying hash table in C, with no
        Python-level override able to intercept a call to the base type's
        own descriptor (Codex review, PR #882, fresh evidence). Fixed by
        making ``_ImmutableDict`` a read-only ``collections.abc.Mapping``
        rather than a ``dict`` subclass at all, so ``dict.__setitem__``
        rejects it outright as not being a ``dict``.
        """
        import pytest

        entry = ChangeKindMeta(
            "test_kind", Verdict.BREAKING, impact="x",
            policy_overrides={"plugin_abi": Verdict.COMPATIBLE},
        )
        with pytest.raises(TypeError):
            dict.__setitem__(entry.policy_overrides, "unknown", Verdict.API_BREAK)
        assert dict(entry.policy_overrides) == {"plugin_abi": Verdict.COMPATIBLE}

    def test_policy_overrides_blocks_private_data_attribute_bypass(self):
        """Neither the private storage nor the attribute itself is reachable.

        ``entry.policy_overrides._data["unknown"] = Verdict.API_BREAK`` (or
        reassigning ``_data``/``_initialized`` wholesale) would otherwise
        mutate the mapping one attribute access away from every other
        blocked path — "no public mutator" only protects the ``Mapping``
        interface, not a private attribute a caller can still reach (Codex
        review, PR #882, fresh evidence). Fixed by storing ``_data`` as a
        ``types.MappingProxyType`` (so item assignment on it also raises)
        and overriding ``__setattr__`` to reject any attribute write once
        construction has completed.
        """
        import pytest

        entry = ChangeKindMeta(
            "test_kind", Verdict.BREAKING, impact="x",
            policy_overrides={"plugin_abi": Verdict.COMPATIBLE},
        )
        with pytest.raises(TypeError):
            entry.policy_overrides._data["unknown"] = Verdict.API_BREAK
        with pytest.raises(TypeError):
            entry.policy_overrides._data = {"unknown": Verdict.API_BREAK}
        assert dict(entry.policy_overrides) == {"plugin_abi": Verdict.COMPATIBLE}

    def test_policy_overrides_immutability_survives_serialization(self):
        """The immutable policy_overrides still round-trips like an ordinary dict.

        ``types.MappingProxyType`` gives immutability for free but cannot be
        pickled at all — ``dataclasses.asdict()``, ``copy.deepcopy()``, and
        ``pickle.dumps()`` all raised ``TypeError: cannot pickle 'mappingproxy'
        object`` (Codex review, PR #882, fresh evidence). The fix (a
        read-only ``collections.abc.Mapping`` implementation, not a ``dict``
        subclass) must support all three, and does so with two distinct
        contracts depending on *which* object gets copied:

        - ``dataclasses.asdict(entry)['policy_overrides']`` — asdict() never
          calls ``copy.deepcopy()`` on the whole ``ChangeKindMeta`` (it walks
          dataclass fields directly), only on the ``policy_overrides`` field
          value itself, which is ``_ImmutableDict.__deepcopy__`` — an
          ordinary, mutable, JSON-serializable ``dict`` (a non-dict
          ``Mapping`` in that copy would make ``json.dumps(asdict(entry))``
          fail where a plain dict field would have succeeded — Codex
          review, PR #882, fresh evidence).
        - ``copy.deepcopy(entry)`` (the whole ``ChangeKindMeta``) —
          ``ChangeKindMeta.__deepcopy__`` reconstructs via the constructor,
          re-running ``__post_init__``, so the copy's own
          ``policy_overrides`` is a fresh, genuinely immutable
          ``_ImmutableDict`` — a plain mutable dict here would let a
          validated copy be silently corrupted after the fact (e.g. if
          placed back into a registry) with no way to catch it (Codex
          review, PR #882, fresh evidence).
        - ``pickle`` goes through a different mechanism (``__reduce__``) and
          also reconstructs a genuinely immutable ``_ImmutableDict``.

        The *original* entry's own ``policy_overrides`` stays immutable
        throughout, regardless of what any copy of it looks like.
        """
        import copy
        import dataclasses
        import json
        import pickle

        import pytest

        entry = ChangeKindMeta(
            "test_kind", Verdict.BREAKING, impact="x",
            policy_overrides={"plugin_abi": Verdict.COMPATIBLE},
        )

        # asdict(): field-level copy, plain mutable dict, JSON-serializable.
        as_dict = dataclasses.asdict(entry)
        assert as_dict["policy_overrides"] == {"plugin_abi": Verdict.COMPATIBLE}
        assert type(as_dict["policy_overrides"]) is dict
        json.dumps(as_dict["policy_overrides"])  # must not raise
        as_dict["policy_overrides"]["plugin_abi"] = Verdict.BREAKING
        assert entry.policy_overrides["plugin_abi"] == Verdict.COMPATIBLE

        # copy.deepcopy() of the whole entry: genuinely immutable copy.
        deep = copy.deepcopy(entry)
        assert deep.policy_overrides == {"plugin_abi": Verdict.COMPATIBLE}
        assert deep == entry
        with pytest.raises(TypeError):
            deep.policy_overrides["plugin_abi"] = Verdict.BREAKING

        # Directly deep-copying the field value alone (not through the whole
        # entry) still hits _ImmutableDict.__deepcopy__ and stays a plain,
        # mutable dict — this is the asdict()-supporting behavior, pinned
        # independently of ChangeKindMeta.__deepcopy__.
        field_copy = copy.deepcopy(entry.policy_overrides)
        assert type(field_copy) is dict
        field_copy["plugin_abi"] = Verdict.BREAKING
        assert entry.policy_overrides["plugin_abi"] == Verdict.COMPATIBLE

        # pickle: genuinely immutable reconstruction.
        rehydrated = pickle.loads(pickle.dumps(entry))
        assert rehydrated.policy_overrides == {"plugin_abi": Verdict.COMPATIBLE}
        assert rehydrated == entry
        with pytest.raises(TypeError):
            rehydrated.policy_overrides["plugin_abi"] = Verdict.BREAKING

        # The original entry stays immutable throughout all of the above.
        with pytest.raises(TypeError):
            entry.policy_overrides["plugin_abi"] = Verdict.BREAKING

    def test_setstate_normalizes_a_legacy_plain_dict_policy_overrides(self):
        """Loading a pre-``_ImmutableDict`` pickle must still be immutable.

        Pickle's default protocol restores a slotted dataclass by calling
        ``__setstate__`` (when defined) with the value
        ``object.__getstate__()`` produces for a ``__dict__``-less
        instance — a plain list of field values in declaration order, not
        a dict — and never calls ``__init__``/``__post_init__``. A pickle
        produced before ``policy_overrides`` became an ``_ImmutableDict``
        (or any hand-built state carrying a plain dict there) would
        therefore silently install a plain, mutable dict on the restored
        instance, bypassing every validation/immutability guarantee
        ``__post_init__`` establishes (Codex review, PR #882, fresh
        evidence — confirmed against a real pre-fix pickle). Fixed with
        ``ChangeKindMeta.__setstate__``, which normalizes on load
        regardless of which version produced the pickle.
        """
        import pytest

        from abicheck.model.change_catalog.registry import _ImmutableDict

        # Simulate a legacy pickle's restored state: a plain dict, not the
        # _ImmutableDict __post_init__ would have wrapped it into, in the
        # field-declaration-order list shape a slotted dataclass restores
        # from.
        legacy_state = [
            "test_kind", Verdict.BREAKING, "x", False,
            {"plugin_abi": Verdict.COMPATIBLE}, None,
        ]
        assert type(legacy_state[4]) is dict

        restored = object.__new__(ChangeKindMeta)
        restored.__setstate__(legacy_state)

        assert isinstance(restored.policy_overrides, _ImmutableDict)
        assert dict(restored.policy_overrides) == {"plugin_abi": Verdict.COMPATIBLE}
        with pytest.raises(TypeError):
            restored.policy_overrides["unknown"] = Verdict.API_BREAK

    def test_setstate_accepts_a_pre_slots_dict_shaped_pickle(self):
        """A pickle from the immediately preceding, pre-``slots=True`` revision must still load.

        That revision's class had a real ``__dict__``, so its own default
        ``__getstate__`` returned it directly — a dict keyed by field name,
        not the positional list a slotted instance restores from. Treating
        that dict as though it were the new list shape zips field names
        against the dict's own KEYS (``dict.__iter__`` yields keys, not
        values), eventually feeding the literal string
        ``"policy_overrides"`` to ``_ImmutableDict`` and raising
        ``ValueError`` (Codex review, PR #882, fresh evidence — confirmed
        against a real pickle produced by that exact prior revision).
        ``__setstate__`` must handle both shapes.
        """
        from abicheck.model.change_catalog.registry import _ImmutableDict

        legacy_dict_state = {
            "kind": "test_kind",
            "default_verdict": Verdict.BREAKING,
            "impact": "x",
            "is_addition": False,
            "policy_overrides": {"plugin_abi": Verdict.COMPATIBLE},
            "description_template": None,
        }
        restored = object.__new__(ChangeKindMeta)
        restored.__setstate__(legacy_dict_state)

        assert restored.kind == "test_kind"
        assert restored.default_verdict == Verdict.BREAKING
        assert restored.impact == "x"
        assert isinstance(restored.policy_overrides, _ImmutableDict)
        assert dict(restored.policy_overrides) == {"plugin_abi": Verdict.COMPATIBLE}

    def test_setstate_refuses_to_mutate_an_already_initialized_entry(self):
        """``__setstate__`` is an ordinary method, not exclusive to pickle.

        Nothing stops a caller from invoking ``entry.__setstate__(...)``
        directly on an already-initialized, LIVE catalog entry (e.g. one
        obtained via ``REGISTRY.entries``) — a crafted state could install
        an unvalidated ``policy_overrides`` entry or blank the required
        ``impact`` text directly onto a shared, already-trusted catalog
        entry (Codex review, PR #882, fresh evidence). Fixed by refusing
        outright unless ``self`` is still a genuinely blank instance — the
        shape pickle's own restore protocol actually produces.
        """
        import pytest

        entry = ChangeKindMeta(
            "test_kind", Verdict.BREAKING, impact="x",
            policy_overrides={"plugin_abi": Verdict.COMPATIBLE},
        )
        with pytest.raises(TypeError, match="already-initialized"):
            entry.__setstate__([
                "test_kind", Verdict.BREAKING, "x", False,
                {"unknown": Verdict.API_BREAK}, None,
            ])
        # The live entry is completely unaffected by the rejected call.
        assert dict(entry.policy_overrides) == {"plugin_abi": Verdict.COMPATIBLE}

    def test_change_kind_meta_has_no_instance_dict(self):
        """A ``ChangeKindMeta`` has no ``__dict__`` to reach past ``frozen=True`` through.

        Without ``slots=True``, ``frozen=True`` only blocks reassigning an
        attribute (``entry.policy_overrides = {...}``) — a caller can still
        reach straight past that guard via the instance's own ``__dict__``
        (``REGISTRY.entries["func_removed"].__dict__["policy_overrides"] =
        {"unknown": Verdict.API_BREAK}``), which installs an unvalidated
        override directly onto the *live*, shared catalog entry every other
        caller trusts — with no ``__setattr__``/``_ImmutableDict`` guard
        anywhere in the way (Codex review, PR #882, fresh evidence). Fixed
        by making the dataclass ``slots=True``: a slotted instance has no
        ``__dict__`` attribute at all, so this path doesn't exist to reach
        through.
        """
        import pytest

        entry = ChangeKindMeta(
            "test_kind", Verdict.BREAKING, impact="x",
            policy_overrides={"plugin_abi": Verdict.COMPATIBLE},
        )
        with pytest.raises(AttributeError):
            entry.__dict__  # noqa: B018 - deliberately probing for absence
        # Confirmed on the actual production registry too, matching the
        # exact exploit shape from review.
        live = REGISTRY.entries["func_removed"]
        with pytest.raises(AttributeError):
            live.__dict__  # noqa: B018

    def test_setstate_does_not_validate_matching_the_constructor(self):
        """Restoring (unpickling) a standalone entry must not be stricter than building one.

        Direct construction, ``ChangeKindMeta("x", Verdict.BREAKING)``, is
        legal today with an empty ``impact``/an unrecognized
        ``policy_overrides`` key — catalog validation is deliberately
        deferred to ``ChangeKindRegistry.__init__``'s own loop over every
        entry it actually holds, not applied per-instance at construction
        time. An earlier revision of ``__setstate__`` called
        ``_validate_entry`` unconditionally, which broke that symmetry:
        ``pickle.loads(pickle.dumps(ChangeKindMeta("x", Verdict.BREAKING)))``
        regressed from working to raising ``ValueError``, and would
        equally have broken loading a standalone, not-yet-registry-inserted
        pickle predating impact text becoming mandatory (Codex review,
        PR #882, fresh evidence). Fixed by dropping that call —
        ``__setstate__`` normalizes ``policy_overrides`` into an
        ``_ImmutableDict`` the same way ``__post_init__`` does, and nothing
        more, matching the constructor's own deferred-validation contract.
        """
        import pickle

        import pytest

        entry = ChangeKindMeta("x", Verdict.BREAKING)
        assert entry.impact == ""
        rehydrated = pickle.loads(pickle.dumps(entry))
        assert rehydrated == entry
        assert rehydrated.impact == ""

        entry2 = ChangeKindMeta(
            "y", Verdict.BREAKING, impact="i",
            policy_overrides={"unknown": Verdict.API_BREAK},
        )
        rehydrated2 = pickle.loads(pickle.dumps(entry2))
        assert dict(rehydrated2.policy_overrides) == {"unknown": Verdict.API_BREAK}

        # ChangeKindRegistry.__init__ still catches either shape once the
        # entry is actually assembled into a real registry -- unpickling
        # alone doesn't grant it a free pass past that gate.
        with pytest.raises(ValueError, match="impact must be non-empty"):
            ChangeKindRegistry([rehydrated])
        with pytest.raises(ValueError, match="unknown policy"):
            ChangeKindRegistry([rehydrated2])

    def test_description_template_with_unknown_placeholder_raises(self):
        """A description_template referencing an out-of-vocabulary field is rejected.

        diff_helpers.make_change() formats a template via
        ``template.format(symbol=..., name=..., old=..., new=...,
        detail=...)`` — a keyword-only call, so a field outside
        TEMPLATE_VOCAB would only fail the first time a finding of that kind
        is actually formatted, not at registry construction. This is D9's
        "valid references" property extended to description_template
        (Codex review, PR #882 — the same shape of gap as the
        policy_overrides checks, found after those already landed).
        """
        import pytest

        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                description_template="Changed: {bogus}",
            ),
        ]
        with pytest.raises(ValueError, match="bogus"):
            ChangeKindRegistry(entries)

    def test_description_template_with_positional_placeholder_raises(self):
        """A bare positional `{}`/`{0}` placeholder is rejected too.

        make_change()'s .format() call is keyword-only, so a positional
        field can never be satisfied — the same "fails only when this kind's
        finding is actually formatted" gap as an unknown named field.
        """
        import pytest

        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                description_template="Changed: {}",
            ),
        ]
        with pytest.raises(ValueError, match="description_template"):
            ChangeKindRegistry(entries)

    def test_description_template_using_only_vocab_is_accepted(self):
        """A template using only TEMPLATE_VOCAB fields passes construction."""
        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                description_template="{symbol} changed from {old} to {new}",
            ),
        ]
        registry = ChangeKindRegistry(entries)
        assert len(registry) == 1

    def test_description_template_with_indexed_field_raises(self):
        """A field with subscript access is rejected, even though it probes fine.

        ``{symbol[0]}`` succeeds against the non-empty probe value
        ``"probe"`` (execution-based validation alone would miss this), and
        only fails once ``make_change()`` is called with a real, empty
        ``symbol`` — a valid ``str`` some findings do pass. Field-name
        validation rejects it deterministically at construction time
        instead, since only the five bare TEMPLATE_VOCAB names are ever
        legal (Codex review, PR #882, fresh evidence beyond the format-code
        fix).
        """
        import pytest

        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                description_template="Changed: {symbol[0]}",
            ),
        ]
        with pytest.raises(ValueError, match=r"symbol\[0\]"):
            ChangeKindRegistry(entries)

    def test_description_template_with_attribute_access_raises(self):
        """A field with attribute traversal is rejected the same way."""
        import pytest

        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                description_template="Changed: {symbol.__class__}",
            ),
        ]
        with pytest.raises(ValueError, match="__class__"):
            ChangeKindRegistry(entries)

    def test_description_template_with_nested_bad_field_raises(self):
        """A field nested inside a format spec is caught too, not just the outer one.

        ``string.Formatter().parse()`` only yields the *outer* field name of
        each replacement field — ``{name:{bogus}}``'s inner ``bogus`` is
        invisible to a single non-recursive pass, so a naive check would
        accept this template and only fail the first time make_change()
        actually formats it (Codex review, PR #882, fresh evidence beyond
        the top-level check above). The registry validates by actually
        executing ``.format()`` with representative values, so this is
        caught as a plain ``KeyError`` surfaced through the wrapping
        ``ValueError``, not by re-parsing the template's grammar by hand.
        """
        import pytest

        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                description_template="Changed: {name:{bogus}}",
            ),
        ]
        with pytest.raises(ValueError, match="bogus"):
            ChangeKindRegistry(entries)

    def test_description_template_with_invalid_conversion_raises(self):
        """An illegal !conversion specifier is rejected at construction time.

        Only ``!r``/``!s``/``!a`` (or no conversion) are legal for
        ``str.format`` — anything else raises ``ValueError`` at format time,
        which construction-time validation must catch instead (Codex
        review, PR #882).
        """
        import pytest

        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                description_template="Changed: {name!x}",
            ),
        ]
        with pytest.raises(ValueError, match="conversion specifier"):
            ChangeKindRegistry(entries)

    def test_description_template_with_invalid_format_code_raises(self):
        """An invalid format *code* (not just a bad field/conversion) is rejected.

        ``string.Formatter().parse()``-based validation never actually
        formats anything, so a syntactically well-formed but semantically
        invalid format spec like ``{name:q}`` (``q`` is not a real
        presentation type) passed the earlier construction-time check and
        only raised ``ValueError: Unknown format code 'q'`` the first time
        ``make_change()`` actually formatted a finding of that kind (Codex
        review, PR #882, fresh evidence beyond the nested-field/conversion
        fix above). The registry now actually executes ``.format()`` with
        representative values at construction time, which catches this the
        same way it catches every other formatting failure.
        """
        import pytest

        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                description_template="Changed: {name:q}",
            ),
        ]
        with pytest.raises(ValueError, match="format code"):
            ChangeKindRegistry(entries)

    def test_description_template_none_value_with_format_spec_raises(self):
        """A format spec that fails for a real None value is rejected too.

        ``old``/``new``/``detail`` (and ``name``) are all ``str | None`` in
        make_change()'s real call shape and are frequently ``None`` in
        practice — a format spec that works for a ``str`` value can still
        raise ``TypeError`` for ``None`` (``format(None, '>10')`` raises,
        while a bare ``{old}`` does not). Probing with only string values
        would miss this failure mode.
        """
        import pytest

        entries = [
            ChangeKindMeta(
                "test_kind", Verdict.BREAKING, impact="x",
                description_template="Changed: {old:>10}",
            ),
        ]
        with pytest.raises(ValueError, match="description_template"):
            ChangeKindRegistry(entries)

    def test_real_registry_satisfies_reference_and_default_validation(self):
        """The production REGISTRY satisfies three of D9's four properties.

        Reconstructing it from its own entries must not raise — i.e. every
        real ChangeKindMeta entry satisfies D9's "complete metadata"
        (non-empty impact), "valid references", and "non-contradictory
        defaults" catalog-validation properties (global uniqueness is
        checked separately by the constructor's own duplicate-key check).
        """
        rebuilt = ChangeKindRegistry(list(REGISTRY.entries.values()))
        assert len(rebuilt) == len(REGISTRY)

    def test_empty_impact_raises(self):
        """A ChangeKindMeta entry with no impact text is rejected.

        ADR-061 D9's "complete metadata" catalog-validation property — the
        fourth of the four, closed by writing real, individually-accurate
        impact text for the 48 entries that previously had none (Phase 5).
        """
        import pytest

        entries = [ChangeKindMeta("test_kind", Verdict.BREAKING)]
        with pytest.raises(ValueError, match="impact must be non-empty"):
            ChangeKindRegistry(entries)

    def test_whitespace_only_impact_raises(self):
        """A ChangeKindMeta entry with only whitespace as impact is rejected.

        A bare truthiness check (``if not e.impact``) accepts
        ``impact="   \\n"`` — non-empty as a string, but carrying no
        human-readable content, so a report would still surface nothing
        useful (Codex review, PR #882, fresh evidence).
        """
        import pytest

        entries = [ChangeKindMeta("test_kind", Verdict.BREAKING, impact="   \n\t  ")]
        with pytest.raises(ValueError, match="impact must be non-empty"):
            ChangeKindRegistry(entries)

    def test_real_registry_has_no_missing_impact_text(self):
        """No production entry has an empty impact — D9's "complete metadata".

        Direct, explicit pin of the specific gap this property closes (all
        397 entries, including the 48 that previously had none), separate
        from the general reconstruction test above.
        """
        missing = sorted(k for k, e in REGISTRY.entries.items() if not e.impact)
        assert missing == []

    def test_adding_kind_is_one_entry(self):
        """Adding a new kind to the registry is a single ChangeKindMeta entry."""
        entry = ChangeKindMeta(
            kind="hypothetical_new_kind",
            default_verdict=Verdict.BREAKING,
            impact="This is what goes wrong.",
            policy_overrides={"plugin_abi": Verdict.COMPATIBLE},
        )
        # The entry contains ALL metadata in one place
        assert entry.default_verdict == Verdict.BREAKING
        assert entry.impact == "This is what goes wrong."
        assert entry.policy_overrides == {"plugin_abi": Verdict.COMPATIBLE}
        assert entry.is_addition is False


# ─── Part B: DetectorRegistry tests ──────────────────────────────────────────


def _get_populated_registry():
    """Import checker (which triggers all detector imports) and return registry."""
    import abicheck.checker  # noqa: F401 — triggers detector module imports
    from abicheck.detector_registry import registry

    return registry


class TestDetectorRegistry:
    """Self-registering detector registry."""

    def test_all_detectors_registered(self):
        """All 65 detectors are registered via decorators."""
        registry = _get_populated_registry()
        assert len(registry) == 65

    def test_detector_names_unique(self):
        """No duplicate detector names."""
        registry = _get_populated_registry()
        names = registry.detector_names
        assert len(names) == len(set(names))

    def test_expected_detectors_present(self):
        """Key detectors are in the registry."""
        registry = _get_populated_registry()
        names = set(registry.detector_names)
        expected = {
            "functions",
            "variables",
            "types",
            "enums",
            "elf",
            "pe",
            "macho",
            "dwarf",
            "advanced_dwarf",
            "enum_renames",
            "field_qualifiers",
            "field_renames",
            "param_defaults",
            "param_renames",
            "pointer_levels",
            "access_levels",
            "anon_fields",
            "var_values",
            "type_kind_changes",
            "reserved_fields",
            "const_overloads",
            "param_restrict",
            "param_va_list",
            "constants",
            "var_access",
            "elf_deleted_fallback",
            "template_inner_types",
            "symbol_renames",
            "method_qualifiers",
            "unions",
            "typedefs",
            "tls_checks",
            "protected_visibility",
            "symbol_version_alias",
            "glibcxx_dual_abi",
            "inline_namespace",
            "vtable_identity",
            "abi_surface",
            "sycl",
            "fingerprint_renames",
        }
        assert expected <= names

    def test_run_all_returns_changes_and_results(self):
        """registry.run_all() returns (changes, detector_results)."""
        registry = _get_populated_registry()
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        changes, results = registry.run_all(old, new)
        assert isinstance(changes, list)
        assert isinstance(results, list)
        # Results should have entries for all detectors (enabled or disabled)
        assert len(results) == 65

    def test_support_check_disables_detector(self):
        """Detectors with failing support checks are disabled."""
        registry = _get_populated_registry()
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        _, results = registry.run_all(old, new)
        result_map = {r.name: r for r in results}
        # PE detector should be disabled (no PE metadata)
        assert result_map["pe"].enabled is False
        assert result_map["pe"].coverage_gap == "missing PE metadata"
        # Macho detector should be disabled
        assert result_map["macho"].enabled is False
        # Advanced DWARF should be disabled
        assert result_map["advanced_dwarf"].enabled is False

    def test_duplicate_name_raises(self):
        """Registering a detector with duplicate name raises ValueError."""
        import pytest

        from abicheck.detector_registry import DetectorRegistry

        reg = DetectorRegistry()

        @reg.detector("test_det")
        def _det1(old, new):
            return []

        with pytest.raises(ValueError, match="Duplicate"):

            @reg.detector("test_det")
            def _det2(old, new):
                return []


# ─── Part C: PostProcessingPipeline tests ────────────────────────────────────


class TestPostProcessingPipeline:
    """Pipeline-based post-processing."""

    def test_default_pipeline_has_expected_steps(self):
        """DEFAULT_PIPELINE has all expected steps in order."""
        from abicheck.post_processing import DEFAULT_PIPELINE

        expected_names = [
            "filter_reserved_field_renames",
            "filter_opaque_size_changes",
            "downgrade_opaque_struct_changes",
            "deduplicate_ast_dwarf",
            "deduplicate_cross_detector",
            "downgrade_opaque_type_changes",
            "enrich_source_locations",
            "filter_non_public_surface",
            "demote_off_python_surface",
            "annotate_layout_unverifiable_covered_by_vtable_changed",
            "mark_reachability",
            "apply_suppression",
            "suppress_renamed_pairs",
            "clear_orphaned_vtable_gap_correlation",
            "filter_redundant",
            "enrich_affected_symbols",
            "attribute_stdlib_embedding",
            "detect_internal_leaks",
            "demote_unreachable_internal_churn",
            "detect_cpp_patterns",
            "detect_namespace_patterns",
            "detect_template_patterns",
            "detect_versioned_symbol_scheme",
            "escalate_frozen_namespace_violations",
        ]
        assert DEFAULT_PIPELINE.step_names == expected_names

    def test_pipeline_runs_on_empty_changes(self):
        """Pipeline produces valid context with empty change list."""
        from abicheck.model import AbiSnapshot
        from abicheck.post_processing import DEFAULT_PIPELINE

        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        ctx = DEFAULT_PIPELINE.run([], old, new)
        assert ctx.kept == []
        assert ctx.redundant == []
        assert ctx.suppressed == []

    def test_pipeline_with_changes(self):
        """Pipeline processes changes through all steps."""
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change
        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.post_processing import DEFAULT_PIPELINE

        old = AbiSnapshot(
            library="test",
            version="1.0",
            functions=[
                Function(
                    name="foo",
                    mangled="foo",
                    return_type="int",
                    params=[],
                    visibility=Visibility.PUBLIC,
                )
            ],
        )
        new = AbiSnapshot(library="test", version="2.0", functions=[])
        changes = [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="foo",
                description="function removed",
            ),
        ]
        ctx = DEFAULT_PIPELINE.run(changes, old, new)
        assert len(ctx.kept) == 1
        assert ctx.kept[0].kind == ChangeKind.FUNC_REMOVED

    def test_kept_alias_violation_raises_runtime_error(self):
        """A step that rebinds ``changes`` to a new list after FilterRedundant,
        without resyncing ``ctx.kept``, must fail loudly.

        Regression guard for the ``ctx.kept`` aliasing contract: FilterRedundant
        sets ``ctx.kept = kept`` and later steps (DetectCppPatterns,
        DemoteUnreachableInternalChurn) rely on that being the *same list
        object* so their in-place suppression/demotion is visible through
        ``ctx.kept``. A step that instead rebinds ``changes`` to a fresh list
        (e.g. a list comprehension) without updating ``ctx.kept`` would
        silently discard whatever was tracked there — this must raise instead.
        """
        import pytest

        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change
        from abicheck.model import AbiSnapshot
        from abicheck.post_processing import FilterRedundant, PostProcessingPipeline

        class _ReboundStep:
            name = "rebinds_changes_bug"

            def run(self, changes, ctx):
                # Simulates the exact bug class the contract guards against:
                # a fresh list instead of an in-place mutation or ctx.kept resync.
                return [c for c in changes]

        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        changes = [
            Change(kind=ChangeKind.FUNC_ADDED, symbol="foo", description="added"),
        ]
        pipeline = PostProcessingPipeline([FilterRedundant(), _ReboundStep()])
        with pytest.raises(RuntimeError, match="aliasing contract"):
            pipeline.run(changes, old, new)

    def test_kept_alias_preserved_through_default_pipeline(self):
        """End-to-end regression: a finding demoted by
        DemoteUnreachableInternalChurn (which runs after FilterRedundant) must
        actually disappear from ``ctx.kept`` — not just from that step's own
        return value — proving the ``ctx.kept`` alias survives every
        intervening step (DetectInternalLeaks, EnrichAffectedSymbols, etc.)."""
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change
        from abicheck.model import AbiSnapshot
        from abicheck.post_processing import DEFAULT_PIPELINE

        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        changes = [
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="ns::detail::Private",
                description="size changed",
            ),
        ]
        ctx = DEFAULT_PIPELINE.run(changes, old, new)
        assert any(c.symbol == "ns::detail::Private" for c in ctx.out_of_surface)
        assert all(c.symbol != "ns::detail::Private" for c in ctx.kept)

    def test_custom_pipeline_with_subset_of_steps(self):
        """Custom pipeline with only some steps."""
        from abicheck.model import AbiSnapshot
        from abicheck.post_processing import (
            DeduplicateAstDwarf,
            FilterReservedFieldRenames,
            PostProcessingPipeline,
        )

        pipeline = PostProcessingPipeline(
            [
                FilterReservedFieldRenames(),
                DeduplicateAstDwarf(),
            ]
        )
        assert pipeline.step_names == [
            "filter_reserved_field_renames",
            "deduplicate_ast_dwarf",
        ]
        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        ctx = pipeline.run([], old, new)
        assert ctx.kept == []


# ─── Integration: compare() uses registry + pipeline ─────────────────────────


class TestCompareUsesNewArchitecture:
    """compare() uses self-registering detectors and pipeline."""

    def test_compare_returns_valid_result(self):
        """compare() still returns correct DiffResult."""
        from abicheck.checker import compare
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="test", version="1.0")
        new = AbiSnapshot(library="test", version="2.0")
        result = compare(old, new)
        assert result.verdict.value == "NO_CHANGE"
        assert result.changes == []
        assert len(result.detector_results) == 65

    def test_compare_detects_func_removal(self):
        """compare() detects function removal via registry."""
        from abicheck.checker import compare
        from abicheck.checker_policy import ChangeKind
        from abicheck.model import AbiSnapshot, Function, Visibility

        old = AbiSnapshot(
            library="test",
            version="1.0",
            functions=[
                Function(
                    name="foo",
                    mangled="foo",
                    return_type="int",
                    params=[],
                    visibility=Visibility.PUBLIC,
                )
            ],
        )
        new = AbiSnapshot(library="test", version="2.0")
        result = compare(old, new)
        assert result.verdict.value == "BREAKING"
        func_removed = [c for c in result.changes if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(func_removed) == 1

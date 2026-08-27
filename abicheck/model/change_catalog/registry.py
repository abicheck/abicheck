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

"""Core types for the single-declaration ChangeKind registry (ADR-061 D9).

Leaf module holding the verdict enum, the per-kind metadata dataclass, and
the registry container that derives the classification sets, plus the
catalog-validation logic D9 assigns to the assembled registry. This is the
target owner ADR-061 names for this logic — it used to live in the legacy
flat ``abicheck/change_registry_types.py``, which now re-exports every name
here unchanged (``from abicheck.change_registry_types import Verdict`` and
the transitive ``from abicheck.change_registry import Verdict`` are both
still valid). The 397-entry data table itself is fully repartitioned into
five sibling taxonomy modules in this same package — ``symbols.py``,
``types.py``, ``platform.py``, ``build.py``, ``source.py`` — which this
module's own ``ChangeKindMeta``/``Verdict`` back, and which
``abicheck/change_registry.py`` imports and concatenates into the single
production ``REGISTRY``.

This module has zero internal imports (a true leaf, per ADR-061's ``model``
layer contract of ``may_import: []``), which is what lets both
``checker_policy.py`` (imports ``REGISTRY`` from ``change_registry.py``,
which imports this module directly) and ``diff_helpers.py`` (same shape)
import ``VALID_BASE_POLICIES``/``TEMPLATE_VOCAB`` from here without an
import cycle.
"""

from __future__ import annotations

import string
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, fields
from enum import Enum
from types import MappingProxyType
from typing import Any


class Verdict(str, Enum):
    NO_CHANGE = "NO_CHANGE"
    COMPATIBLE = "COMPATIBLE"
    COMPATIBLE_WITH_RISK = "COMPATIBLE_WITH_RISK"
    API_BREAK = "API_BREAK"
    BREAKING = "BREAKING"


#: Canonical set of valid built-in policy names. Import from here — do not
#: redefine. ``ChangeKindRegistry`` below validates every
#: ``ChangeKindMeta.policy_overrides`` key against this set at construction
#: time (ADR-061 Phase 5, D9's "valid references" catalog validation).
#: ``checker_policy.py``/``change_registry_types.py`` re-export this name
#: unchanged, so every existing
#: ``from abicheck.checker_policy import VALID_BASE_POLICIES`` caller is
#: unaffected.
VALID_BASE_POLICIES: frozenset[str] = frozenset(
    {"strict_abi", "sdk_vendor", "plugin_abi"}
)

#: Policies whose ``checker_policy.policy_kind_sets()`` implementation
#: classifies every kind carrying a ``policy_overrides`` entry for that
#: policy as ``Verdict.COMPATIBLE`` unconditionally — the declared override
#: *value* is never consulted, only its key's presence
#: (``_policy_override_kinds()`` gathers ``policy_overrides_for(policy)``'s
#: keys, not its values). ``ChangeKindRegistry`` below rejects a declared
#: override for one of these policies that isn't ``Verdict.COMPATIBLE``,
#: since anything else would silently disagree with actual runtime behavior.
#: Keep this in sync with ``policy_kind_sets()`` — if a future policy's
#: implementation genuinely consumes the declared verdict, remove it here.
_VERDICT_BLIND_POLICIES: frozenset[str] = frozenset({"sdk_vendor", "plugin_abi"})

#: Fixed placeholder vocabulary a ``ChangeKindMeta.description_template`` may
#: use. Import from here — do not redefine. ``diff_helpers.make_change``
#: formats a kind's template from exactly these structured fields
#: (``{symbol} {name} {old} {new} {detail}``) — see that module for the
#: per-field meaning. ``diff_helpers.py``/``change_registry_types.py``
#: re-export this name unchanged, so every existing
#: ``from abicheck.diff_helpers import TEMPLATE_VOCAB`` caller is unaffected.
TEMPLATE_VOCAB = frozenset({"symbol", "name", "old", "new", "detail"})


class _ImmutableDict(Mapping[str, Verdict]):
    """An immutable mapping that is deliberately *not* a ``dict`` subclass.

    ``ChangeKindMeta.policy_overrides`` needs to be immutable after
    construction (see ``__post_init__`` below) *and* round-trip cleanly
    through ``dataclasses.asdict()``/``copy.deepcopy()``/``pickle`` the same
    way an ordinary ``dict`` field already does. Three earlier designs each
    closed one gap and left another (Codex review, PR #882, fresh evidence
    each time):

    - ``types.MappingProxyType`` gives immutability for free but cannot be
      pickled at all (``asdict()``'s recursive dict handling only
      special-cases a literal ``dict``; anything else falls back to a plain
      ``copy.deepcopy()``, which mappingproxy has no support for).
    - A plain ``dict`` subclass overriding the mutating methods
      (``__setitem__``/``update``/``__ior__``/re-invoked ``__init__``, etc.)
      fixes that, and round-trips correctly once given a custom
      ``__reduce__`` (the *default* pickle/deepcopy protocol for a dict
      subclass reconstructs item-by-item, which hits the very mutators being
      overridden). But being a genuine ``dict`` instance means its storage
      is still reachable through ``dict``'s own *unbound* methods called
      directly: ``dict.__setitem__(entry.policy_overrides, "unknown",
      Verdict.API_BREAK)`` mutates the underlying hash table in C, bypassing
      every overridden Python-level method entirely — there is no override
      that can intercept a call to the base type's own descriptor.

    The only way to close that last gap is to not be a ``dict`` at all:
    ``dict.__setitem__(obj, ...)`` requires its first argument to *be* a
    ``dict`` instance (or subclass), and raises ``TypeError`` immediately
    for anything else. This class implements the read-only
    ``collections.abc.Mapping`` protocol (``__getitem__``/``__iter__``/
    ``__len__``, which is all ``Mapping`` needs to derive ``__contains__``/
    ``keys()``/``values()``/``items()``/``get()``) over ``self._data`` — a
    private ``types.MappingProxyType`` view (not a plain ``dict``: a plain
    dict there would itself be reachable and mutable one attribute access
    away, via ``entry.policy_overrides._data["unknown"] = ...`` — Codex
    review, PR #882, fresh evidence; the earlier "wrap a mutable dict, only
    guard access through this class's own methods" framing missed exactly
    this). ``Mapping`` supplies no ``__setitem__``/``update``/``pop``/etc.
    at all — those are ``MutableMapping``-only mixin methods, and this
    class implements only the read-only ``Mapping`` protocol. Separately,
    neither ABC defines ``__or__``/``__ior__`` at all (Codex review, PR
    #882, fresh evidence corrected an earlier revision of this docstring
    that mis-attributed them to ``MutableMapping``): PEP 584's `|`/`|=`
    are a ``dict``-specific addition to the concrete type, not a mixin any
    ABC provides. Either way, ``entry.policy_overrides["x"] = y`` and
    ``entry.policy_overrides |= {...}`` both raise ``TypeError`` from
    Python's own attribute/operator resolution — no per-method overriding
    needed to block them. Two methods
    are still overridden below to close the remaining reflection-level
    gaps: ``__init__`` guards against ``entry.policy_overrides.__init__
    ({...})`` re-invoking it directly on an already-constructed instance
    (the same shape of bypass a plain dict subclass has, just for this
    class's own constructor instead of ``dict.__init__``), and
    ``__setattr__`` guards against reassigning ``_data``/``_initialized``
    directly (``entry.policy_overrides._data = {...}``), which would
    otherwise swap in an unvalidated mapping wholesale without going
    through ``__init__`` at all — the two guards share the same
    ``_initialized`` flag, so together they reject every attribute write on
    a real instance after its one legitimate ``__init__`` call.

    ``isinstance(x, dict)`` does not hold for this class, unlike the earlier
    dict-subclass design — checked against every consumer of
    ``ChangeKindMeta.policy_overrides``/``ChangeKindRegistry.
    policy_overrides_for()`` in this codebase: none relies on ``dict``-ness
    specifically, only on the ``Mapping`` protocol (``.items()``,
    ``[key]``, ``in``), which this class provides. ``dataclasses.asdict()``
    is the one place ``dict``-ness *is* observable indirectly: its generic
    branch reaches every non-dict/list/tuple/dataclass field via
    ``copy.deepcopy()``, so ``__deepcopy__`` below deliberately returns a
    plain, mutable ``dict`` — the disconnected copy ``asdict()``/
    ``copy.deepcopy()`` produce is ordinary and JSON-serializable, matching
    exactly what an ordinary ``dict`` field would give you, while the
    *original* entry's own ``policy_overrides`` stays immutable regardless.
    Pickling is a different mechanism (``__reduce__``) and keeps
    reconstructing a genuine, immutable ``_ImmutableDict``.
    """

    __slots__ = ("_data", "_initialized")

    def __init__(
        self,
        source: Mapping[str, Verdict] | Iterable[tuple[str, Verdict]] = (),
    ) -> None:
        # A second call on an already-constructed instance
        # (``entry.policy_overrides.__init__({"unknown": ...})``) would
        # otherwise silently replace ``_data`` with unvalidated content —
        # ``__init__`` is legitimately invoked exactly once per real object,
        # by ``ChangeKindMeta.__post_init__`` and by ``__reduce__``'s
        # reconstruction below, always on a brand-new instance. This also
        # doubles as the guard ``__setattr__`` below relies on.
        if getattr(self, "_initialized", False):
            raise TypeError("policy_overrides is immutable after construction")
        # ``_data`` is itself a ``types.MappingProxyType`` view over a
        # private dict with no other reference anywhere, not a plain dict —
        # a plain dict here would still be reachable and mutable through
        # ``entry.policy_overrides._data["unknown"] = ...`` (Codex review,
        # PR #882, fresh evidence): "no public mutator" only protects the
        # ``Mapping`` interface, not an attribute one attribute-access away.
        # This has none of MappingProxyType's earlier pickling problems —
        # those applied to the *field's* own type (asdict()/deepcopy()/
        # pickle handling a bare mappingproxy value), not to something used
        # purely as this class's own private storage, which its own
        # __reduce__/__deepcopy__ above already convert to a plain dict
        # before handing off to pickle/deepcopy's machinery.
        self._data = MappingProxyType(dict(source))
        self._initialized = True

    def __setattr__(self, name: str, value: Any) -> None:
        # Blocks the sibling bypass to the one above: reassigning ``_data``
        # directly (``entry.policy_overrides._data = {...}``) would swap in
        # an unvalidated mapping wholesale, without going through
        # ``__init__`` at all (Codex review, PR #882, fresh evidence).
        # ``_initialized`` is only ever ``True`` after ``__init__`` has
        # already set both slots, so this rejects every later attribute
        # write on a real instance while still allowing ``__init__`` itself
        # to set them the first time.
        if getattr(self, "_initialized", False):
            raise TypeError("policy_overrides is immutable after construction")
        object.__setattr__(self, name, value)

    def __getitem__(self, key: str) -> Verdict:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._data!r})"

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Verdict]:
        # Deliberately returns a plain, ordinary (mutable) dict rather than
        # another _ImmutableDict — matching exactly what an *ordinary* dict
        # field would produce under copy.deepcopy() (a disconnected copy,
        # unremarkable in every way, keys/values already immutable so a
        # shallow dict() copy is a real deep copy here). This is also what
        # makes dataclasses.asdict() work: its generic-value branch calls
        # copy.deepcopy() on any field that isn't itself a dict/list/tuple/
        # dataclass, so without this override asdict()'s output kept the
        # live _ImmutableDict — a non-dict Mapping json.dumps() cannot
        # serialize, unlike the plain dict an ordinary field would have
        # produced (Codex review, PR #882, fresh evidence). The *original*
        # entry's own policy_overrides attribute is completely unaffected —
        # this only governs what a disconnected copy of it looks like.
        # pickle round-trips take a different path (__reduce__ below) and
        # keep reconstructing a genuine, immutable _ImmutableDict, since
        # pickle's job is faithfully reconstructing the same object/type,
        # not producing JSON-primitive-friendly output.
        return dict(self._data)

    def __reduce__(self) -> tuple[Any, ...]:
        return (self.__class__, (dict(self._data),))


@dataclass(frozen=True, slots=True)
class ChangeKindMeta:
    """All metadata for a single ChangeKind, declared in one place.

    ``slots=True`` (Codex review, PR #882, fresh evidence): without it,
    ``frozen=True`` only blocks reassigning an attribute
    (``entry.policy_overrides = {...}``) — it does nothing to stop a caller
    reaching straight past that guard via the instance's own ``__dict__``
    (``REGISTRY.entries["func_removed"].__dict__["policy_overrides"] =
    {"unknown": Verdict.API_BREAK}``), which installs an unvalidated
    override directly onto the *live*, shared catalog entry every other
    caller trusts, with no ``__setattr__``/``_ImmutableDict`` guard anywhere
    in the way. A slotted dataclass has no ``__dict__`` at all, so that
    attribute path doesn't exist to reach through — the same fix already
    applied to ``_ImmutableDict`` itself (a ``MappingProxyType``-backed
    ``_data`` plus its own ``__setattr__`` guard) applied one layer up, to
    the object that holds it.

    **Residual, deliberately not chased further** (Codex review, PR #882,
    fresh evidence): ``object.__setattr__(entry, "policy_overrides", ...)``
    still mutates a live entry even with ``slots=True``, since it calls the
    base implementation directly and bypasses the *class's own* generated
    ``__setattr__`` override entirely — the same escape hatch
    ``__post_init__`` itself legitimately relies on to set fields on an
    otherwise-frozen instance, and the Python docs name it as the standard
    workaround for `frozen=True` generally. This is not specific to this
    class's design: no combination of ``frozen``/``slots``/a custom
    ``__setattr__`` closes it for *any* ordinary Python class, since
    ``object.__setattr__`` always resolves to the same underlying
    descriptor-set operation a data descriptor's own ``__set__`` would
    reach anyway — only a type with no settable descriptor for the name at
    all (a ``NamedTuple``, whose fields are plain ``property`` getters
    over immutable tuple storage) resists it, confirmed empirically. That
    would mean replacing this dataclass with a materially different
    representation — no ``__post_init__``, a custom ``__new__`` in its
    place, ``dataclasses.asdict()``/``dataclasses.fields()`` call sites
    elsewhere (tests only, checked) moved to the namedtuple equivalents —
    a redesign disproportionate to the actual threat model: reaching this
    path already requires a caller *inside the same process* deliberately
    choosing the documented low-level bypass over the class's own public
    surface, at which point simpler routes to the identical outcome exist
    (replacing ``REGISTRY`` itself, monkeypatching
    ``checker_policy.policy_kind_sets``) — the same limitation every other
    ``frozen=True`` dataclass in this codebase already has, not a new gap
    introduced here. What ``slots=True`` above actually defends against —
    and the only threat model this class's immutability was ever meant to
    cover — is *accidental* mutation through ordinary, non-adversarial
    code (a caller not realizing ``entry.policy_overrides["x"] = y`` or
    ``entry.__dict__[...] = ...`` corrupts shared state), which it still
    does.
    """

    kind: str  # ChangeKind enum value (e.g. "func_removed")
    default_verdict: Verdict
    impact: str = ""
    is_addition: bool = False
    policy_overrides: Mapping[str, Verdict] = field(default_factory=dict)
    # Optional ``str.format``-style template for a finding's per-change
    # ``description`` (C6). Detectors build their Change via
    # ``diff_helpers.make_change`` and pass structured fields rather than
    # hand-rolling an f-string, so the wording for a kind lives in one place.
    # Placeholders are drawn from the fixed vocabulary
    # ``{symbol} {name} {old} {new} {detail}`` (``make_change`` validates this).
    # ``None`` means the kind keeps a *bespoke* per-call-site description — used
    # when the text embeds computed offsets, demangled signatures, vtable slot
    # indices, counts, etc. that no fixed template can express.
    description_template: str | None = None

    def __post_init__(self) -> None:
        # ``frozen=True`` only stops reassigning the *attribute*
        # (``entry.policy_overrides = {...}``) — it does not stop mutating
        # the dict object itself (``entry.policy_overrides["x"] = y``), and
        # a caller can hand in a dict it keeps its own live reference to.
        # Either path can silently invalidate the "valid references"/
        # "non-contradictory defaults" checks ``_validate_entry`` already ran
        # on construction, without re-running them, and can make
        # ``ChangeKindRegistry.policy_overrides_for()`` disagree with sets
        # already derived at import time (Codex review, PR #882). Defensively
        # copy into an immutable mapping so neither is possible, while still
        # round-tripping through asdict()/deepcopy()/pickle the way an
        # ordinary dict field does (see _ImmutableDict).
        object.__setattr__(
            self, "policy_overrides", _ImmutableDict(self.policy_overrides)
        )

    def __deepcopy__(self, memo: dict[int, Any]) -> ChangeKindMeta:
        # copy.deepcopy(entry) (the whole ChangeKindMeta, not one field)
        # has no custom __deepcopy__/__reduce__ to intercept by default, so
        # Python's generic object-copying mechanism would build a new
        # instance and set each field's own deep copy directly via
        # object.__setattr__ — bypassing __post_init__ entirely. For
        # policy_overrides that field-level deep copy is
        # _ImmutableDict.__deepcopy__, which deliberately returns a plain,
        # mutable dict (see that method's own docstring — required for
        # dataclasses.asdict()'s JSON-serialization use case). Without this
        # override, the resulting ChangeKindMeta.policy_overrides would
        # therefore be a bare mutable dict, silently defeating the
        # immutability guarantee for any copy later placed back into a
        # registry or otherwise treated as a validated entry (Codex
        # review, PR #882, fresh evidence). Reconstructing via the
        # constructor instead re-runs __post_init__, which wraps a fresh
        # copy of policy_overrides into a genuinely immutable
        # _ImmutableDict again.
        #
        # dataclasses.asdict() never reaches this method at all — for a
        # dataclass instance it walks fields directly (getattr + recurse),
        # never calling copy.deepcopy() on the instance itself — so the
        # two paths stay genuinely independent: asdict()'s field-level
        # copy stays a plain dict, while copy.deepcopy() of the whole
        # entry stays immutable.
        new = ChangeKindMeta(
            kind=self.kind,
            default_verdict=self.default_verdict,
            impact=self.impact,
            is_addition=self.is_addition,
            policy_overrides=dict(self.policy_overrides),
            description_template=self.description_template,
        )
        memo[id(self)] = new
        return new

    def __setstate__(self, state: list[Any] | dict[str, Any]) -> None:
        # Pickle's default protocol restores a slotted dataclass by calling
        # this method (when defined) with the value ``object.__getstate__()``
        # produces for a __dict__-less instance: a plain LIST of field
        # values in declaration order, not a dict keyed by field name (a
        # slots dataclass has no per-field key/value mapping to hand back —
        # confirmed empirically). But a pickle produced by the immediately
        # preceding, pre-``slots=True`` revision of this class restores with
        # the OLDER shape instead — that class had a real ``__dict__``, so
        # its own default ``__getstate__`` returned it directly, a dict
        # keyed by field name. Loading such a pickle here is a real,
        # supported case (not a hypothetical): it's what "the immediately
        # preceding released version's pickle" looks like, one commit back
        # (Codex review, PR #882, fresh evidence — confirmed against a real
        # pickle produced by that exact prior revision). Treating a dict
        # state as though it were the new positional-list shape would zip
        # field names against the dict's own KEYS instead of its values
        # (``dict.__iter__`` yields keys), eventually feeding the literal
        # string ``"policy_overrides"`` to ``_ImmutableDict`` and raising
        # ``ValueError`` — confirmed exactly. Handle both shapes explicitly
        # instead of assuming the new one unconditionally.
        #
        # A pickle written before policy_overrides became an
        # ``_ImmutableDict`` (or one produced by any code that had a plain
        # dict at this field) would, in either state shape, otherwise
        # silently install a plain, mutable dict on the restored instance,
        # bypassing every validation/immutability guarantee __post_init__
        # establishes — confirmed with a real pickle from before the
        # _ImmutableDict change: type(loaded.policy_overrides) is dict, and
        # loaded.policy_overrides["x"] = y succeeds (Codex review, PR #882,
        # fresh evidence). Normalize on load instead, so every restored
        # instance's policy_overrides is provably an _ImmutableDict
        # regardless of which version produced the pickle.
        #
        # __setstate__ is an ordinary method, not exclusive to pickle's own
        # restore path — nothing stops a caller from invoking it directly
        # on an already-initialized, LIVE catalog entry (e.g. one obtained
        # via ``REGISTRY.entries``), which would (pre-``slots=True``) have
        # silently overwritten its __dict__ in place: that never went
        # through frozen=True's __setattr__ override, so a crafted state
        # could install an unvalidated override directly onto a shared
        # catalog entry other code already trusts, or blank the required
        # ``impact`` text (Codex review, PR #882, fresh evidence). Refuse
        # outright unless ``self`` is still a genuinely blank instance — the
        # shape pickle's own restore protocol actually produces
        # (``object.__new__(cls)`` with no ``__init__``/``__post_init__``
        # call, so every slot is still unset) — checked via a slot that's
        # always populated rather than ``self.__dict__`` (which no longer
        # exists to check once slotted).
        #
        # Deliberately does NOT also call _validate_entry() here (an
        # earlier revision of this fix did, and was reverted — Codex
        # review, PR #882, fresh evidence): direct construction,
        # ``ChangeKindMeta("x", Verdict.BREAKING)``, is legal today with
        # an empty ``impact``/an unrecognized ``policy_overrides`` key —
        # catalog validation is deliberately deferred to
        # ``ChangeKindRegistry.__init__``'s own loop over every entry it
        # actually holds, not applied per-instance at construction time.
        # Validating unconditionally inside __setstate__ broke that
        # symmetry: ``pickle.loads(pickle.dumps(ChangeKindMeta("x",
        # Verdict.BREAKING)))`` regressed from working (matching
        # __init__'s own behavior) to raising ValueError, and would
        # equally have broken loading a standalone, not-yet-registry-
        # inserted pickle predating impact text becoming mandatory. The
        # blank-instance guard above is what actually closes the live-
        # mutation attack this method exists to prevent; it doesn't
        # depend on also validating restored content.
        if hasattr(self, "kind"):
            raise TypeError(
                "ChangeKindMeta.__setstate__ refuses to overwrite an "
                "already-initialized instance"
            )
        values: dict[str, Any]
        if isinstance(state, dict):
            # Legacy shape: a pre-slots pickle's own __dict__, already keyed
            # by field name.
            values = dict(state)
        else:
            field_names = [f.name for f in fields(self)]
            values = dict(zip(field_names, state, strict=True))
        overrides = values.get("policy_overrides")
        if not isinstance(overrides, _ImmutableDict):
            values["policy_overrides"] = _ImmutableDict(overrides or {})
        for name, value in values.items():
            object.__setattr__(self, name, value)


#: Representative ``str.format(**...)`` kwarg sets used to *actually execute*
#: a ``description_template`` at registry-construction time (see
#: ``_check_template_formats`` below), rather than re-implementing Python's
#: own replacement-field grammar by hand. Two sets, not one: real callers
#: (``diff_helpers.make_change()``) always pass a real ``str`` for
#: ``symbol``, but ``name``/``old``/``new``/``detail`` are all
#: ``str | None`` and frequently ``None`` in practice — and a format spec
#: that works for a ``str`` value can still raise ``TypeError`` for ``None``
#: (``format(None, ">10")`` raises; ``format(None, "")`` — i.e. a bare
#: ``{old}`` — does not), so probing only with strings would miss that
#: failure mode.
_TEMPLATE_PROBE_VALUE_SETS: tuple[dict[str, str | None], ...] = (
    {
        "symbol": "probe",
        "name": "probe",
        "old": "probe",
        "new": "probe",
        "detail": "probe",
    },
    {"symbol": "probe", "name": None, "old": None, "new": None, "detail": None},
)


def _template_field_names(template: str) -> set[str]:
    """Return every top-level replacement-field name referenced by template.

    Recurses into a format spec that itself contains a nested replacement
    field (``{name:{bogus}}``), so a bad reference hidden there is still
    found. ``string.Formatter().parse()`` reports a field's *full* access
    expression as its field name — ``{symbol[0]}`` reports the field name
    ``"symbol[0]"``, not ``"symbol"``; ``{symbol.__class__}`` reports
    ``"symbol.__class__"`` — so an exact-membership check against
    ``TEMPLATE_VOCAB`` (five bare names, no indexing or attribute access)
    already rejects both without needing to special-case them.
    """
    names: set[str] = set()
    for _, field_name, format_spec, _conversion in string.Formatter().parse(template):
        if field_name is not None:
            names.add(field_name)
        if format_spec and "{" in format_spec:
            names |= _template_field_names(format_spec)
    return names


def _check_template_fields(template: str) -> None:
    """Raise ``ValueError`` if ``template`` references a field outside TEMPLATE_VOCAB.

    Catches field *traversal* (``{symbol[0]}``, ``{symbol.__class__}``) that
    ``_check_template_formats`` below cannot reliably catch by probing:
    indexing a string only raises for an out-of-range index, so
    ``{symbol[0]}`` succeeds against the non-empty probe value ``"probe"``
    and only fails once ``make_change()`` is called with a real, empty
    ``symbol`` — which is a valid ``str`` some findings do pass (Codex
    review, PR #882, fresh evidence beyond the format-code fix). This check
    is independent of runtime values: it is unconditionally illegal for a
    template to reference anything but the five declared plain names, so it
    can reject deterministically at construction time rather than depending
    on which probe values happen to trigger the failure.
    """
    bad = _template_field_names(template) - TEMPLATE_VOCAB
    if bad:
        raise ValueError(
            f"description_template {template!r} references {sorted(bad)}, "
            f"outside TEMPLATE_VOCAB {sorted(TEMPLATE_VOCAB)}"
        )


def _check_template_formats(template: str) -> None:
    """Raise ``ValueError`` if ``template`` cannot be formatted by ``make_change()``.

    Actually executes ``template.format(**probe)`` for each of
    ``_TEMPLATE_PROBE_VALUE_SETS`` — the exact operation
    ``diff_helpers.make_change()`` performs at finding-emission time — rather
    than hand-parsing the template's replacement-field grammar. An earlier
    version of this check used ``string.Formatter().parse()`` to inspect only
    each replacement field's outer field name, which missed a field nested
    inside a format spec (``{name:{bogus}}``), an illegal ``!conversion``
    (``{name!x}`` — only ``r``/``s``/``a``/none are legal), and an outright
    invalid format *code* (``{name:q}`` — ``q`` is not a real presentation
    type, raising ``ValueError: Unknown format code 'q'`` only at format
    time). Executing the real call catches all of these — and anything else
    ``str.format`` can raise — by construction, since it does not depend on
    this function correctly re-deriving Python's own formatting grammar
    (Codex review, PR #882, two rounds: nested fields/conversions, then
    format codes).
    """
    for probe in _TEMPLATE_PROBE_VALUE_SETS:
        try:
            template.format(**probe)
        except Exception as exc:  # noqa: BLE001 - re-raised with kind context below
            raise ValueError(
                f"description_template {template!r} fails to format with "
                f"representative values {probe!r}: {exc}"
            ) from exc


def _validate_entry(e: ChangeKindMeta) -> None:
    """Enforce three of D9's four catalog-validation properties.

    ADR-061 D9 assigns the assembled registry four properties: global
    uniqueness (enforced by the constructor's own duplicate-key check, not
    here), complete metadata, valid references, and non-contradictory
    defaults. This function enforces the latter three:

    * **Complete metadata** — every entry must carry non-empty ``impact``
      text. ``description_template`` stays genuinely optional (a kind can
      keep a bespoke, per-call-site description — see ``ChangeKindMeta``'s
      own docstring), so only ``impact`` is required. This was the fourth
      property blocked on writing 48 real, individually-accurate one-line
      descriptions for entries the catalog had never had one for — real
      domain content, not a mechanical check, and now done (ADR-061 Phase 5).
    * **Valid references** and **non-contradictory defaults** — see the
      ``policy_overrides``/``description_template`` checks below.

    Enum-membership completeness (every ``ChangeKind`` has exactly one
    registry entry, and no entry names a value outside the enum) is a
    distinct, already-enforced property, checked separately by
    ``tests/test_architecture_refactor.py``'s membership tests rather than
    here, since it requires comparing against the ``ChangeKind`` enum
    itself, which this leaf module does not import.

    Raises ``ValueError`` with the offending kind named, matching the
    constructor's existing duplicate-key failure mode, so a bad entry fails
    at import time rather than silently reaching a comparison.
    """
    if not e.impact.strip():
        raise ValueError(
            f"{e.kind!r}: impact must be non-empty — D9's \"complete "
            f"metadata\" catalog-validation property requires every entry "
            f"to carry human-readable impact text"
        )
    for policy, override in e.policy_overrides.items():
        if policy not in VALID_BASE_POLICIES:
            raise ValueError(
                f"{e.kind!r}: policy_overrides names unknown policy {policy!r}; "
                f"valid policies are {sorted(VALID_BASE_POLICIES)}"
            )
        if policy == "strict_abi":
            # strict_abi IS the base policy default_verdict already encodes —
            # an override under this key would be a second, competing source
            # of truth for the same policy rather than a real override.
            raise ValueError(
                f"{e.kind!r}: policy_overrides may not target 'strict_abi' "
                f"(that policy's verdict is default_verdict itself)"
            )
        if override == e.default_verdict:
            # A policy_overrides entry that restates default_verdict verbatim
            # is not an override at all — either the entry is stale after a
            # default_verdict edit, or it never needed to be declared.
            raise ValueError(
                f"{e.kind!r}: policy_overrides[{policy!r}] == default_verdict "
                f"({override!r}); a redundant override contradicts the point "
                f"of declaring one — remove it or pick a genuinely different verdict"
            )
        if policy in _VERDICT_BLIND_POLICIES and override != Verdict.COMPATIBLE:
            # checker_policy.policy_kind_sets() classifies every kind with a
            # 'sdk_vendor'/'plugin_abi' override as Verdict.COMPATIBLE
            # unconditionally (via _policy_override_kinds(), which gathers
            # only policy_overrides_for(policy)'s KEYS — the declared verdict
            # is never consulted at runtime). A declared override value other
            # than Verdict.COMPATIBLE would therefore pass the redundancy
            # check above while silently behaving as COMPATIBLE anyway — a
            # real metadata/runtime-behavior mismatch, not a redundant-override
            # duplicate (Codex review, PR #882). If a future policy's
            # implementation in policy_kind_sets() genuinely honors the
            # declared verdict, remove it from _VERDICT_BLIND_POLICIES rather
            # than special-casing around this check.
            raise ValueError(
                f"{e.kind!r}: policy_overrides[{policy!r}] declares {override!r}, "
                f"but checker_policy.policy_kind_sets() classifies every "
                f"{policy!r}-keyed kind as Verdict.COMPATIBLE unconditionally, "
                f"discarding the declared verdict — only Verdict.COMPATIBLE "
                f"matches this policy's actual runtime behavior today"
            )
    if e.is_addition and e.default_verdict != Verdict.COMPATIBLE:
        # addition_kinds() is documented as "a subset of COMPATIBLE" — an
        # is_addition entry whose own default_verdict disagrees with that
        # invariant is self-contradictory.
        raise ValueError(
            f"{e.kind!r}: is_addition=True requires default_verdict == "
            f"Verdict.COMPATIBLE (addition_kinds() is a subset of "
            f"COMPATIBLE_KINDS), got {e.default_verdict!r}"
        )
    if e.description_template is not None:
        # diff_helpers.make_change() formats description_template via
        # ``template.format(symbol=..., name=..., old=..., new=...,
        # detail=...)`` — a keyword-only call, so any field name outside
        # TEMPLATE_VOCAB, a positional `{}`/`{0}` (which that call shape can
        # never satisfy), an illegal conversion, a bad field nested inside a
        # format spec, or an invalid format code, all raise at format time —
        # but only the first time a finding of this kind is actually
        # formatted, not at registry construction. That is D9's "valid
        # references" property for this field, the same shape as the
        # policy_overrides checks above (Codex review, PR #882).
        try:
            _check_template_fields(e.description_template)
            _check_template_formats(e.description_template)
        except ValueError as exc:
            raise ValueError(f"{e.kind!r}: {exc}") from exc


class ChangeKindRegistry:
    """Registry of ChangeKindMeta entries, deriving classification sets.

    Usage::

        registry = ChangeKindRegistry(entries)
        breaking = registry.kinds_for_verdict(Verdict.BREAKING)
        impact = registry.impact_for("func_removed")
    """

    def __init__(self, entries: list[ChangeKindMeta]) -> None:
        self._entries: dict[str, ChangeKindMeta] = {}
        for e in entries:
            if e.kind in self._entries:
                raise ValueError(f"Duplicate registry entry for {e.kind!r}")
            _validate_entry(e)
            self._entries[e.kind] = e

    def __reduce__(self) -> tuple[type[ChangeKindRegistry], tuple[list[ChangeKindMeta]]]:
        """Reconstruct through ``__init__`` on unpickling (Codex review, PR #882,
        fresh evidence).

        Without this, pickle's default protocol restores an instance by
        calling ``cls.__new__(cls)`` and then setting ``__dict__`` directly
        from the pickled state — ``__init__`` (and therefore
        ``_validate_entry()``/the duplicate-key check) never runs. Returning
        ``(ChangeKindRegistry, (entries,))`` instead makes unpickling call
        ``ChangeKindRegistry(entries)`` exactly like any other construction
        path, so a registry pickled *under this revision* is re-validated
        every time it is restored, not just the first time it was built.

        This governs only pickles this revision *writes* — see
        ``__setstate__`` below for the pickles this revision merely *reads*
        (a pickle already on disk, written by a revision before this
        method existed).
        """
        return (ChangeKindRegistry, (list(self._entries.values()),))

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Revalidate state restored from a pickle written *before* ``__reduce__``
        existed (Codex review, PR #882, fresh evidence).

        ``__reduce__`` is encoded into the pickle bytes at *write* time —
        it only changes what a newly-written pickle looks like, not how an
        already-written one is read back. A real production ``REGISTRY``
        pickle written before this class defined ``__reduce__`` still
        carries the default protocol's ``__newobj__``-plus-raw-``__dict__``
        payload, and loading *that* payload bypasses ``__init__`` (and
        ``_validate_entry()``) exactly the way ``__reduce__``'s own
        docstring describes — a 397-entry registry with 48 empty-``impact``
        entries loads here as fully "real", contradicting the "complete
        metadata" guarantee. Python's unpickling machinery decides whether
        to call ``__setstate__`` by checking the *current*, in-memory class
        for the method — not by what the pickle's writer-time class
        defined — so simply defining this method retroactively covers that
        legacy payload too, with no version marker needed. Reconstructs
        through ``__init__`` exactly like ``__reduce__``'s own path, so a
        restored registry is re-validated regardless of which pickle
        format produced it. Never invoked for a pickle this revision wrote
        itself: ``__reduce__`` returns a 2-tuple with no state component,
        so pickle reconstructs directly via ``ChangeKindRegistry(entries)``
        and this method never runs for that path.

        Being a public method, it is directly callable on an already-
        constructed instance too — including the live, import-time-built
        production ``REGISTRY`` — not only on the blank instance the
        unpickler creates via ``cls.__new__(cls)``. Calling it there would
        re-run ``__init__`` on an object already in use, silently
        replacing its ``_entries`` in place while every classification
        set derived from the *original* ``REGISTRY`` at import time
        (``BREAKING_KINDS`` and siblings in ``checker_policy.py``) stays
        frozen and now disagrees with it. Guarded out the same way
        ``ChangeKindMeta.__setstate__`` guards its own instance (Codex
        review, PR #882, fresh evidence).
        """
        if hasattr(self, "_entries"):
            raise TypeError(
                "ChangeKindRegistry.__setstate__ refuses to overwrite an "
                "already-initialized instance"
            )
        entries_by_kind = state.get("_entries", {}) if isinstance(state, dict) else {}
        self.__init__(list(entries_by_kind.values()))  # type: ignore[misc]

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, kind_value: str) -> bool:
        return kind_value in self._entries

    def get(self, kind_value: str) -> ChangeKindMeta | None:
        return self._entries.get(kind_value)

    def kinds_for_verdict(self, verdict: Verdict) -> frozenset[str]:
        """Return all kind values whose default_verdict matches."""
        return frozenset(
            e.kind for e in self._entries.values() if e.default_verdict == verdict
        )

    def addition_kinds(self) -> frozenset[str]:
        """Return kind values flagged as additions (subset of COMPATIBLE)."""
        return frozenset(e.kind for e in self._entries.values() if e.is_addition)

    def policy_overrides_for(self, policy: str) -> dict[str, Verdict]:
        """Return {kind_value: overridden_verdict} for a given policy name."""
        return {
            e.kind: e.policy_overrides[policy]
            for e in self._entries.values()
            if policy in e.policy_overrides
        }

    def impact_text(self) -> dict[str, str]:
        """Return {kind_value: impact} for all entries with non-empty impact."""
        return {e.kind: e.impact for e in self._entries.values() if e.impact}

    def description_template_for(self, kind_value: str) -> str | None:
        """Return the description template for a kind, or None if bespoke/unknown."""
        e = self._entries.get(kind_value)
        return e.description_template if e is not None else None

    def templated_kinds(self) -> frozenset[str]:
        """Return kind values that own a description template (C6 migration set)."""
        return frozenset(
            e.kind for e in self._entries.values() if e.description_template is not None
        )

    @property
    def entries(self) -> dict[str, ChangeKindMeta]:
        return dict(self._entries)

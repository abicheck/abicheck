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

"""The generic, payload-exclusion-aware dataclass string walk
``qualified_name_segments.renumber_anonymous_closure_identities`` builds on.

Split out of ``qualified_name_segments.py`` (mechanical extraction, unchanged
function bodies) once that module crossed the AI-readiness ``file-size``
gate's 800-line production cap -- the same "move responsibility instead of
raising the baseline" discipline ``fact_field_readers_scope.py``/
``fact_detector_misuse_scope.py`` already establish elsewhere in this
codebase. Leaf module (stdlib only), same as its former home.
"""

from __future__ import annotations

import dataclasses as _dataclasses
from collections.abc import Callable as _Callable
from enum import Enum as _Enum

__all__ = [
    "_PAYLOAD_FIELD_EXCLUSIONS",
    "_collect_strings",
    "_legacy_sibling_is_payload_excluded",
    "_walk_rewrite_strings",
]

#: Dataclass field names that carry free-text/expression payload, never a
#: type/name spelling -- so a coincidental substring matching the closure
#: marker syntax must not be collected as (fabricated) identity evidence or
#: rewritten as if it were one (Codex review: a ``RecordType.deprecated``
#: message like ``"avoid (lambda:x.h:10:2)"`` was silently corrupted to
#: ``"avoid (lambda:x.h#1)"``). Shared across every declaration dataclass in
#: ``model.py`` that has a field of this name (``Function``/``Variable``/
#: ``TypeField``/``RecordType``/``EnumType``/``EnumMember`` all document
#: ``deprecated`` as "see Function.deprecated for the message-string
#: convention"; ``Param.default``/``TypeField.default`` are documented
#: "verbatim, value not preserved"), matched by name alone rather than
#: per-dataclass, since the walk in ``_collect_strings``/
#: ``_walk_rewrite_strings`` is itself dataclass-agnostic. ``Variable.value``
#: (its compile-time constant initializer, "if known", model.py's own
#: docstring) is the identical payload shape -- added after the same
#: reachable-corruption pattern was found on it too (Codex review, fresh
#: evidence). ``source_location``/``source_header`` (ADR-015 provenance --
#: a filesystem path, optionally with ``:line:col`` appended, never a C++
#: type/name spelling) are the same shape again: a legal path containing
#: marker-shaped text of its own (``/tmp/(lambda:a.h:1:2)``) was rewritten
#: even for a snapshot with no real closure at all, corrupting persisted
#: declaration provenance and, transitively, any later header-origin/
#: dependency-scoping decision that reads it (Codex review, fresh evidence).
_PAYLOAD_FIELD_EXCLUSIONS: frozenset[str] = frozenset(
    {"deprecated", "default", "value", "source_location", "source_header"}
)


def _collect_strings(value: object, out: list[str]) -> None:
    """Append every ``str`` reachable from *value* to *out*, recursing
    through dataclasses, lists/tuples, and dicts (keys and values) --
    except a field named in :data:`_PAYLOAD_FIELD_EXCLUSIONS`.

    A ``(str, Enum)`` member (e.g. a ``BuildMode`` enum -- this codebase's
    own established shape, see ``serialization.py``) is excluded from the
    plain-``str`` case even though ``isinstance(x, str)`` is true for one:
    treating it as ordinary text here is harmless for *collection*, but the
    identical check in :func:`_walk_rewrite_strings` below must not, so both
    stay symmetric rather than silently diverging on what counts as a string.
    """
    if isinstance(value, str) and not isinstance(value, _Enum):
        out.append(value)
    elif _dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = _dataclasses.fields(value)
        # ADR-063 Phase 5: every `model.fact.Fact[T]` sibling now reachable
        # from `functions`/`variables`/`types`/`enums` (up to ~10 per
        # declaration) makes this walk's own "cheap no-op when nothing
        # embeds a marker" common case measurably non-cheap (real PR #982
        # perf-gate regression, ~2x on the serialize scenario) unless a
        # `Fact`'s own `status: FactStatus` field is skipped -- it is
        # structurally guaranteed to never hold a string (`FactStatus` is a
        # plain `enum.Enum`, not `(str, Enum)`; `model/` has no other field
        # literally named "status"), so recursing into it can never
        # contribute to `out`. Recognized the same cheap-first structural
        # way `_walk_rewrite_strings`' own `is_fact_value_field` already
        # recognizes a `Fact` (this module is deliberately import-free, see
        # its own docstring) -- a class literally named "Fact" with this
        # exact field shape is close enough that a false positive would
        # need a coincidentally-identical, unrelated type. Read-only here,
        # so (unlike `_walk_rewrite_strings`) frozen-ness doesn't matter and
        # `fields` is reused instead of calling `dataclasses.fields()` a
        # second time just to build the comparison set.
        is_fact = type(value).__name__ == "Fact" and {f.name for f in fields} == {
            "status",
            "value",
            "diagnostics",
        }
        for f in fields:
            if (
                f.name in _PAYLOAD_FIELD_EXCLUSIONS
                or _legacy_sibling_is_payload_excluded(f.name)
            ):
                continue
            if is_fact and f.name == "status":
                continue
            _collect_strings(getattr(value, f.name), out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_strings(item, out)
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and not isinstance(k, _Enum):
                out.append(k)
            _collect_strings(v, out)


def _legacy_sibling_is_payload_excluded(field_name: str | None) -> bool:
    """Whether *field_name* (a ``<x>_fact`` sibling) describes the same
    payload its own legacy field ``<x>`` does, and that legacy field is
    itself listed in :data:`_PAYLOAD_FIELD_EXCLUSIONS`.

    ``source_header_fact`` must be excluded exactly like ``source_header``
    is -- a filesystem path, never identity-bearing text -- while
    ``qualified_name_fact`` must not be, matching ``qualified_name``'s own
    non-exclusion (Codex review, fresh evidence: a real closure marker and
    a legal source-header path can coincidentally embed the identical
    normalized marker text, e.g. a type spelling ``(lambda:x.h:5:1)``
    alongside a path ``/tmp/(lambda:x.h:5:1)/api.h`` -- without this check,
    the earlier ``is_fact_value_field`` override rewrote every reachable
    ``Fact[...]``'s ``value`` unconditionally, including
    ``source_header_fact``, corrupting real provenance the legacy
    ``source_header`` field itself was correctly left untouched).
    """
    if field_name is None or not field_name.endswith("_fact"):
        return False
    return field_name[: -len("_fact")] in _PAYLOAD_FIELD_EXCLUSIONS


def _walk_rewrite_strings(
    value: object, rewrite: _Callable[[str], str], *, field_name: str | None = None
) -> object:
    """Rewrite every ``str`` reachable from *value* via ``rewrite(s)``,
    mutating dataclasses/lists/dicts in place where possible -- except a
    field named in :data:`_PAYLOAD_FIELD_EXCLUSIONS`. Returns the (possibly
    new) value -- a bare ``str`` can't be mutated in place.

    ``field_name`` is the dataclass field name this call is processing the
    value *of* (``None`` for a call with no single owning field -- the
    top-level call, or a list/dict element one level removed from its own
    field) -- propagated through container recursion so a ``Fact[...]``
    reached via a payload-excluded ``<x>_fact`` sibling (e.g.
    ``source_header_fact``) is recognized as such once inside it; see
    :func:`_legacy_sibling_is_payload_excluded`.

    A **frozen** dataclass is rebuilt via ``dataclasses.replace`` rather
    than mutated: ``setattr`` on one raises ``FrozenInstanceError``
    outright, so this walk would crash the whole dump the moment any
    reachable model field held one. That is not hypothetical -- ADR-063
    Phase 2's ``entity_id`` carrier (a frozen ``model.identity.EntityId``,
    itself holding a tuple of frozen scope segments) is reachable from
    ``functions``/``variables``/``types``/``enums``, all four of which
    ``qualified_name_segments._LAMBDA_IDENTITY_FIELDS`` walks. Rebuilding is
    also the right *behaviour*, not merely a way to avoid the exception: a
    closure marker that survives unrewritten inside an identity carrier
    would leave that carrier keyed on the raw ``:line:col`` spelling this
    whole function exists to remove, i.e. path/line-tainted identity next to
    a normalized one. Only ``init=True`` fields can be handed to
    ``replace``; a changed ``init=False`` field on a frozen dataclass is
    instead applied via ``object.__setattr__`` -- the same escape hatch a
    frozen dataclass's own ``__post_init__`` uses to set a derived field,
    and the established convention elsewhere in this codebase for the
    identical need (see ``compatibility_evaluation_config.py``) -- applied
    AFTER ``replace`` rebuilds the ``init=True`` fields, onto the
    freshly-rebuilt object rather than the original, so a rewrite touching
    both kinds of field in one dataclass lands on the object this function
    actually returns (Codex review, PR #943): a reachable ``init=False``
    field can itself hold a closure marker (e.g. one populated from a
    rewritten ``init=True`` field inside ``__post_init__``), and silently
    discarding its rewrite would leave that field pointing at stale,
    path/line-tainted content even though the dataclass it belongs to was
    otherwise correctly rebuilt.
    """
    if isinstance(value, str) and not isinstance(value, _Enum):
        return rewrite(value)
    if _dataclasses.is_dataclass(value) and not isinstance(value, type):
        params = getattr(value, "__dataclass_params__", None)
        is_frozen = bool(getattr(params, "frozen", False))
        # ADR-063 Phase 5 (Codex review): `_PAYLOAD_FIELD_EXCLUSIONS`'s
        # "value" entry exists for `Variable.value` (a compile-time
        # constant, not identity-bearing text) — but `model.fact.Fact[T]`'s
        # own payload field is *also* named `value`, and a `Fact[str]`
        # sibling (`qualified_name_fact`/`source_header_fact`) legitimately
        # wraps the exact identity spelling this walk exists to renumber.
        # Recognized structurally, not by importing `Fact` (this module is
        # deliberately import-free) -- a class literally named "Fact" with
        # this shape is close enough that a false positive would need a
        # coincidentally-identical, unrelated type. Without this, a
        # closure-marker-embedded qualified_name/source_header gets
        # renumbered on the legacy field but left stale inside its own
        # Fact sibling, persisting two conflicting spellings.
        is_fact_value_field = (
            type(value).__name__ == "Fact"
            and is_frozen
            and {f.name for f in _dataclasses.fields(value)}
            == {"status", "value", "diagnostics"}
            and not _legacy_sibling_is_payload_excluded(field_name)
        )
        replacements: dict[str, object] = {}
        frozen_field_updates: dict[str, object] = {}
        for f in _dataclasses.fields(value):
            if f.name in _PAYLOAD_FIELD_EXCLUSIONS and not (
                is_fact_value_field and f.name == "value"
            ):
                continue
            old = getattr(value, f.name)
            new = _walk_rewrite_strings(old, rewrite, field_name=f.name)
            if new is old:
                continue
            if not is_frozen:
                setattr(value, f.name, new)
            elif f.init:
                replacements[f.name] = new
            else:
                frozen_field_updates[f.name] = new
        if replacements or frozen_field_updates:
            value = _dataclasses.replace(value, **replacements)
        for name, new in frozen_field_updates.items():
            object.__setattr__(value, name, new)
        return value
    if isinstance(value, list):
        for i, item in enumerate(value):
            new_item = _walk_rewrite_strings(item, rewrite, field_name=field_name)
            if new_item is not item:
                value[i] = new_item
        return value
    if isinstance(value, tuple):
        return tuple(
            _walk_rewrite_strings(item, rewrite, field_name=field_name)
            for item in value
        )
    if isinstance(value, dict):
        rewritten: dict[object, object] = {}
        changed = False
        for k, v in value.items():
            new_k = rewrite(k) if isinstance(k, str) and not isinstance(k, _Enum) else k
            new_v = _walk_rewrite_strings(v, rewrite, field_name=field_name)
            rewritten[new_k] = new_v
            if new_k != k or new_v is not v:
                changed = True
        if changed:
            value.clear()
            value.update(rewritten)
        return value
    return value

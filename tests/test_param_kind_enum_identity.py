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

"""A ``str``-subclass ``Enum`` field reachable from the closure-marker walk
(``qualified_name_segments._collect_strings``/``_walk_rewrite_strings``) must
never be silently downcast to a plain ``str``.

Reported: ``compare`` crashed with ``AttributeError: 'str' object has no
attribute 'value'`` at ``diff_types_abicc_parity.py``'s
``removed_const_overload`` detector (``p.kind.value``), for ANY snapshot
containing at least one closure/anonymous-type marker anywhere
(``functions``/``variables``/``types``/``enums``/``typedefs``/
``typedefs_qualified``/``fact_provenance``) -- "any marker-bearing L2 dump".

Root cause: ``Param.kind`` is a ``ParamKind(str, Enum)``, which satisfies
``isinstance(value, str)``. ``_walk_rewrite_strings`` treated it exactly
like ordinary free text and handed it to ``name_classification.
strip_anonymous_type_location``, which unconditionally applies
``re.Pattern.sub(...)`` -- and unlike this module's own
``apply_anonymous_type_ordinals`` (which has a fast identity-preserving
``if "(" not in name: return name`` path), ``re.sub`` returns a genuinely
NEW, plain ``str`` object even when there are zero matches. Since
``Param.kind``'s own values (``"value"``, ``"pointer"``, ...) never contain
a marker, the rewrite was always a no-op *in content* but never a no-op *in
type*: ``storage.snapshot_load_normalization.
normalize_anonymous_type_spellings_on_load`` (called unconditionally by
``serialization.snapshot_from_dict`` on every load) walked into every
``Param.kind`` reachable from a marker-bearing snapshot's ``functions`` and
silently replaced the ``ParamKind`` enum member with a bare ``str`` of the
same spelling, only surfacing much later as the reported ``AttributeError``.

Fixed generally (not by special-casing ``Param.kind``): ``_collect_strings``/
``_walk_rewrite_strings`` now treat a ``str``-subclass ``Enum`` member as
opaque, never as rewritable free text -- closing the same downcast for any
current or future ``str`` `Enum` field reachable from the walked containers
(``Visibility``, ``AccessLevel``, ``ElfVisibility``, ...), not just
``ParamKind``.
"""

from __future__ import annotations

import enum

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Function, Param, ParamKind
from abicheck.name_classification import strip_anonymous_type_location
from abicheck.qualified_name_segments import _collect_strings, _walk_rewrite_strings
from abicheck.serialization import snapshot_from_dict
from abicheck.storage.snapshot_load_normalization import (
    normalize_anonymous_type_spellings_on_load,
)


def _marker_bearing_snapshot(pointer_kind: ParamKind) -> AbiSnapshot:
    """A minimal snapshot with (a) a marker somewhere in ``types`` -- enough
    to make the whole-snapshot walk activate at all -- and (b) a
    ``Function`` with both a const and non-const overload, each carrying a
    pointer :class:`Param`, exactly what
    ``diff_types_abicc_parity._diff_const_overloads`` groups on."""
    from abicheck.model import RecordType

    return AbiSnapshot(
        library="libwidget.so",
        version="1.0",
        types=[
            RecordType(
                name="raii_guard<(lambda:x.h:1:2)>",
                kind="class",
                size_bits=8,
            )
        ],
        functions=[
            Function(
                name="Widget::getPtr",
                mangled="_ZN6Widget6getPtrEPi",
                return_type="int",
                params=[
                    Param(name="p", type="int*", kind=pointer_kind, pointer_depth=1)
                ],
                is_const=True,
            ),
            Function(
                name="Widget::getPtr",
                mangled="_ZN6Widget6getPtrEPiv",
                return_type="int",
                params=[
                    Param(name="p", type="int*", kind=pointer_kind, pointer_depth=1)
                ],
                is_const=False,
            ),
        ],
    )


class TestWalkPrimitivesPreserveEnumIdentity:
    """The primitive contract, tested directly -- not just through its
    highest-level caller (AGENTS.md's "Primitive-level property tests")."""

    def test_walk_rewrite_strings_never_downcasts_a_str_enum_member(self) -> None:
        param = Param(name="p", type="int", kind=ParamKind.POINTER)
        result = _walk_rewrite_strings(param, strip_anonymous_type_location)
        assert isinstance(result.kind, ParamKind)
        assert result.kind is ParamKind.POINTER

    def test_walk_rewrite_strings_still_rewrites_a_real_marker_elsewhere(self) -> None:
        """The fix must not turn the whole rewrite into a no-op -- an
        ordinary string field with a real marker is still normalized; only
        the enum-typed field is left alone."""
        param = Param(name="p", type="(lambda at /a/foo.h:1:2)", kind=ParamKind.POINTER)
        result = _walk_rewrite_strings(param, strip_anonymous_type_location)
        assert isinstance(result.kind, ParamKind)
        assert result.type == "(lambda:foo.h:1:2)"

    def test_collect_strings_does_not_collect_enum_members(self) -> None:
        param = Param(name="p", type="int", kind=ParamKind.POINTER)
        out: list[str] = []
        _collect_strings(param, out)
        assert ParamKind.POINTER.value not in out
        assert "int" in out

    def test_collect_strings_skips_enum_dict_keys_too(self) -> None:
        class _K(str, enum.Enum):
            A = "a"

        out: list[str] = []
        _collect_strings({_K.A: "value"}, out)
        assert "a" not in out
        assert "value" in out


class TestEndToEndMarkerBearingSnapshotLoadPreservesParamKind:
    """Reproduces the actual reported crash end to end: load a
    marker-bearing snapshot through the real public boundary and run the
    detector that dereferences ``Param.kind.value``."""

    def test_normalize_on_load_preserves_param_kind_enum(self) -> None:
        snap = _marker_bearing_snapshot(ParamKind.POINTER)
        normalize_anonymous_type_spellings_on_load(snap)
        for fn in snap.functions:
            for p in fn.params:
                assert isinstance(p.kind, ParamKind)

    def test_const_overload_detector_does_not_crash_on_marker_bearing_snapshot(
        self,
    ) -> None:
        """``_diff_const_overloads`` builds its ``(name, param_signature)``
        grouping key -- the one that dereferences ``p.kind.value`` -- over
        EVERY public function in both snapshots, unconditionally, on every
        ``compare()`` call: the crash does not require an actual const
        overload to be removed, only a marker anywhere in the snapshot plus
        any function with a non-empty parameter list. Reproduces the
        reported crash verbatim: prior to the fix this call raised
        ``AttributeError: 'str' object has no attribute 'value'``.
        """
        old = _marker_bearing_snapshot(ParamKind.POINTER)
        new = _marker_bearing_snapshot(ParamKind.POINTER)
        # A real const-overload removal too (const dropped, non-const
        # kept), so the detector's own finding is exercised, not just its
        # grouping key.
        new.functions = [f for f in new.functions if not f.is_const]

        result = compare(old, new)

        assert any(c.kind == ChangeKind.REMOVED_CONST_OVERLOAD for c in result.changes)

    def test_snapshot_from_dict_round_trip_preserves_param_kind_enum(self) -> None:
        """The real public loading boundary
        (``snapshot_from_dict`` -> on-load normalization), not just the
        in-memory shortcut -- a fix verified only against a hand-built
        object can pass identically before and after the regression."""
        snap = _marker_bearing_snapshot(ParamKind.POINTER)
        raw = {
            "library": snap.library,
            "version": snap.version,
            "types": [
                {"name": t.name, "kind": t.kind, "size_bits": t.size_bits}
                for t in snap.types
            ],
            "functions": [
                {
                    "name": f.name,
                    "mangled": f.mangled,
                    "return_type": f.return_type,
                    "is_const": f.is_const,
                    "params": [
                        {
                            "name": p.name,
                            "type": p.type,
                            "kind": p.kind.value,
                            "pointer_depth": p.pointer_depth,
                        }
                        for p in f.params
                    ],
                }
                for f in snap.functions
            ],
        }
        loaded = snapshot_from_dict(raw)
        for fn in loaded.functions:
            for p in fn.params:
                assert isinstance(p.kind, ParamKind)

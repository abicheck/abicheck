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

"""A DTO materialized once per exported symbol carries no per-instance dict.

**Bug class.** A small frozen dataclass is cheap to read and cheap to
write, and its cost is invisible at the definition site -- but when one is
built per row of a real binary's export table, the per-instance
``__dict__`` dominates the object itself. Measured here: 48 B of object
plus 296 B of ``__dict__``, so the dictionary is six times the data. The
defect is not a bug in any single call site and no test of behaviour can
see it; it only shows up as allocation pressure proportional to the input,
which is why it needs a structural assertion rather than a benchmark.

**General invariant**, stated over the module rather than the one type that
prompted it: every frozen dataclass in ``model/export_index.py`` -- the
module whose whole job is per-symbol export rows -- is slotted, and
instances expose no ``__dict__``. Asserted by *constructing* an instance
and checking it, not by reading the decorator's keyword, because
``slots=True`` is one of several ways to get there and the property that
matters is the absence of the dictionary.

Scoped deliberately: this says nothing about DTOs elsewhere that are built
once per run, where a ``__dict__`` costs nothing worth defending against.
"""

from __future__ import annotations

import dataclasses
import sys

import pytest

from abicheck.model import export_index


def _frozen_dataclasses():
    for name in dir(export_index):
        obj = getattr(export_index, name)
        if (
            isinstance(obj, type)
            and dataclasses.is_dataclass(obj)
            and obj.__module__ == export_index.__name__
        ):
            yield name, obj


def _instance(cls):
    """Build one with every field defaulted or trivially filled."""
    kwargs = {}
    for field in dataclasses.fields(cls):
        if field.default is not dataclasses.MISSING:
            continue
        if field.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            continue
        ann = str(field.type)
        if "str" in ann and "tuple" not in ann:
            kwargs[field.name] = "x"
        elif "tuple" in ann:
            kwargs[field.name] = ()
        elif "bool" in ann:
            kwargs[field.name] = True
        elif "int" in ann:
            kwargs[field.name] = 0
        else:
            kwargs[field.name] = "x"
    return cls(**kwargs)


class TestPerSymbolDtosAreSlotted:
    def test_every_frozen_dataclass_in_the_module_is_slotted(self) -> None:
        """Module-wide, so a newly added per-symbol row type is covered too."""
        unslotted = []
        for name, cls in _frozen_dataclasses():
            try:
                inst = _instance(cls)
            except Exception as exc:  # pragma: no cover - construction guard
                pytest.fail(f"could not construct {name}: {exc!r}")
            if getattr(inst, "__dict__", None) is not None:
                unslotted.append(name)
        assert not unslotted, (
            f"these per-symbol DTOs still carry a per-instance __dict__: "
            f"{unslotted}. Each costs ~296 B on top of ~48 B of data, "
            f"multiplied by every row of a real export table."
        )

    def test_the_module_actually_has_dataclasses_to_check(self) -> None:
        """Vacuity guard.

        The sweep above passes trivially if the discovery predicate stops
        matching -- a rename, a move, or a decorator change would silently
        empty it while still reporting success.
        """
        found = [n for n, _ in _frozen_dataclasses()]
        assert "RawExportEntry" in found, found
        assert "RawExportIndex" in found, found

    def test_a_slotted_entry_is_materially_smaller(self) -> None:
        """The reason for the rule, asserted rather than asserted-about.

        Compares against an unslotted twin declared here, so the oracle is
        a real measurement of both shapes on this interpreter rather than a
        number copied from a comment that could drift.
        """

        @dataclasses.dataclass(frozen=True)
        class UnslottedTwin:
            name: str
            is_default: bool = True
            ordinal: int | None = None
            sym_type: str | None = None
            is_data: bool | None = None
            visibility: str | None = None

        slotted = export_index.RawExportEntry(
            "_Z3foov", True, None, "FUNC", None, "default"
        )
        unslotted = UnslottedTwin("_Z3foov", True, None, "FUNC", None, "default")

        slotted_bytes = sys.getsizeof(slotted)
        unslotted_bytes = sys.getsizeof(unslotted) + sys.getsizeof(unslotted.__dict__)
        assert slotted_bytes < unslotted_bytes, (
            f"slotted {slotted_bytes} B is not smaller than unslotted "
            f"{unslotted_bytes} B"
        )
        # Not a pinned constant: interpreter versions move these numbers. The
        # claim is that removing the dict is a large fraction, not that it is
        # exactly 264 B.
        assert unslotted_bytes - slotted_bytes > 100


class TestBehaviourIsUnchanged:
    """Slotting must not alter the value semantics any consumer relies on."""

    def test_equality_hashing_and_field_reads_still_work(self) -> None:
        a = export_index.RawExportEntry("_Z3foov", True, None, "FUNC", None, "default")
        b = export_index.RawExportEntry("_Z3foov", True, None, "FUNC", None, "default")
        assert a == b
        assert hash(a) == hash(b)
        assert len({a, b}) == 1
        assert a.name == "_Z3foov"
        assert a.sym_type == "FUNC"
        assert a.is_default is True

    def test_defaults_still_apply(self) -> None:
        e = export_index.RawExportEntry("bare")
        assert e.is_default is True
        assert e.ordinal is None
        assert e.visibility is None

    def test_it_is_still_frozen(self) -> None:
        e = export_index.RawExportEntry("x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            e.name = "y"  # type: ignore[misc]

    def test_an_unknown_attribute_cannot_be_attached(self) -> None:
        """The property is that it fails -- not which exception it raises.

        A trap worth recording, because it will confuse whoever meets it
        next. ``@dataclass(slots=True)`` cannot add slots to an existing
        class, so it builds and returns a *new* one; the frozen
        ``__setattr__`` generated for the original closed over that
        original class. For a declared field the guard fires first and the
        familiar ``FrozenInstanceError`` is raised, but for an undeclared
        name it falls through to ``super(original_cls, self)``, where
        ``self`` is an instance of the *new* class -- and CPython raises
        ``TypeError: super(type, obj): obj ... is not an instance or
        subtype of type``.

        This is not something this change introduced: `SymbolSignatureStatus`
        in `bundle_models.py` has been `frozen=True, slots=True` for longer
        and behaves identically (verified). A frozen dataclass *without*
        slots raises `FrozenInstanceError` here instead. Either way the
        assignment fails and nothing is attached, which is the invariant
        worth pinning; asserting the exception type would pin an
        interpreter implementation detail that CPython may well change.
        """
        e = export_index.RawExportEntry("x")
        with pytest.raises((AttributeError, TypeError)):
            e.extra = 1  # type: ignore[attr-defined]
        assert not hasattr(e, "extra"), "an undeclared attribute was attached"

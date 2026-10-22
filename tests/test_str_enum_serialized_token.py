"""Every str-valued enum in ``abicheck`` serializes to its value on any Python.

CPython 3.15's ``json`` encodes a ``str`` subclass through ``str()``, so a
``class X(str, Enum)`` member (whose ``str()`` is ``"X.NAME"``) is written as
``"X.NAME"`` instead of its value: snapshots, baselines and caches written on
3.15 then fail to load anywhere. Same-encoder round trips cannot see this --
both directions run on the same interpreter -- so this test checks the
serialized *token* against the member's declared value, which holds on 3.11
through 3.15 alike only when the enum is a ``StrEnum``.
"""

from __future__ import annotations

import enum
import importlib
import json
import pkgutil
import types

import pytest

import abicheck


def _import_or_none(name: str) -> types.ModuleType | None:
    try:
        return importlib.import_module(name)
    except Exception:  # optional-dependency modules
        return None


def _str_enums() -> list[type[enum.Enum]]:
    found: dict[str, type[enum.Enum]] = {}
    for info in pkgutil.walk_packages(abicheck.__path__, "abicheck."):
        module = _import_or_none(info.name)
        if module is None:
            continue
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, enum.Enum)
                and issubclass(obj, str)
                and obj.__module__.startswith("abicheck")
            ):
                found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return [found[k] for k in sorted(found)]


_ENUMS = _str_enums()


def test_discovers_the_known_serialized_enums() -> None:
    names = {e.__name__ for e in _ENUMS}
    assert {"ChangeKind", "Visibility", "SymbolBinding", "Verdict"} <= names
    assert len(_ENUMS) > 50


@pytest.mark.parametrize("enum_cls", _ENUMS, ids=lambda e: e.__qualname__)
def test_str_enum_member_text_is_its_value(enum_cls: type[enum.Enum]) -> None:
    assert issubclass(enum_cls, enum.StrEnum), (
        f"{enum_cls.__module__}.{enum_cls.__qualname__} mixes str into Enum; "
        "use enum.StrEnum so str()/format()/json emit the value on every Python"
    )
    for member in enum_cls:
        # str() is what CPython 3.15's json encoder serializes; the oracle is
        # the declared value, not a round trip through the same encoder.
        assert str(member) == member.value
        assert f"{member}" == member.value
        assert json.dumps({member: member}) == json.dumps({member.value: member.value})

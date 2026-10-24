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

"""Generic ``.abicheck.yml`` key-table engine (strict schema, ADR-043).

``BuildConfig``'s recognized keys are declared once, as a table of
:class:`KeySpec` rows in ``build_config.py``. This module is the mechanism
that table is run through: it checks a raw mapping against the table
(unknown keys, wrong YAML types, whole-block validators), reads the values
into ``BuildConfig`` constructor arguments, and dumps the non-default values
back out. Adding a key is one table row, not a dataclass-side ``from_dict``
read, ``to_dict`` emission, registry entry and type-table entry.

It lives here, not in ``build_config.py``, because it has no dependency on
``BuildConfig`` itself and is ``extract``-classified (it may import only the
``model``/``storage`` layers and sibling leaf modules). The few validators a
table row can point at that need a richer dependency (``EnvironmentMatrix``,
``model.bundle_variants``) are defined here too, for the same reason.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..environment_matrix import EnvironmentMatrix
from ..model.bundle_variants import bundle_variants_findings

#: Recognized top-level keys whose value is validated by a dedicated,
#: whole-block validator rather than a flat key table: ``deployment:`` embeds
#: ``EnvironmentMatrix``'s nested shape, ``bundle_variants:`` (ADR-062 A1.6)
#: is a name -> spec mapping (``model.bundle_variants``).
STRUCTURED_SUBKEY_TYPES: dict[str, dict[str, str]] = {
    "scope": {
        "dependencies": "list[{name: str, header_roots: list[str]}]",
        "dependency_evidence": "str ('full')",
    },
}


class Kind(StrEnum):
    """How one table row's YAML value is typed and read/written.

    ``BOOL``/``STR``/``INT``/``LIST``/``DICT_STR`` are plain typed leaves, each
    filling one ``BuildConfig`` field. ``CUSTOM`` is a leaf validated by its own
    ``validate`` callable and parsed elsewhere (no field of its own).
    ``GROUP`` is a whole block's extra keys, read and written as one unit
    (``scope:`` ownership). ``WHOLE`` is a whole top-level block with its own
    ``validate``/``read``/``write``.
    """

    BOOL = "bool"
    STR = "str"
    INT = "int"
    LIST = "list"
    DICT_STR = "dict_str"
    CUSTOM = "custom"
    GROUP = "group"
    WHOLE = "whole"


@dataclass(frozen=True)
class KeySpec:
    """One recognized ``.abicheck.yml`` key.

    ``path`` is ``"block.sub"`` for a block sub-key, a bare name for a
    top-level scalar (``"version"``), or a bare block name for a ``GROUP``/
    ``WHOLE`` row. ``attr`` is the ``BuildConfig`` field it fills (``None``
    when the key is only validated). ``normalize(value, path)`` runs after a
    present, type-checked value is read and may raise ``ValueError``.
    ``always_emit`` makes a ``WHOLE`` row dump whenever its field is not its
    default, even when its block is empty.
    """

    path: str
    kind: Kind
    attr: str | None = None
    default: Any = None
    normalize: Callable[[Any, str], Any] | None = None
    validate: Callable[[Any], list[str]] | None = None
    read: Callable[[Any], Any] | None = None
    write: Callable[[Any], dict[str, Any]] | None = None
    always_emit: bool = False


def _block(data: dict[str, Any], key: str) -> dict[str, Any]:
    """One top-level block as a mapping (``{}`` when absent or not a mapping)."""
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _type_findings(spec: KeySpec, label: str, value: object) -> list[str]:
    """Type findings for one present value of *spec*; *label* names it in a message."""
    if spec.kind is Kind.CUSTOM or spec.kind is Kind.WHOLE:
        return spec.validate(value) if spec.validate is not None else []
    if spec.kind is Kind.BOOL and not isinstance(value, bool):
        return [f"{label} must be a boolean, got {type(value).__name__}: {value!r}"]
    if spec.kind is Kind.STR and not isinstance(value, str):
        return [f"{label} must be a string, got {type(value).__name__}: {value!r}"]
    if spec.kind is Kind.INT and (
        not isinstance(value, int) or isinstance(value, bool)
    ):
        return [f"{label} must be an integer, got {type(value).__name__}: {value!r}"]
    if spec.kind is Kind.DICT_STR:
        if not isinstance(value, dict):
            return [
                f"{label} must be a mapping of string to string, got "
                f"{type(value).__name__}: {value!r}"
            ]
        bad_pairs = [
            (k, v)
            for k, v in value.items()
            if not isinstance(k, str) or not isinstance(v, str)
        ]
        if bad_pairs:
            return [
                f"{label} must be a mapping of string to string, got "
                f"non-string key/value pair(s): {bad_pairs!r}"
            ]
    if spec.kind is Kind.LIST:
        if not isinstance(value, (list, str)):
            return [
                f"{label} must be a string or list of strings, "
                f"got {type(value).__name__}: {value!r}"
            ]
        # A bare string is folded to a 1-element list on read; a list container
        # must hold strings only, never coerced via ``str(x)``.
        bad = (
            [x for x in value if not isinstance(x, str)]
            if isinstance(value, list)
            else []
        )
        if bad:
            return [
                f"{label} must be a list of strings, got non-string element(s): {bad!r}"
            ]
    return []


def _read_leaf(spec: KeySpec, raw: object) -> Any:
    """One typed leaf value, normalized; an absent or ``null`` value is the default."""
    if raw is None:
        return _fresh(spec.default)
    value: Any = raw
    if spec.kind is Kind.LIST:
        value = [value] if isinstance(value, str) else list(value)
    elif spec.kind is Kind.DICT_STR:
        value = dict(value)
    if spec.normalize is not None:
        value = spec.normalize(value, spec.path)
    return value


def _fresh(default: Any) -> Any:
    """A copy of a mutable default, so no two configs share one list/dict."""
    if isinstance(default, list):
        return list(default)
    if isinstance(default, dict):
        return dict(default)
    return default


def _plain(value: Any) -> Any:
    """A YAML-dumpable copy of a leaf value."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value


def deployment_findings(value: object) -> list[str]:
    """Type findings for the top-level ``deployment:`` key.

    Not a fixed subkey set, since it embeds ``EnvironmentMatrix``'s own nested
    YAML shape (a ``compilers`` list, ``sycl:``/``cuda:`` sub-blocks, a
    ``runtime_floors:`` dict), so it delegates to ``EnvironmentMatrix.from_dict``
    rather than re-declaring that shape here.
    """
    if value is None:
        return []
    if not isinstance(value, dict):
        return [f"deployment must be a mapping, got {type(value).__name__}: {value!r}"]
    try:
        EnvironmentMatrix.from_dict(value, strict=True)
    except (TypeError, ValueError) as exc:
        return [f"deployment: {exc}"]
    return []


def bundle_variants_block_findings(value: object) -> list[str]:
    """Type findings for the top-level ``bundle_variants:`` key."""
    return [] if value is None else bundle_variants_findings(value)


class KeyTable:
    """Checks, reads and dumps a ``.abicheck.yml`` mapping against a key table.

    *specs* is the full table in dump order (blocks are emitted at the position
    of their first row). *opaque_top* lists recognized top-level keys that no
    row describes and that this loader accepts without parsing (a sibling
    module owns them).
    """

    def __init__(self, specs: tuple[KeySpec, ...], opaque_top: frozenset[str]) -> None:
        self.specs = specs
        #: ``block`` -> ``sub`` -> spec, for every row a block sub-key owns.
        self.blocks: dict[str, dict[str, KeySpec]] = {}
        #: Bare top-level scalar name -> spec (``version``).
        self.top_scalars: dict[str, KeySpec] = {}
        #: Whole top-level block name -> spec.
        self.whole: dict[str, KeySpec] = {}
        for spec in specs:
            if spec.kind is Kind.GROUP:
                continue
            if spec.kind is Kind.WHOLE:
                self.whole[spec.path] = spec
            elif "." in spec.path:
                block, sub = spec.path.split(".", 1)
                self.blocks.setdefault(block, {})[sub] = spec
            else:
                self.top_scalars[spec.path] = spec
        #: Every recognized top-level key.
        self.known_top: frozenset[str] = frozenset(
            opaque_top
            | self.blocks.keys()
            | self.top_scalars.keys()
            | self.whole.keys()
        )
        #: Every recognized block sub-key, per block.
        self.known_blocks: dict[str, frozenset[str]] = {
            block: frozenset(subs) for block, subs in self.blocks.items()
        }

    def findings(self, data: dict[str, Any]) -> list[str]:
        """Every structural problem in *data*, collected (not just the first)."""
        out: list[str] = []
        for key, value in data.items():
            if key not in self.known_top:
                out.append(f"unknown .abicheck.yml key {key!r}")
            elif key in self.whole:
                out += _type_findings(self.whole[key], key, value)
            elif key in self.blocks:
                out += self._block_findings(key, value)
            elif key in self.top_scalars and value is not None:
                out += _type_findings(self.top_scalars[key], key, value)
        return out

    def _block_findings(self, block: str, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, dict):
            return [f"{block} must be a mapping, got {type(value).__name__}: {value!r}"]
        out: list[str] = []
        for sub, sub_value in value.items():
            spec = self.blocks[block].get(sub)
            if spec is None:
                out.append(f"unknown .abicheck.yml key {block}.{sub!r}")
            else:
                out += _type_findings(spec, f"{block}.{sub}", sub_value)
        return out

    def read(self, top: dict[str, Any]) -> dict[str, Any]:
        """Constructor arguments (``BuildConfig`` field name -> value) from *top*.

        Assumes :meth:`findings` already passed for *top*.
        """
        values: dict[str, Any] = {}
        for spec in self.specs:
            if spec.kind is Kind.GROUP:
                assert spec.read is not None
                values.update(spec.read(_block(top, spec.path)))
            elif spec.kind is Kind.WHOLE:
                if spec.attr is not None and spec.read is not None:
                    values[spec.attr] = spec.read(top)
            elif spec.attr is not None:
                if "." in spec.path:
                    block, sub = spec.path.split(".", 1)
                    values[spec.attr] = _read_leaf(spec, _block(top, block).get(sub))
                else:
                    values[spec.attr] = _read_leaf(spec, top.get(spec.path))
        return values

    def dump(self, cfg: Any) -> dict[str, Any]:
        """The non-default ``.abicheck.yml`` mapping of *cfg* (a ``BuildConfig``).

        A leaf is emitted only when its value differs from its default, so a
        dumped config stays minimal and reloads to the same ``BuildConfig``.
        """
        out: dict[str, Any] = {}
        for spec in self.specs:
            if spec.kind is Kind.GROUP:
                assert spec.write is not None
                block_values = spec.write(cfg)
                if block_values:
                    out.setdefault(spec.path, {}).update(block_values)
            elif spec.kind is Kind.WHOLE:
                if spec.write is None or spec.attr is None:
                    continue
                block_value = spec.write(cfg)
                emit = (
                    getattr(cfg, spec.attr) != spec.default
                    if spec.always_emit
                    else bool(block_value)
                )
                if emit:
                    out[spec.path] = block_value
            elif spec.attr is not None:
                value = getattr(cfg, spec.attr)
                if value == spec.default:
                    continue
                if "." in spec.path:
                    block, sub = spec.path.split(".", 1)
                    out.setdefault(block, {})[sub] = _plain(value)
                else:
                    out[spec.path] = _plain(value)
        return out

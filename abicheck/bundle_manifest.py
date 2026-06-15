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

"""Instantiation-manifest input for bundle-aware analysis (ADR-023).

A *manifest* is the list of symbols (typically mangled, often template
instantiations) that a release publicly promises to ship. The bundle layer
(:mod:`abicheck.bundle`) enforces that every promised entry has at least one
matching exported symbol in the new bundle. This module defines the manifest
data model (:class:`ManifestEntry`, :class:`InstantiationManifest`) and the
YAML/JSON loader (:func:`load_manifest`) plus its parsing/validation helpers.

This is a leaf module: it imports nothing from :mod:`abicheck.bundle`. The
manifest types and loader are re-exported from :mod:`abicheck.bundle` so the
historical ``from abicheck.bundle import ManifestEntry`` import paths keep
working.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast


@dataclass(frozen=True)
class ManifestEntry:
    """One promised entry in an :class:`InstantiationManifest`.

    Exactly one of ``symbol``, ``pattern``, or ``template`` is set.

    - ``symbol`` — a literal mangled symbol name. Matched by equality
      against the bundle's exported-symbol set. Useful when the contract
      genuinely is one specific symbol (versioned entry points, dlsym
      plugin contracts).
    - ``pattern`` — a glob (``fnmatch`` semantics, with ``*`` and ``?``)
      matched against the *demangled* form of every exported symbol.
      Match is found-iff any exported symbol's demangled form matches
      the glob. Best for "any train_ops<*> for these algorithm classes"
      type promises that headers don't capture as a contract.
    - ``template`` — a C++ qualified template name plus an
      ``instantiations`` list of parameter assignments. abicheck
      expands each assignment into a substring of the demangled form
      ``Template<v1, v2, ...>`` and looks for at least one exported
      symbol whose demangled name contains it. The natural shape for
      libraries (for example oneDAL, libtorch, or MKL) that maintain an
      explicit instantiation matrix in their build system.
    """

    symbol: str | None = None  # literal mangled symbol
    pattern: str | None = None  # fnmatch glob on demangled form
    template: str | None = None  # C++ qualified template name
    instantiations: tuple[dict[str, str], ...] = ()  # for template form
    library: str | None = None  # provider when optional_provider=False
    optional_provider: bool = True  # True = any sibling may provide

    def kind(self) -> str:
        """Return ``'symbol'``, ``'pattern'``, or ``'template'`` for diagnostics."""
        if self.symbol is not None:
            return "symbol"
        if self.pattern is not None:
            return "pattern"
        return "template"

    def display_name(self) -> str:
        """Best human-readable identifier for the entry (used in findings).

        For template entries, expands the instantiations into the same
        ``Template<arg1, arg2>`` form the matcher uses so the finding
        actually identifies *which* parameter set failed — otherwise
        users would see ``Template`` and have no idea which instantiation
        was missing.
        """
        if self.symbol is not None:
            return self.symbol
        if self.pattern is not None:
            return self.pattern
        if self.template is not None and self.instantiations:
            expanded = _expand_instantiations(self.template, self.instantiations)
            return ", ".join(expanded)
        return self.template or "<empty>"


@dataclass(frozen=True)
class InstantiationManifest:
    """A list of symbols a release publicly promises to ship.

    Loaded from a YAML/JSON file via :func:`load_manifest`. The bundle
    layer enforces that every entry has at least one matching exported
    symbol in the new bundle (or at the named provider when
    ``optional_provider=False``).
    """

    entries: tuple[ManifestEntry, ...]

    @property
    def symbols(self) -> frozenset[str]:
        """Literal-symbol entries only (back-compat for existing callers)."""
        return frozenset(e.symbol for e in self.entries if e.symbol is not None)


def _expand_instantiations(
    template: str, instantiations: tuple[dict[str, str], ...]
) -> list[str]:
    """Build demangled-form substring patterns from a template + parameter list.

    Returns a list of strings like ``"acme::lib::train_ops<float, method::dense, task::train>"``
    that the matcher tests as substring against the demangled form of
    each exported symbol. Parameter order in the produced angle-bracket
    list is the iteration order of the dict (insertion order, preserved
    in Python 3.7+). YAML/JSON manifests therefore declare parameters
    in the same order the template's parameter list takes them.
    """
    expanded: list[str] = []
    for inst in instantiations:
        args = ", ".join(str(v) for v in inst.values())
        expanded.append(f"{template}<{args}>")
    return expanded


def _load_manifest_data(path: Path) -> dict[str, object]:
    """Read and parse YAML or JSON manifest file; validate top-level shape."""
    import json

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("provides"), list):
        raise ValueError(f"manifest {path}: missing top-level 'provides:' list")
    return cast("dict[str, object]", data)


def _validate_manifest_entry_shape(path: Path, raw: dict[str, object]) -> str:
    """Validate that *raw* has exactly one of symbol/pattern/template; return it."""
    shape_keys = [k for k in ("symbol", "pattern", "template") if k in raw]
    if len(shape_keys) == 0:
        raise ValueError(
            f"manifest {path}: entry must have one of 'symbol', "
            f"'pattern', or 'template': {raw!r}",
        )
    if len(shape_keys) > 1:
        raise ValueError(
            f"manifest {path}: entry has conflicting fields "
            f"{shape_keys!r}; pick exactly one: {raw!r}",
        )
    return shape_keys[0]


def _parse_template_instantiations(
    path: Path, raw: dict[str, object]
) -> tuple[dict[str, str], ...]:
    """Parse and coerce the 'instantiations' list from a template entry."""
    insts_raw = raw.get("instantiations", [])
    if not isinstance(insts_raw, list) or not insts_raw:
        raise ValueError(
            f"manifest {path}: template entry needs a non-empty "
            f"'instantiations:' list: {raw!r}",
        )
    insts: list[dict[str, str]] = []
    for inst in insts_raw:
        if not isinstance(inst, dict):
            raise ValueError(
                f"manifest {path}: each instantiation must be a "
                f"mapping of parameter name to value: {inst!r}",
            )
        # Preserve dict insertion order from YAML/JSON; coerce
        # values to str so YAML's `true`/`false`/numbers render
        # correctly in the expanded template signature.
        insts.append({str(k): str(v) for k, v in inst.items()})
    return tuple(insts)


def _parse_manifest_entry(path: Path, raw: dict[str, object]) -> ManifestEntry:
    """Convert one raw mapping from a manifest 'provides' list into a :class:`ManifestEntry`."""
    if not isinstance(raw, dict):
        raise ValueError(f"manifest {path}: entry is not a mapping: {raw!r}")
    shape = _validate_manifest_entry_shape(path, raw)
    optional_provider = raw.get("optional_provider", True)
    if not isinstance(optional_provider, bool):
        raise ValueError(
            f"manifest {path}: 'optional_provider' must be a boolean "
            f"(got {type(optional_provider).__name__} {optional_provider!r}): {raw!r}",
        )
    library = str(raw["library"]) if raw.get("library") else None
    if shape == "template":
        insts = _parse_template_instantiations(path, raw)
        return ManifestEntry(
            template=str(raw["template"]),
            instantiations=insts,
            library=library,
            optional_provider=optional_provider,
        )
    if shape == "pattern":
        return ManifestEntry(
            pattern=str(raw["pattern"]),
            library=library,
            optional_provider=optional_provider,
        )
    return ManifestEntry(
        symbol=str(raw["symbol"]),
        library=library,
        optional_provider=optional_provider,
    )


def load_manifest(path: Path) -> InstantiationManifest:
    """Load a manifest from YAML (``.yaml``/``.yml``) or JSON.

    Format (all three entry shapes are accepted; exactly one of
    ``symbol`` / ``pattern`` / ``template`` per entry)::

        version: 1
        provides:
          # 1. Literal symbol — exact match against .dynsym.
          - symbol: acme_lib_version
            library: libfoo_core.so.1
            optional_provider: false

          # 2. Glob pattern — fnmatch against demangled form.
          - pattern: "acme::lib::detail::train_kernel<*>*"
            library: libfoo_core.so.1
            optional_provider: false

          # 3. Template + instantiations — natural shape for template libs.
          - template: acme::lib::train_ops
            instantiations:
              - {Float: float,  Method: "method::dense",  Task: "task::train"}
              - {Float: float,  Method: "method::sparse", Task: "task::train"}
              - {Float: double, Method: "method::dense",  Task: "task::train"}
            library: libfoo_core.so.1
            optional_provider: false
    """
    data = _load_manifest_data(path)
    provides = cast("list[dict[str, object]]", data["provides"])
    entries = [_parse_manifest_entry(path, raw) for raw in provides]
    return InstantiationManifest(entries=tuple(entries))

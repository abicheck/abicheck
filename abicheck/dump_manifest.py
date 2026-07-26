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

"""ADR-050 D3 (G32 Phase B) — the ``--dump-manifest`` YAML document: a strict
parser for a multi-translation-unit dump description.

**Schema (all paths are resolved relative to the manifest file's own
directory — "root-relative", the same normalization
:func:`abicheck.comparability.compute_extraction_contract` already assumes
for a manifest-driven extraction, per the ADR's own "for the manifest path,
both fingerprints' roots are simply the manifest file's own directory"
note)**::

    compiler: c++                    # optional, default "c++"
    target: null                     # optional target-triple string
    frontend_context: host           # optional, default "host" (see below)
    public_header_paths: []          # optional, ADR-015 provenance inputs
    public_header_dirs: []           # optional, ADR-015 provenance inputs
    roots:                           # required, non-empty
      - include/foo.h                # the declared public surface (D1
      - include/bar.h                # scope_fingerprint's declared_headers)
    translation_units:               # required, non-empty
      - name: legacy-main            # required, unique across the manifest
        forced_includes:             # ordered; headers force-included into
          - include/foo.h            # this TU's own compile (the manifest
                                      # equivalent of a single named header)
        includes:                    # ordered; this TU's own -I search dirs
          - vendor/include           # a bare string: ancestor rule decides
          - path: ../src             # ownership (D1, unchanged); a mapping:
            project_owned: true      # explicitly project-owned regardless
        required: true               # optional, default true
        contributes_to_abi: true     # optional, default true

``roots`` is the manifest-mode equivalent of ``--header``/``-H`` (what's
being *compared* — feeds ``scope_fingerprint``'s ``declared_headers``).
``public_header_paths``/``public_header_dirs`` is the separate, optional
ADR-015 *provenance*-classification input (``--public-header``/
``--public-header-dir``'s manifest equivalent) — a different concept D1
already keeps distinct from scope declaration; a manifest with public
headers but no separate provenance fields behaves exactly like a legacy
``dump -H foo.h`` invocation with no ``--public-header`` (every declaration
stays ``UNKNOWN`` origin). A TU's ``forced_includes`` is what that TU
actually compiles (the manifest equivalent of a single named ``--header``,
typically drawn from ``roots`` but not required to be — a TU may force-include
a private support header alongside a public one); ``includes`` is that TU's
own ``-I`` search-path list for resolving those headers' own ``#include``s.

**Every parse-time schema violation raises
:class:`abicheck.errors.ManifestValidationError`** — unknown top-level or
per-TU fields, a duplicate mapping key anywhere in the document (PyYAML's
default ``safe_load`` silently accepts a duplicate key with
last-value-wins semantics; since ``required``/``contributes_to_abi``/
``project_owned``/``frontend_context`` all control extraction scope or
hard-fail behavior, a duplicate key here is exactly the kind of silent
scope drift this whole ADR exists to catch, not a cosmetic issue), a
duplicate TU ``name``, the ``contributes_to_abi=True ⇒ required=True``
invariant (a TU whose declarations feed the ABI model cannot also be
allowed to fail silently — "optional but contributes" is the exact shape
that produces a false removal), and a declared ``frontend_context`` other
than ``"host"`` (Phase D, not this phase, adds a real host/device
selector — accepting the field now but rejecting any non-default value
gives Phase D somewhere to plug in without a later schema change, while
never handing back a snapshot a caller could mistake for device-derived
before that selector exists).

A manifest declaring TUs with different compilers/target triples is
impossible to express in the first place, by construction: ``compiler``/
``target`` are base-profile (top-level) fields only, never accepted at the
per-TU level — the existing unknown-field check already rejects an attempt
to override either there, so no separate heterogeneous-context check is
needed on top of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ManifestValidationError

#: Accepted top-level manifest fields.
_TOP_LEVEL_FIELDS = frozenset(
    {
        "compiler",
        "target",
        "frontend_context",
        "public_header_paths",
        "public_header_dirs",
        "roots",
        "translation_units",
    }
)

#: Accepted per-TU fields.
_TU_FIELDS = frozenset(
    {"name", "includes", "forced_includes", "required", "contributes_to_abi"}
)

#: Accepted keys on a mapping-form ``includes`` entry (``{path: ..., project_owned: true}``).
_INCLUDE_MAPPING_FIELDS = frozenset({"path", "project_owned"})

#: The only ``frontend_context`` value this phase can honor — Phase D adds
#: the real host/device selector; see this module's own docstring.
_SUPPORTED_FRONTEND_CONTEXTS = frozenset({"host"})


def _load_yaml_strict(text: str, *, source: str) -> Any:
    """Parse *text* as YAML, raising :class:`ManifestValidationError` on a
    duplicate mapping key anywhere in the document.

    ``yaml.safe_load`` alone silently accepts ``{a: 1, a: 2}`` with
    last-value-wins semantics (PyYAML's ``SafeConstructor.construct_mapping``
    never checks for a repeated key). A ``SafeLoader`` subclass overriding
    ``construct_mapping`` to check for repeats is the standard, minimal way
    to close this without hand-rolling a YAML parser.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError(
            "PyYAML is required for --dump-manifest support. "
            "Install it with: pip install pyyaml"
        ) from exc

    class _StrictLoader(yaml.SafeLoader):
        pass

    def _construct_mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=True)
            if key in seen:
                raise ManifestValidationError(
                    f"{source}: duplicate key {key!r} in the same mapping "
                    f"(line {key_node.start_mark.line + 1})"
                )
            seen.add(key)
            mapping[key] = loader.construct_object(value_node, deep=True)
        return mapping

    _StrictLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
    )

    try:
        return yaml.load(text, Loader=_StrictLoader)
    except ManifestValidationError:
        raise
    except yaml.YAMLError as exc:
        raise ManifestValidationError(f"{source}: invalid YAML: {exc}") from exc


def _reject_unknown_fields(
    data: dict[str, Any], allowed: frozenset[str], *, context: str
) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ManifestValidationError(
            f"{context}: unknown field(s) {', '.join(unknown)!s} "
            f"(accepted: {', '.join(sorted(allowed))})"
        )


def _require_str(data: dict[str, Any], key: str, *, context: str, default: str | None = None) -> str:
    if key not in data:
        if default is not None:
            return default
        raise ManifestValidationError(f"{context}: missing required field {key!r}")
    value = data[key]
    if not isinstance(value, str) or not value:
        raise ManifestValidationError(
            f"{context}: {key!r} must be a non-empty string, got {value!r}"
        )
    return value


def _optional_bool(data: dict[str, Any], key: str, *, context: str, default: bool) -> bool:
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, bool):
        raise ManifestValidationError(
            f"{context}: {key!r} must be a boolean, got {value!r}"
        )
    return value


def _resolve_path(raw: str, *, base_dir: Path, context: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ManifestValidationError(f"{context}: expected a non-empty path string, got {raw!r}")
    p = Path(raw)
    return p if p.is_absolute() else (base_dir / p)


@dataclass(frozen=True)
class IncludeEntry:
    """One ``translation_units[].includes`` entry: a resolved ``-I``
    directory, optionally marked ``project_owned`` (ADR-050 D1's "sibling
    support root" escape hatch — the manifest-idiom equivalent of the
    legacy CLI's labeled ``--include old:LABEL=PATH``, needing no separate
    label since manifest paths are already root-relative/side-normalized).
    """

    path: Path
    project_owned: bool = False


@dataclass(frozen=True)
class TranslationUnit:
    """One ``translation_units[]`` entry."""

    name: str
    forced_includes: tuple[Path, ...] = ()
    includes: tuple[IncludeEntry, ...] = ()
    required: bool = True
    contributes_to_abi: bool = True


@dataclass(frozen=True)
class DumpManifest:
    """A parsed, validated ``--dump-manifest`` document."""

    base_dir: Path
    compiler: str = "c++"
    target: str | None = None
    frontend_context: str = "host"
    public_header_paths: tuple[Path, ...] = ()
    public_header_dirs: tuple[Path, ...] = ()
    roots: tuple[Path, ...] = ()
    translation_units: tuple[TranslationUnit, ...] = field(default_factory=tuple)


def _parse_includes(
    raw: Any, *, base_dir: Path, context: str
) -> tuple[IncludeEntry, ...]:
    if not isinstance(raw, list):
        raise ManifestValidationError(f"{context}: 'includes' must be a list, got {raw!r}")
    entries: list[IncludeEntry] = []
    for idx, item in enumerate(raw):
        item_ctx = f"{context}.includes[{idx}]"
        if isinstance(item, str):
            entries.append(
                IncludeEntry(path=_resolve_path(item, base_dir=base_dir, context=item_ctx))
            )
        elif isinstance(item, dict):
            _reject_unknown_fields(item, _INCLUDE_MAPPING_FIELDS, context=item_ctx)
            path_raw = item.get("path")
            if not isinstance(path_raw, str) or not path_raw:
                raise ManifestValidationError(
                    f"{item_ctx}: mapping form requires a non-empty 'path' string"
                )
            project_owned = item.get("project_owned", False)
            if not isinstance(project_owned, bool):
                raise ManifestValidationError(
                    f"{item_ctx}: 'project_owned' must be a boolean, got {project_owned!r}"
                )
            entries.append(
                IncludeEntry(
                    path=_resolve_path(path_raw, base_dir=base_dir, context=item_ctx),
                    project_owned=project_owned,
                )
            )
        else:
            raise ManifestValidationError(
                f"{item_ctx}: expected a path string or a {{path, project_owned}} "
                f"mapping, got {item!r}"
            )
    return tuple(entries)


def _parse_path_list(raw: Any, *, base_dir: Path, context: str) -> tuple[Path, ...]:
    if not isinstance(raw, list):
        raise ManifestValidationError(f"{context}: expected a list, got {raw!r}")
    return tuple(
        _resolve_path(item, base_dir=base_dir, context=f"{context}[{idx}]")
        for idx, item in enumerate(raw)
    )


def _parse_tu(raw: Any, *, base_dir: Path, index: int) -> TranslationUnit:
    if not isinstance(raw, dict):
        raise ManifestValidationError(
            f"translation_units[{index}]: expected a mapping, got {raw!r}"
        )
    context = f"translation_units[{index}]"
    _reject_unknown_fields(raw, _TU_FIELDS, context=context)
    name = _require_str(raw, "name", context=context)
    context = f"translation_units[{index}] ({name!r})"
    forced_includes = (
        _parse_path_list(raw["forced_includes"], base_dir=base_dir, context=f"{context}.forced_includes")
        if "forced_includes" in raw
        else ()
    )
    includes = (
        _parse_includes(raw["includes"], base_dir=base_dir, context=context)
        if "includes" in raw
        else ()
    )
    required = _optional_bool(raw, "required", context=context, default=True)
    contributes_to_abi = _optional_bool(raw, "contributes_to_abi", context=context, default=True)
    if contributes_to_abi and not required:
        raise ManifestValidationError(
            f"{context}: 'contributes_to_abi: true' requires 'required: true' "
            "-- a TU whose declarations feed the ABI model cannot also be "
            "allowed to fail silently (ADR-050 D3)"
        )
    return TranslationUnit(
        name=name,
        forced_includes=forced_includes,
        includes=includes,
        required=required,
        contributes_to_abi=contributes_to_abi,
    )


def parse_manifest(text: str, *, base_dir: Path, source: str = "<manifest>") -> DumpManifest:
    """Parse and validate a manifest document already read into *text*.

    *base_dir* is the directory every relative path in the document
    resolves against (the manifest file's own directory, for a real file —
    kept as a separate parameter so this function itself never touches the
    filesystem, matching :func:`load_manifest`'s split below).
    """
    data = _load_yaml_strict(text, source=source)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ManifestValidationError(
            f"{source}: manifest must be a YAML mapping, got {type(data).__name__}"
        )
    _reject_unknown_fields(data, _TOP_LEVEL_FIELDS, context=source)

    compiler = _require_str(data, "compiler", context=source, default="c++")
    target_raw = data.get("target")
    if target_raw is not None and not isinstance(target_raw, str):
        raise ManifestValidationError(f"{source}: 'target' must be a string or null")
    frontend_context = _require_str(data, "frontend_context", context=source, default="host")
    if frontend_context not in _SUPPORTED_FRONTEND_CONTEXTS:
        raise ManifestValidationError(
            f"{source}: frontend_context {frontend_context!r} is not supported yet "
            "-- device context selection requires ADR-050 Phase D (G32), not yet "
            "implemented; only 'host' is accepted"
        )
    public_header_paths = (
        _parse_path_list(data["public_header_paths"], base_dir=base_dir, context=f"{source}.public_header_paths")
        if "public_header_paths" in data
        else ()
    )
    public_header_dirs = (
        _parse_path_list(data["public_header_dirs"], base_dir=base_dir, context=f"{source}.public_header_dirs")
        if "public_header_dirs" in data
        else ()
    )
    if "roots" not in data:
        raise ManifestValidationError(f"{source}: missing required field 'roots'")
    roots = _parse_path_list(data["roots"], base_dir=base_dir, context=f"{source}.roots")
    if not roots:
        raise ManifestValidationError(f"{source}: 'roots' must be non-empty")

    if "translation_units" not in data:
        raise ManifestValidationError(f"{source}: missing required field 'translation_units'")
    tus_raw = data["translation_units"]
    if not isinstance(tus_raw, list) or not tus_raw:
        raise ManifestValidationError(f"{source}: 'translation_units' must be a non-empty list")
    tus = tuple(
        _parse_tu(item, base_dir=base_dir, index=idx) for idx, item in enumerate(tus_raw)
    )
    seen_names: set[str] = set()
    for tu in tus:
        if tu.name in seen_names:
            raise ManifestValidationError(
                f"{source}: duplicate translation unit name {tu.name!r}"
            )
        seen_names.add(tu.name)

    return DumpManifest(
        base_dir=base_dir,
        compiler=compiler,
        target=target_raw,
        frontend_context=frontend_context,
        public_header_paths=public_header_paths,
        public_header_dirs=public_header_dirs,
        roots=roots,
        translation_units=tus,
    )


def load_manifest(path: Path) -> DumpManifest:
    """Load and validate a ``--dump-manifest`` YAML file from *path*.

    Raises:
        ManifestValidationError: On any schema violation (see this module's
            docstring for the exact list).
        OSError: If *path* cannot be read.
    """
    text = path.read_text(encoding="utf-8")
    return parse_manifest(text, base_dir=path.resolve().parent, source=str(path))


def single_tu_manifest(
    headers: list[Path],
    includes: list[Path],
    *,
    base_dir: Path,
    compiler: str = "c++",
    name: str = "legacy-main",
) -> DumpManifest:
    """Build the internal one-TU manifest a legacy single-header ``dump``/
    ``compare`` invocation is equivalent to (ADR-050 D3: "all existing
    single-header/``-H`` CLI invocations construct a single-TU manifest
    internally... no behavior change for a caller not opting into a
    manifest file").

    Not used by ``load_manifest`` (a real file always goes through the
    strict parser above) — this is the synthesis direction, for
    :mod:`abicheck.dumper_manifest`'s dispatch to represent the legacy path
    as the manifest path's own one-TU special case rather than a parallel
    implementation.
    """
    return DumpManifest(
        base_dir=base_dir,
        compiler=compiler,
        roots=tuple(headers),
        translation_units=(
            TranslationUnit(
                name=name,
                forced_includes=tuple(headers),
                includes=tuple(IncludeEntry(path=p) for p in includes),
                required=True,
                contributes_to_abi=True,
            ),
        ),
    )

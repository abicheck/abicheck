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

"""Per-library header/include/compile-context override manifest (G38 Phase 17).

``compare_release_against_bundle_facts()`` (``bundle_side_input.py``) has
accepted ``per_library_headers``/``per_library_includes``/``per_library_
compile: dict[str, CompileContext]`` since G38 Phase 13 follow-up -- exactly
what a mixed-toolchain bundle needs (oneDAL's own CPU/plain-C++ vs.
``-fsycl``/``icpx`` DPC++ libraries sharing one umbrella header tree, the
whole reason G38 Phase 17 exists per its own "Origin" table). Nothing on the
CLI path forwarded them: ``compare --old-bundle-facts`` always passed a
single, uniform ``headers``/``includes``/``compile`` triple to every
library. This module is the manifest half that closes that gap --
``compare_bundle_facts.dispatch()`` is the caller.

**Not wired into ``.abicheck.yml`` discovery, and deliberately so** -- the
identical reasoning ``bundle_variants_config.py``'s own module docstring
already gives for its ``bundle_variants:`` block applies here without
change: ``BuildConfig`` (``abicheck/buildsource/inline.py``) has a fixed,
declared-field schema, and a genuinely new top-level config block would need
real schema/model work and loading/precedence plumbing a CLI-only manifest
file avoids entirely. This module therefore takes an already-parsed raw
``dict`` (whatever a caller's own YAML/JSON loader produced), not a
``.abicheck.yml`` path.

**Physically under ``abicheck/workflows/``, unlike ``bundle_variants_
config.py``'s own flat-root placement (Codex review, verified against
AGENTS.md's task-routing table).** Root AGENTS.md's "Task routing and
dependency direction" section is explicit: "route new behavior to the
target owner rather than extending a flat root prefix family" -- the
pre-existing flat `bundle_*.py` siblings (`bundle_variants_config.py`
included) predate ADR-061 and are grandfathered into that family's
`legacy_paths`/`architecture/debt.yaml` entries, not a precedent for where
genuinely *new* code should land. This module coordinates a `compare`-shaped
workflow's manifest input (`workflows`'s own routing-table row: "Coordinate
dump, compare, scan, release, aggregate, project, or dependency behavior"),
so it lives here from the start, with no `architecture/modules.yaml`
allowlist edit needed at all -- physical location under `abicheck/
workflows/` is what classifies it, exactly as ADR-061 intends for new code.

Validates eagerly and completely before returning anything, mirroring
``bundle_variants_config.parse_bundle_variants_config()``'s own convention:
a malformed entry is a hard :class:`BundleFactsLibraryOverridesError`, never
a silent no-op or a partial result a caller might not notice. A library name
not present in the bundle it's applied against (when *known_libraries* is
given) is also a hard error, not a silent no-op -- G38 Phase 17's own
testing-bar text calls this out explicitly as a required case, since a
typo'd library name in the manifest would otherwise silently fall back to
the uniform default with no signal anything was wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..compile_context import CompileContext

#: Recognized keys inside one library's own mapping. The compile-context
#: fields mirror `CompileContext`'s own field names exactly (rather than the
#: CLI flag spellings, e.g. `--gcc-path`) since this manifest is a direct,
#: per-library alternative to the uniform CLI flags, not a second CLI
#: surface of its own.
_HEADER_KEYS = frozenset({"headers", "includes"})
_COMPILE_KEYS = frozenset(
    {
        "gcc_path",
        "gcc_prefix",
        "gcc_options",
        "sysroot",
        "nostdinc",
        "frontend",
        "frontend_context",
    }
)
_LIBRARY_KEYS = _HEADER_KEYS | _COMPILE_KEYS


class BundleFactsLibraryOverridesError(ValueError):
    """A per-library override manifest failed eager validation."""


@dataclass(frozen=True)
class BundleFactsLibraryOverrides:
    """Parsed per-library override maps, ready to forward unchanged into
    ``compare_release_against_bundle_facts()``'s own ``per_library_headers``/
    ``per_library_includes``/``per_library_compile`` parameters."""

    headers: dict[str, list[Path]] = field(default_factory=dict)
    includes: dict[str, list[Path]] = field(default_factory=dict)
    compile: dict[str, CompileContext] = field(default_factory=dict)


def _require_str_list(value: object, *, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise BundleFactsLibraryOverridesError(
            f"{where}: must be a list of strings, got {type(value).__name__}"
        )
    if any(not v for v in value):
        # Codex review: an empty string is a `str`, so it passed the
        # isinstance check above cleanly -- but `_resolve_path("", base_dir=
        # ...)` (below) resolves an empty *relative* path to `base_dir`
        # itself (`Path("/x") / Path("") == Path("/x")`), silently turning
        # an accidentally blank `headers: [""]` entry into "scan the
        # manifest's own directory" instead of the clean rejection every
        # other malformed path value here gets.
        raise BundleFactsLibraryOverridesError(f"{where}: must not contain an empty string")
    return value


def _resolve_path(raw: str, *, base_dir: Path | None) -> Path:
    # Mirrors dump_manifest._resolve_path exactly (Codex review): a manifest
    # is a portable, shareable document, so a relative header/include/sysroot
    # path inside it must anchor to the manifest's own directory, not
    # whatever directory the `compare` process happens to be launched from.
    # `base_dir=None` (no real manifest file behind this call, e.g. a
    # Python-API caller passing an in-memory dict) keeps the path exactly as
    # given, deferred to the caller the same way it always was.
    p = Path(raw)
    if base_dir is None or p.is_absolute():
        return p
    return base_dir / p


def _resolve_str_list_as_paths(
    value: object, *, base_dir: Path | None, where: str
) -> list[Path]:
    return [
        _resolve_path(p, base_dir=base_dir) for p in _require_str_list(value, where=where)
    ]


def parse_bundle_facts_library_overrides(
    raw: dict[str, object],
    *,
    known_libraries: set[str] | None = None,
    base_dir: Path | None = None,
) -> BundleFactsLibraryOverrides:
    """Validate a raw per-library override mapping.

    *raw* is shaped ``{library_name: {headers: [...], includes: [...],
    gcc_path: ..., gcc_options: ..., sysroot: ..., ...}}`` -- one entry per
    library that needs a header root, include path, or compile context
    different from the comparison's uniform ``--header``/``--include``/
    compile-context flags. A library entirely absent from *raw* keeps the
    uniform fallback (``compare_release_against_bundle_facts()``'s own
    per-library lookup already falls back to the uniform value when a key is
    missing from these maps -- see ``bundle_side_input.py``), so an empty
    ``{}`` manifest is valid and changes nothing.

    *known_libraries*, when given, is the bundle's own canonical library-name
    set (from the resolved NEW-side match map) -- a manifest entry naming a
    library outside that set is a hard error, since a typo'd name would
    otherwise silently fall back to the uniform default with no signal
    anything is wrong.

    *base_dir*, when given, is the directory every relative ``headers``/
    ``includes``/``sysroot`` path resolves against -- the manifest file's own
    parent directory, for a real manifest file (Codex review: without this, a
    manifest stored outside the process's current working directory silently
    resolved its relative paths against the wrong directory, exactly the
    portability trap ``dump_manifest.load_manifest()``'s own ``base_dir``
    threading already closed for ``--dump-manifest``). An absolute path is
    never altered. ``None`` (the default) keeps every path exactly as
    written, for a caller with no real manifest file behind the raw dict.
    """
    if not isinstance(raw, dict):
        raise BundleFactsLibraryOverridesError(
            f"per-library override manifest: must be a mapping of library "
            f"name -> override spec, got {type(raw).__name__}"
        )
    headers: dict[str, list[Path]] = {}
    includes: dict[str, list[Path]] = {}
    compile_by_library: dict[str, CompileContext] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not name:
            raise BundleFactsLibraryOverridesError(
                f"per-library override manifest: library names must be "
                f"non-empty strings, got {name!r}"
            )
        if known_libraries is not None and name not in known_libraries:
            raise BundleFactsLibraryOverridesError(
                f"per-library override manifest: {name!r} is not a library "
                f"in this bundle -- known libraries are "
                f"{sorted(known_libraries)!r}"
            )
        if not isinstance(entry, dict):
            raise BundleFactsLibraryOverridesError(
                f"per-library override manifest.{name}: must be a mapping, "
                f"got {type(entry).__name__}"
            )
        non_str_keys = [k for k in entry if not isinstance(k, str)]
        if non_str_keys:
            # Codex review: a YAML mapping can carry a non-string key (e.g.
            # `2: 3`) -- `sorted(set(entry) - _LIBRARY_KEYS)` below would
            # raise a raw, untranslated TypeError comparing str to int
            # instead of the clean BundleFactsLibraryOverridesError every
            # other malformed-entry case here produces.
            raise BundleFactsLibraryOverridesError(
                f"per-library override manifest.{name}: keys must be "
                f"strings, got {non_str_keys!r}"
            )
        unknown = set(entry) - _LIBRARY_KEYS
        if unknown:
            raise BundleFactsLibraryOverridesError(
                f"per-library override manifest.{name}: unrecognized "
                f"key(s) {sorted(unknown)!r} -- known keys are "
                f"{sorted(_LIBRARY_KEYS)!r}"
            )
        if "headers" in entry:
            headers[name] = _resolve_str_list_as_paths(
                entry["headers"],
                base_dir=base_dir,
                where=f"per-library override manifest.{name}.headers",
            )
        if "includes" in entry:
            includes[name] = _resolve_str_list_as_paths(
                entry["includes"],
                base_dir=base_dir,
                where=f"per-library override manifest.{name}.includes",
            )
        compile_fields = {k: v for k, v in entry.items() if k in _COMPILE_KEYS}
        if compile_fields:
            compile_by_library[name] = _build_compile_context(
                compile_fields,
                where=f"per-library override manifest.{name}",
                base_dir=base_dir,
            )
    return BundleFactsLibraryOverrides(
        headers=headers, includes=includes, compile=compile_by_library
    )


def load_bundle_facts_library_overrides(
    manifest_path: Path,
    *,
    known_libraries: set[str] | None = None,
) -> BundleFactsLibraryOverrides:
    """Read, strictly parse, and validate a real
    ``--bundle-facts-library-manifest`` YAML/JSON file at *manifest_path*.

    The file-reading counterpart of :func:`parse_bundle_facts_library_overrides`
    above, kept in this ``workflows``-classified module rather than the
    ``frontends``-layer CLI dispatch code that calls it (Codex review):
    ``dump_manifest`` (the ``_load_yaml_strict`` duplicate-key-checking YAML
    loader below, shared with ``--dump-manifest``) is classified ``extract``,
    and ``frontends`` may not import ``extract`` directly
    (``architecture/modules.yaml``'s ``may_import: [model, workflows,
    report]``) -- only ``workflows`` may. Threads the manifest's own resolved
    parent directory through as *base_dir*, so a relative ``headers``/
    ``includes``/``sysroot`` path inside the manifest anchors to the manifest
    file itself rather than the calling process's current working directory.

    Raises :class:`BundleFactsLibraryOverridesError` (a ``ValueError``
    subclass) on invalid YAML, a duplicate mapping key anywhere in the
    document, or any schema violation :func:`parse_bundle_facts_library_overrides`
    itself raises.
    """
    from ..dump_manifest import _load_yaml_strict
    from ..errors import ManifestValidationError

    try:
        raw = _load_yaml_strict(
            manifest_path.read_text(encoding="utf-8"), source=str(manifest_path)
        )
    except ManifestValidationError as exc:
        raise BundleFactsLibraryOverridesError(
            f"--bundle-facts-library-manifest {manifest_path}: {exc}"
        ) from exc
    return parse_bundle_facts_library_overrides(
        raw if raw is not None else {},
        known_libraries=known_libraries,
        base_dir=manifest_path.resolve().parent,
    )


def known_libraries_for_new_side(
    new_dir: Path, *, include_private_dso: bool = False
) -> set[str]:
    """The canonical library-name set a NEW-side directory resolves to.

    The same primitives (and the same ``include_private_dso`` semantics)
    ``bundle_side_input.compare_release_against_bundle_facts`` itself uses to
    build its own NEW-side match map -- exposed separately so
    ``compare_bundle_facts.dispatch()`` can validate
    ``--bundle-facts-library-manifest``'s library names *before* running the
    real comparison, without re-deriving this resolution independently and
    risking drift.

    Lives in this ``workflows``-classified module, not
    ``bundle_side_input.py`` (Codex review, fresh evidence): that module is
    grandfathered flat-root legacy (``architecture/modules.yaml``'s
    ``legacy_paths``/frozen ``bundle_`` family) -- being import-compatible
    with it (`frontends -> workflows` is legal, so this function is already
    reachable through it) does not make it the right place to *add* new
    behavior, the same "route new behavior to the target owner, not a flat
    root family" rule that moved this file's own manifest parser here in the
    first place. ``package.discover_shared_libraries``/``.extraction.
    build_match_map`` are ``extract``-classified, which ``frontends`` may
    not import directly (``may_import: [model, workflows, report]``) --
    ``workflows`` already may.
    """
    from ..package import discover_shared_libraries
    from .extraction import build_match_map

    new_files = discover_shared_libraries(new_dir, include_private=include_private_dso)
    new_map, _match_warnings = build_match_map(new_files)
    return set(new_map)


def _build_compile_context(
    fields: dict[str, object], *, where: str, base_dir: Path | None
) -> CompileContext:
    # `gcc_options` here is a list of already-separate compiler-flag tokens,
    # mirroring the modern `--compiler-option` CLI flag (repeatable, one
    # literal argument each) that `resolve_compile_context()` maps straight
    # onto `CompileContext.gcc_option_tokens` -- not the legacy free-form,
    # shlex-split `--gcc-options` string CLI audit PR 5/5 removed as a flag.
    # A per-library manifest entry should not need this codebase's platform-
    # dependent quote-aware tokenizer just to express "these N flags."
    gcc_options = fields.get("gcc_options", [])
    if not isinstance(gcc_options, list) or not all(
        isinstance(v, str) for v in gcc_options
    ):
        raise BundleFactsLibraryOverridesError(
            f"{where}.gcc_options: must be a list of strings, got "
            f"{type(gcc_options).__name__}"
        )
    str_fields: dict[str, str | None] = {}
    for key, default, nullable in (
        ("gcc_path", None, True),
        ("gcc_prefix", None, True),
        ("sysroot", None, True),
        ("frontend", "auto", False),
        ("frontend_context", "host", False),
    ):
        value = fields.get(key, default)
        if value is None and not nullable:
            # Codex review: `frontend`/`frontend_context` are given real
            # defaults above, not "absent means None" the way gcc_path/
            # gcc_prefix/sysroot are -- an explicit `frontend: null` in the
            # manifest is present-but-wrong-type, not "key omitted." Without
            # this check it sailed past the `isinstance` check below (`value
            # is not None` is False) and only failed later, at the
            # `assert frontend is not None` a few lines down -- an
            # AssertionError, not the clean BundleFactsLibraryOverridesError
            # every other malformed-field case here raises.
            raise BundleFactsLibraryOverridesError(
                f"{where}.{key}: must be a string, got null"
            )
        if value is not None and not isinstance(value, str):
            raise BundleFactsLibraryOverridesError(
                f"{where}.{key}: must be a string, got {type(value).__name__}"
            )
        str_fields[key] = value
    nostdinc = fields.get("nostdinc", False)
    if not isinstance(nostdinc, bool):
        raise BundleFactsLibraryOverridesError(
            f"{where}.nostdinc: must be a boolean, got {type(nostdinc).__name__}"
        )
    sysroot = str_fields["sysroot"]
    if sysroot == "":
        # Codex review: an empty sysroot string is falsy, so `sysroot=...
        # if sysroot else None` below would silently swallow it -- but the
        # entry itself is not otherwise empty (the `sysroot` key is
        # present), so this library still gets its own per-library
        # CompileContext, which *replaces* the uniform one entirely
        # (bundle_side_input.py's own `(per_library_compile or {}).get(key,
        # compile)` fallback: present in the map at all means "use this
        # instead", not "use this where set"). An accidentally blank
        # `sysroot: ""` would therefore silently discard that library's
        # uniform --compiler/--compiler-option/etc, rather than being
        # rejected as the malformed input it is.
        raise BundleFactsLibraryOverridesError(f"{where}.sysroot: must not be an empty string")
    # Codex review: `--ast-frontend`'s own Click choice is case-insensitive
    # (`click.Choice(AST_FRONTENDS, case_sensitive=False)`) and the typed
    # API normalizes both fields via `.lower()` throughout (service_compare_
    # evidence.py et al.) -- this manifest's raw membership check rejected
    # an otherwise-valid CLI-equivalent spelling like `frontend: CLANG`.
    # Normalized before the check and stored as the canonical lowercase
    # value, matching that convention exactly.
    frontend = str_fields["frontend"]
    frontend_context = str_fields["frontend_context"]
    assert frontend is not None and frontend_context is not None  # defaulted above
    frontend = frontend.lower()
    frontend_context = frontend_context.lower()
    # Codex review: a typo'd enum value (e.g. "clnag") previously reached
    # CompileContext unchecked and only failed later, deep in extraction --
    # surfacing as dispatch()'s generic exit-1 ClickException instead of the
    # clean, immediate usage error every other malformed manifest field here
    # raises. HEADER_AST_FRONTENDS is api_types.py's own canonical set (a
    # `public_root_surfaces` entry, so any migrated package may import it),
    # the same one cli_options.AST_FRONTENDS / --ast-frontend validate
    # against; _SUPPORTED_FRONTEND_CONTEXTS is dump_manifest's identical set
    # for --dump-manifest's own frontend_context field. Reused, not
    # reimplemented, so this manifest's accepted values can't drift from the
    # CLI's.
    from ..api_types import HEADER_AST_FRONTENDS
    from ..dump_manifest import _SUPPORTED_FRONTEND_CONTEXTS

    if frontend not in HEADER_AST_FRONTENDS:
        raise BundleFactsLibraryOverridesError(
            f"{where}.frontend: {frontend!r} is not a recognized AST frontend "
            f"-- accepted values are {sorted(HEADER_AST_FRONTENDS)!r}"
        )
    if frontend_context not in _SUPPORTED_FRONTEND_CONTEXTS:
        raise BundleFactsLibraryOverridesError(
            f"{where}.frontend_context: {frontend_context!r} is not "
            f"supported -- accepted values are "
            f"{sorted(_SUPPORTED_FRONTEND_CONTEXTS)!r}"
        )
    return CompileContext(
        gcc_path=str_fields["gcc_path"],
        gcc_prefix=str_fields["gcc_prefix"],
        gcc_option_tokens=tuple(gcc_options),
        sysroot=_resolve_path(sysroot, base_dir=base_dir) if sysroot else None,
        nostdinc=nostdinc,
        frontend=frontend,
        frontend_context=frontend_context,
    )

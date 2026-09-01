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

from .compile_context import CompileContext

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
    return value


def parse_bundle_facts_library_overrides(
    raw: dict[str, object],
    *,
    known_libraries: set[str] | None = None,
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
        unknown = set(entry) - _LIBRARY_KEYS
        if unknown:
            raise BundleFactsLibraryOverridesError(
                f"per-library override manifest.{name}: unrecognized "
                f"key(s) {sorted(unknown)!r} -- known keys are "
                f"{sorted(_LIBRARY_KEYS)!r}"
            )
        if "headers" in entry:
            headers[name] = [
                Path(p)
                for p in _require_str_list(
                    entry["headers"],
                    where=f"per-library override manifest.{name}.headers",
                )
            ]
        if "includes" in entry:
            includes[name] = [
                Path(p)
                for p in _require_str_list(
                    entry["includes"],
                    where=f"per-library override manifest.{name}.includes",
                )
            ]
        compile_fields = {k: v for k, v in entry.items() if k in _COMPILE_KEYS}
        if compile_fields:
            compile_by_library[name] = _build_compile_context(
                compile_fields, where=f"per-library override manifest.{name}"
            )
    return BundleFactsLibraryOverrides(
        headers=headers, includes=includes, compile=compile_by_library
    )


def _build_compile_context(fields: dict[str, object], *, where: str) -> CompileContext:
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
    for key, default in (
        ("gcc_path", None),
        ("gcc_prefix", None),
        ("sysroot", None),
        ("frontend", "auto"),
        ("frontend_context", "host"),
    ):
        value = fields.get(key, default)
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
    frontend = str_fields["frontend"]
    frontend_context = str_fields["frontend_context"]
    assert frontend is not None and frontend_context is not None  # defaulted above
    return CompileContext(
        gcc_path=str_fields["gcc_path"],
        gcc_prefix=str_fields["gcc_prefix"],
        gcc_option_tokens=tuple(gcc_options),
        sysroot=Path(sysroot) if sysroot else None,
        nostdinc=nostdinc,
        frontend=frontend,
        frontend_context=frontend_context,
    )

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

"""ADR-050 D1 extraction-contract attachment, factored out of :mod:`dumper`.

Relocated out of ``dumper.py`` (PR #624 follow-up) to free line budget --
``dumper.py`` sits at the AI-readiness file-size hard cap, so any
net-positive addition there needs an equal-or-greater reduction elsewhere
first. Pure relocation: :func:`_attach_extraction_contract` is unchanged
except for its module home, re-exported from ``dumper.py`` so
``dumper._attach_extraction_contract`` (the bare name ``dumper.dump()``
calls, and the import ``service.py`` uses to reach the same path from its
PE/Mach-O header-scoped dump route) keeps working unchanged.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from ._compiler_options import language_standard_field
from .model import AbiSnapshot

if TYPE_CHECKING:
    from .comparability import IncludeDir
    from .dump_manifest import DumpManifest


def _profile_compiler_version(ast_toolchain: dict[str, str]) -> str | None:
    """Combine the AST frontend's own identity (``producer`` + its resolved
    ``version``) with the host compiler's ``compiler_version`` into one
    profile-fingerprint input (Codex review, PR #624 follow-up).

    Reading only ``compiler_version`` (falling back to bare ``version``)
    silently discards the frontend's own identity whenever a host
    ``compiler_version`` is also present — for a castxml-produced snapshot
    that is *always* the case, so two dumps made with different castxml
    binaries (or castxml vs. clang, when both happen to wrap the same host
    compiler/version) could share a ``profile_fingerprint`` despite castxml
    and clang's ``-ast-dump=json`` having materially different parsing
    capabilities (see ``dumper_clang.py``'s own docs on what it can't see).
    ``json.dumps``, not a raw string join (the same class of bug already
    fixed for ``macro_ops``/slot tokens elsewhere in this module): none of
    these three components is guaranteed separator-free.
    """
    producer = ast_toolchain.get("producer") or ""
    frontend_version = ast_toolchain.get("version") or ""
    host_version = ast_toolchain.get("compiler_version") or ""
    if not (producer or frontend_version or host_version):
        return None
    return json.dumps([producer, frontend_version, host_version])


def _manifest_declared_includes(dump_manifest: DumpManifest) -> list[IncludeDir]:
    """Union of every TU's own ``includes`` entries (ADR-050 D3), in
    first-declared order across TUs, deduplicated by resolved path.

    A ``project_owned`` entry's per-slot fingerprint token is the entry's
    own root-relative string (relative to the manifest file's own
    directory, ``dump_manifest.base_dir``) rather than a bare ``None``
    label: manifest paths are already root-relative/side-normalized by
    design, so — unlike the legacy CLI's labeled ``--include
    old:LABEL=PATH`` form — no separate user-supplied label is needed for
    two manifests describing the same logical support root under
    different checkout roots to already share the same stable,
    mount-point-independent per-slot token (see ``dump_manifest.py``'s own
    docstring). A non-``project_owned`` entry gets ``label=None``, leaving
    the ordinary ancestor rule (``_classify_include_dirs``) to decide
    ownership, unchanged.

    Uses :func:`os.path.relpath`, not ``Path.relative_to`` — the primary
    documented use case (``dump_manifest.py``'s own schema example,
    ``path: ../src``) resolves *outside* ``base_dir``, where
    ``relative_to`` raises ``ValueError``. ``relpath`` instead climbs back
    up (``"../src"``), preserving the mount-point-independent property for
    a sibling of the manifest's own directory: two checkouts at different
    absolute paths that each have the identical `<checkout>/../src`
    relative layout still produce the same token. Only a genuinely
    cross-drive path on Windows (``ValueError`` from ``relpath`` itself)
    falls back to the absolute resolved path, since no relative form
    exists there at all.
    """
    from .comparability import IncludeDir

    seen: set[Path] = set()
    result: list[IncludeDir] = []
    base = dump_manifest.base_dir.resolve()
    for tu in dump_manifest.translation_units:
        for entry in tu.includes:
            resolved = entry.path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            label = None
            if entry.project_owned:
                try:
                    label = os.path.relpath(resolved, base)
                except ValueError:
                    label = str(resolved)
            result.append(IncludeDir(path=entry.path, label=label))
    return result


def _attach_extraction_contract(
    snapshot: AbiSnapshot,
    *,
    headers: list[Path],
    extra_includes: list[Path] | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    lang: str | None,
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
    extra_include_labels: dict[Path, str] | None = None,
    dump_manifest: DumpManifest | None = None,
) -> None:
    """Populate *snapshot*.contract in place from this dump's resolved
    extraction inputs (profile/scope fingerprints), so a later
    ``compare()`` can prove two snapshots were extracted comparably
    (``comparability.check_contracts_comparable``, wired into
    ``checker.compare`` since PR #624). ``compute_extraction_contract``
    itself returns ``None`` when there is nothing to fingerprint at all
    (see its docstring).

    Called both by ``dumper.dump()`` and by any other snapshot-producing
    path that runs a header-AST frontend directly rather than through
    ``dump()`` -- e.g. ``service.py``'s PE/Mach-O header-scoped dump path
    (``_try_header_scoped_dump``), which calls
    ``dumper._dump_pe``/``dumper._dump_macho`` directly and would otherwise
    always leave ``contract=None`` on an otherwise-genuine header-based
    dump (Codex review, PR #624 follow-up). The ELF equivalent
    (``service._dump_elf``) already routes through ``dumper.dump()``
    itself, so it needs no separate call.

    ``extra_include_labels`` (ADR-050 D1, G32 Phase A): a resolved
    ``path -> label`` map from a labeled ``--include old:LABEL=PATH``/
    ``new:LABEL=PATH`` CLI entry (:class:`comparability.IncludeDir`'s
    ``label`` field), threaded here from both call sites above. A path with
    no entry gets ``label=None``, identical to before this parameter
    existed.

    ``dump_manifest`` (ADR-050 D3, G32 Phase B): when given, *declared_includes*
    is derived from the manifest's own per-TU ``includes`` (via
    :func:`_manifest_declared_includes`) instead of *extra_includes*/
    *extra_include_labels* — a manifest-driven dump's ``extra_includes`` is
    always empty (``dump()``'s own mutual-exclusivity check enforces that),
    so without this branch a manifest's ``project_owned`` markers would
    never reach ``profile_fingerprint`` at all.
    """
    from .comparability import IncludeDir, compute_extraction_contract
    from .dumper_toolchain import _compiler_family_from_toolchain
    from .header_conditionals import (
        ordered_macro_ops,
        pass_through_flags_from_tokens,
        resolve_pass_through_paths,
    )

    _flag_tokens = list(gcc_option_tokens)
    if gcc_options:
        try:
            _flag_tokens = (
                shlex.split(gcc_options, posix=os.name != "nt") + _flag_tokens
            )
        except ValueError:
            pass  # malformed --gcc-options must not abort the dump

    snapshot.contract = compute_extraction_contract(
        compiler_family=_compiler_family_from_toolchain(snapshot.ast_toolchain),
        compiler_version=_profile_compiler_version(snapshot.ast_toolchain),
        abi_dialect=snapshot.ast_toolchain.get("abi_dialect"),
        language_standard=language_standard_field(lang, gcc_options, gcc_option_tokens),
        macro_ops=ordered_macro_ops(_flag_tokens),
        pass_through_flags=resolve_pass_through_paths(
            pass_through_flags_from_tokens(_flag_tokens), extra_includes or []
        ),
        # Gate declared_headers/declared_includes on from_headers, NOT on
        # `headers` being non-empty: headers can be supplied yet fully
        # ignored (dwarf_only=True / symbols_only=True force a DWARF/
        # symbols path even with headers given — see _dump_elf). Feeding an
        # ignored `headers` list in would falsely assert a header-parsed
        # scope for a DWARF-derived surface.
        declared_headers=list(headers) if snapshot.from_headers else [],
        declared_includes=(
            []
            if not snapshot.from_headers
            else _manifest_declared_includes(dump_manifest)
            if dump_manifest is not None
            else (
                [
                    IncludeDir(path=p, label=(extra_include_labels or {}).get(p))
                    for p in extra_includes
                ]
                if extra_includes
                else []
            )
        ),
        l2_frontend_ran=snapshot.from_headers,
        public_header_paths=list(public_headers or []),
        public_header_dirs=list(public_header_dirs or []),
    )

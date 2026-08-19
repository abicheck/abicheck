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

"""PE/Mach-O header-scoped dump, relocated out of :mod:`service` to free line
budget -- ``service.py`` sits at the AI-readiness file-size hard cap, so any
net-positive addition there needs an equal-or-greater reduction elsewhere
first (the same "relocate a self-contained chunk first" playbook
``dumper_contract.py`` already used for ``dumper.py``, CLAUDE.md). Mostly a
relocation -- both functions keep their prior behavior for every existing
caller -- plus ADR-050 D1's own ``include_labels`` parameter/threading added
in the same commit, re-exported from ``service.py`` so
``service._try_header_scoped_dump`` / ``service._has_matched_public_surface``
(the bare names ``service._dump_pe``/``_dump_macho`` call, and what every
test monkeypatches via
``monkeypatch.setattr(service, "_try_header_scoped_dump", ...)``) keep
working unchanged.

``CompileContext``/``expand_header_inputs`` are imported locally inside
:func:`_try_header_scoped_dump` rather than at module level, mirroring that
function's own pre-existing local ``from .dumper import ...`` calls: they
live in :mod:`service_scan`, which ``service.py`` itself only imports at the
bottom of the file (after every function using them is already defined) to
sidestep an import-cycle risk -- a local import here reaches the same
already-loaded module without needing to reason about load order at all.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from . import deadline
from .errors import AstContextAmbiguousError, AstContextMissingError
from .header_utils import (
    cache_relevant_operand_paths,
    dedup_paths_preserve_order,
    deferred_token_dirs,
    resolve_inferred_header_roots,
)
from .model import AbiSnapshot, Visibility

if TYPE_CHECKING:
    from .service_scan import CompileContext


def _has_matched_public_surface(snap: AbiSnapshot) -> bool:
    """True if header parsing matched at least one exported symbol.

    ``dumper._dump_pe`` / ``dumper._dump_macho`` mark a declaration ``PUBLIC``
    only when its (mangled) name is present in the binary's export table.  When
    no declaration matches — e.g. an MSVC-mangled C++ DLL parsed with a
    Clang/GCC toolchain that emits Itanium names — every symbol collapses to
    ``HIDDEN`` and header scoping has had no effect.
    """
    return any(f.visibility == Visibility.PUBLIC for f in snap.functions) or any(
        v.visibility == Visibility.PUBLIC for v in snap.variables
    )


def _try_header_scoped_dump(
    fmt: str,
    path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    lang_explicit: bool = False,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    include_labels: dict[Path, str] | None = None,
) -> tuple[AbiSnapshot | None, str | None]:
    """Attempt a header-scoped dump for a PE/Mach-O binary.

    Returns ``(snapshot, None)`` when the selected header backend is available
    *and* at least one declared symbol matched the export table.  Returns
    ``(None, reason)`` (after emitting a ``UserWarning``) when scoping is
    unavailable or had no effect, so the caller can fall back to export-table
    mode and record the structured confidence signal (ADR-024 §D5.3).
    ``reason`` is one of ``"header-backend-unavailable"`` /
    ``"mangling-fallback"``.  This mirrors the public-API scoping that
    ``abidw --headers-dir`` / abi-dumper apply for ELF.

    ``lang_explicit`` (G31 Phase C follow-up): forces *lang* on this pass
    regardless of value when set, matching the sibling force decision
    ``service.run_dump``'s own ``_header_graph_lang`` computes for the same
    request — see :attr:`abicheck.api_types.DumpRequest.lang_explicit`.
    ``False`` (the default) is a no-op: identical to the pre-existing
    "force only bare ``'c'``" behavior.
    """
    from .dumper import _dump_macho as _dumper_macho, _dump_pe as _dumper_pe
    from .service_scan import CompileContext, expand_header_inputs

    # Expand header directories into individual files (same as the ELF path),
    # so `--header <dir>` scopes correctly instead of feeding a directory to
    # castxml's `#include`. Done *outside* the broad except below so a genuinely
    # bad/empty header path raises a clear ValidationError rather than silently
    # falling back to the full export table.
    resolved_headers = expand_header_inputs(headers)

    compiler = "cc" if lang.lower() == "c" else "c++"
    lang_arg = lang if (lang_explicit or lang.lower() == "c") else None
    cc = compile if compile is not None else CompileContext()
    # P3 parity with the ELF path: auto-add the inferred public-header roots so a
    # -H umbrella resolves its own relative includes without a separate -I on
    # PE/Mach-O too (else header parsing fails and we drop to export-table mode,
    # losing the L2/type surface). Same bucket selection — plain -I with no build
    # context, deferred -isystem otherwise — and the deferred dirs are hashed
    # into the AST cache key (Codex review).
    eff_includes = list(includes)
    eff_tokens = cc.gcc_option_tokens
    deferred_dirs: tuple[Path, ...] = ()
    if resolved_headers:
        inc_extra, deferred = resolve_inferred_header_roots(
            headers,
            list(includes),
            gcc_options=cc.gcc_options,
            gcc_option_tokens=cc.gcc_option_tokens,
        )
        # Deduped the same way the ELF `dump` path composes its own
        # `eff_includes + inc_extra` (AGENTS.md's L3->L2-fold "nineteenth
        # finding", candidate mechanism (b)): `includes` may already carry
        # an L3-seeded directory, and `resolve_inferred_header_roots`'s own,
        # separate lookup can independently resolve the same directory --
        # left undeduped, the extra `declared_includes` slot tokenizes into
        # an `include_sequence` a `scan --against` candidate (which folds
        # the seed and inferred roots in one pass) never produces for the
        # identical project, spuriously failing profile_fingerprint
        # comparability.
        eff_includes = dedup_paths_preserve_order(eff_includes + inc_extra)
        eff_tokens = cc.gcc_option_tokens + tuple(deferred)
        # Plus every include-search dir and forced pre-include the *caller's
        # own* context tokens name — the identical fold ``service._dump_elf``
        # and ``service._attach_header_graph`` already apply to their own
        # cache keys (AGENTS.md's tenth and seventeenth L3->L2-fold findings),
        # applied here so PE/Mach-O cannot disagree with ELF about staleness
        # for the same tokens (Codex review, PR D). ``cc`` is the *merged* L3
        # context on the ``dump`` path (``cli_dump_helpers.handle_non_elf_dump``
        # passes ``compile=l3_effective_ctx``), so a build-derived
        # ``-I``/``-include`` is covered without threading a separate
        # ``extra_hash_dirs`` channel down through ``run_dump``/``resolve_input``
        # — the caller's own derived-dirs return stays unused precisely because
        # the tokens it was derived from already arrive here.
        deferred_dirs = tuple(
            deferred_token_dirs(deferred)
        ) + cache_relevant_operand_paths(cc.gcc_option_tokens)
    try:
        if fmt == "pe":
            snap = _dumper_pe(
                path,
                resolved_headers,
                eff_includes,
                version,
                compiler,
                gcc_path=cc.gcc_path,
                gcc_prefix=cc.gcc_prefix,
                gcc_options=cc.gcc_options,
                gcc_option_tokens=eff_tokens,
                sysroot=cc.sysroot,
                nostdinc=cc.nostdinc,
                lang=lang_arg,
                header_backend=header_backend,
                extra_hash_dirs=deferred_dirs,
                frontend_context=cc.frontend_context,
            )
        else:
            snap = _dumper_macho(
                path,
                resolved_headers,
                eff_includes,
                version,
                compiler,
                gcc_path=cc.gcc_path,
                gcc_prefix=cc.gcc_prefix,
                gcc_options=cc.gcc_options,
                gcc_option_tokens=eff_tokens,
                sysroot=cc.sysroot,
                nostdinc=cc.nostdinc,
                lang=lang_arg,
                header_backend=header_backend,
                extra_hash_dirs=deferred_dirs,
                frontend_context=cc.frontend_context,
            )
    except deadline.DeadlineExceeded:
        # A --budget deadline expiring mid-parse is not "this header backend is
        # unavailable" — it's the scan's own budget guard firing. Falling back
        # to export-table mode here would silently mask the overflow (the scan
        # would report a degraded-but-"successful" result instead of the
        # dedicated budget-overflow exit code) and, worse, let the scan
        # continue doing more work after the point where it should have
        # aborted (Codex review). Propagate so run_scan_core's
        # except deadline.DeadlineExceeded -> _BudgetOverflow mapping applies,
        # same as the ELF L2 path.
        raise
    except (AstContextMissingError, AstContextAmbiguousError):
        # These only ever come from a NON-"host" --frontend-context request
        # (ADR-050 D5) -- there is no "device" default, so seeing either
        # here always means the user explicitly asked for a SYCL/DPC++ AST
        # context the configured compiler couldn't supply. Falling back to
        # export-table mode would silently drop --header/--include and
        # succeed anyway, exactly the "explicit request quietly ignored"
        # failure mode --frontend-context is supposed to fail loudly on
        # instead (Codex review).
        raise
    except Exception as exc:  # noqa: BLE001 — header backend/parse failure → fall back
        warnings.warn(
            f"Header-based ABI scoping unavailable for '{path.name}' "
            f"({fmt.upper()}): {exc}. Falling back to export-table mode — "
            f"--header/--include were ignored.",
            UserWarning,
            stacklevel=2,
        )
        return None, "header-backend-unavailable"

    # ADR-050 D1 (Codex review, PR #624 follow-up): this path calls
    # dumper._dump_pe/_dump_macho directly, bypassing dumper.dump() entirely
    # -- without this call, every PE/Mach-O header-scoped dump would leave
    # snap.contract=None regardless of whether headers were genuinely used,
    # unlike the ELF service path (_dump_elf above), which already routes
    # through dumper.dump() and gets this for free. public_headers/
    # public_header_dirs are the same outer provenance inputs run_dump
    # applies via _apply_native_provenance after this call returns (Codex
    # review, PR #624 follow-up) -- without threading them in here too, two
    # saved snapshots differing only in declared public-header provenance
    # could share the same scope_fingerprint.
    from .dumper import _attach_extraction_contract

    _attach_extraction_contract(
        snap,
        headers=resolved_headers,
        extra_includes=eff_includes,
        gcc_options=cc.gcc_options,
        gcc_option_tokens=eff_tokens,
        lang=lang_arg,
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
        extra_include_labels=include_labels,
    )

    if not _has_matched_public_surface(snap):
        warnings.warn(
            f"None of the provided headers matched exported symbols in "
            f"'{path.name}'. This commonly happens when a C++ {fmt.upper()} binary "
            f"uses a name-mangling scheme (e.g. MSVC) different from the compiler "
            f"used to parse the headers. Falling back to export-table mode — "
            f"header-based scoping had no effect.",
            UserWarning,
            stacklevel=2,
        )
        return None, "mangling-fallback"
    return snap, None

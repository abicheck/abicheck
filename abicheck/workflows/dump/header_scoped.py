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
in the same commit. The PE/Mach-O extractors reach it through
``workflows.dump.scoped``; tests patch or import it from this module
(``abicheck.workflows.dump.header_scoped``), not from ``service.py``.

``CompileContext``/``expand_header_inputs`` are imported locally inside
:func:`_try_header_scoped_dump` rather than at module level, mirroring that
function's own pre-existing local ``from .dumper import ...`` calls: they
live in :mod:`dry_run_estimate`, which ``service.py`` itself only imports at the
bottom of the file (after every function using them is already defined) to
sidestep an import-cycle risk -- a local import here reaches the same
already-loaded module without needing to reason about load order at all.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import TYPE_CHECKING

import abicheck.deadline as deadline

from ...errors import AstContextAmbiguousError, AstContextMissingError
from ...header_utils import (
    cache_relevant_operand_paths,
    dedup_paths_preserve_order,
    deferred_token_dirs,
    resolve_inferred_header_roots,
)
from ...model import AbiSnapshot

if TYPE_CHECKING:
    from ...dry_run_estimate import CompileContext


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

    Returns ``(snapshot, None)`` when the headers parsed and at least one
    declaration is bound to an export, exactly or by a normalized identity
    across mangling schemes (:mod:`abicheck.extract.header_export_binding`).
    Returns ``(snapshot, "export-binding-unresolved")`` when the headers
    parsed but no declaration could be bound, and ``(snapshot,
    "export-binding-partial")`` when some bound but exports the headers may
    declare stayed undecided (recorded as export-table entries). Either way
    the header surface is kept as type-level evidence, and the code is
    recorded on the snapshot.
    Returns ``(None, "header-backend-unavailable")`` (after a
    ``UserWarning``) only when the header parse itself failed, so the caller
    falls back to export-table mode and records that confidence signal
    (ADR-024 §D5.3). This mirrors the public-API scoping that
    ``abidw --headers-dir`` / abi-dumper apply for ELF.

    ``lang_explicit`` (G31 Phase C follow-up): forces *lang* on this pass
    regardless of value when set, matching the sibling force decision
    ``service.run_dump``'s own ``_header_graph_lang`` computes for the same
    request — see :attr:`abicheck.workflows.service_requests.DumpRequest.lang_explicit`.
    ``False`` (the default) is a no-op: identical to the pre-existing
    "force only bare ``'c'``" behavior.
    """
    from ...dry_run_estimate import CompileContext, expand_header_inputs
    from ...dumper import (
        _attach_extraction_contract,
        _dump_macho as _dumper_macho,
        _dump_pe as _dumper_pe,
    )
    from ...extract.header_order_witness import header_order_proof

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
        # context on the ``dump`` path (``cli_dump_non_elf.handle_non_elf_dump``
        # passes ``compile=l3_effective_ctx``), so a build-derived
        # ``-I``/``-include`` is covered without threading a separate
        # ``extra_hash_dirs`` channel down through ``run_dump``/``resolve_input``
        # — the caller's own derived-dirs return stays unused precisely because
        # the tokens it was derived from already arrive here.
        deferred_dirs = tuple(
            deferred_token_dirs(deferred)
        ) + cache_relevant_operand_paths(cc.gcc_option_tokens)

    def _dump_with_contract(headers: list[Path]) -> AbiSnapshot:
        """Run the native builder and attach its per-input contract.

        PE/Mach-O header-scoped extraction bypasses :func:`dumper.dump`, so
        the header-order witness must wrap this equivalent entry point. Each
        witness pass needs its own contract: the canonical proof is its own
        declared ``header_sequence``, not the original request's sequence.

        The parameter is named ``headers`` because the witness decorator binds
        its arguments by that name.
        """
        if fmt == "pe":
            snap = _dumper_pe(
                path,
                headers,
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
                headers,
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
        _attach_extraction_contract(
            snap,
            headers=headers,
            extra_includes=eff_includes,
            gcc_options=cc.gcc_options,
            gcc_option_tokens=eff_tokens,
            lang=lang_arg,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            extra_include_labels=include_labels,
        )
        return snap

    try:
        # Match dumper.dump()'s witness behavior for this direct PE/Mach-O
        # route. Without it, macOS never records header_canonical, so a safe
        # reordered header surface still fails the profile gate.
        snap = header_order_proof(_dump_with_contract)(resolved_headers)
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

    return snap, _bind_exports(fmt, path, snap)


def _bind_exports(fmt: str, path: Path, snap: AbiSnapshot) -> str | None:
    """Bind header declarations the exact export lookup missed, keeping the
    parsed header surface whatever the outcome.

    A header set that parsed is type-level evidence even when no
    declaration's spelling meets an export (an MSVC DLL against an Itanium
    parse, a template-only header): its types, enums, templates and
    concepts stay. Exports still unbound remain in the snapshot's own PE/
    Mach-O export table, which the platform export delta compares. Returns
    :data:`EXPORT_BINDING_UNRESOLVED` (recorded on the snapshot)
    when no declaration could be bound to any export, else ``None``.
    """
    from ...extract.header_export_binding import (
        EXPORT_BINDING_PARTIAL,
        EXPORT_BINDING_UNRESOLVED,
        bind_header_declarations_to_exports,
        binding_export_names,
        unbound_export_entries,
    )

    names, x86_32 = binding_export_names(snap)
    outcome = bind_header_declarations_to_exports(snap, names, x86_32=x86_32)
    undecided = unbound_export_entries(outcome, snap, x86_32=x86_32)
    if undecided:
        # Exports the parsed headers may declare stay on the surface as
        # export-table entries: read as undeclared, their removal would be
        # filtered out as a private export's.
        snap.declarations.functions = [*snap.declarations.functions, *undecided]
        snap._func_by_mangled = None
        snap._var_by_mangled = None
        snap._type_by_name = None
        warnings.warn(
            f"{len(undecided)} export(s) of '{path.name}' ({fmt.upper()}) could "
            "not be bound to one header declaration across name-mangling "
            "schemes (overloads, operators, template instantiations, or a "
            f"signature mismatch), e.g. {outcome.undecided[0]!r}; they are "
            "compared from the export table only.",
            UserWarning,
            stacklevel=3,
        )
    if not names:
        return None
    if outcome.any_declaration_bound:
        if not undecided:
            return None
        snap.scope_fallback = EXPORT_BINDING_PARTIAL
        return EXPORT_BINDING_PARTIAL
    warnings.warn(
        f"None of the provided headers' declarations could be bound to an "
        f"exported symbol of '{path.name}' ({fmt.upper()}): the headers declare "
        f"no exported entity, or name it under another mangling scheme. The "
        f"parsed header surface (types, templates, declarations) is kept; "
        f"{len(outcome.unbound_exports)} export(s) are compared from the export "
        f"table only.",
        UserWarning,
        stacklevel=3,
    )
    snap.scope_fallback = EXPORT_BINDING_UNRESOLVED
    return EXPORT_BINDING_UNRESOLVED

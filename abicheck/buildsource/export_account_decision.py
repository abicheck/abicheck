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

"""The one decision about what an export *is*, per export, per snapshot.

Two findings describe an export no parsed public declaration maps to:
``exported_not_public`` (a hygiene finding over one snapshot) and the
``*_added_elf_only``/``*_removed_elf_only`` existence findings (an OLD->NEW
fact). Each used to answer "what do the public headers say about this
export?" on its own, from different evidence -- the hygiene check read the
header *text* (``template <...>`` declarations, ``#ifdef`` regions, excluded
headers) and the existence detector only the parsed declarations. On oneCCL
the two disagreed about the same symbol: ``exported_not_public`` accounted
six ``create_communicators`` instantiations as instantiations of a public
template, while ``func_added_elf_only`` said each was "not declared in any
public header".

:func:`account_export` is that answer, extracted unchanged from the
``exported_not_public`` loop so both callers read one decision.
:func:`accounting_context` builds the per-snapshot inputs it needs once, and
reuses them inside :func:`accounting_scope` -- a comparison asks for the same
snapshot's context from the hygiene check and from the existence wording.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from ..model import AbiSnapshot, Function, ScopeOrigin, Variable
from ..model.cxx_artifact_symbols import is_cxx_class_artifact_symbol
from ..model.execution_cache import request_key
from ..model.execution_cache_scoped import ScopedCache
from .export_accounting import (
    _ALLOCATOR_INTERPOSER_MARKER,
    _ALLOCATOR_INTERPOSER_SYMBOLS,
    ACCOUNT_ALLOCATOR_INTERPOSER,
    ACCOUNT_CXX_ARTIFACT,
    ACCOUNT_EXTERNAL_DEP,
    ACCOUNT_OWN_TYPE_INSTANTIATION,
    ACCOUNT_PUBLIC,
    ACCOUNT_PUBLIC_TEMPLATE,
    _account_undocumented_export,
    _external_dependency_origin,
    _library_self_names,
    _linked_library_names,
)
from .export_declaration_evidence import (
    DeclarationHint,
    ExportDeclarationEvidence,
    build_export_declaration_evidence,
    instantiated_over_owned_types,
    public_template_for_export,
    textual_declaration_hint,
)

__all__ = [
    "ExportAccount",
    "ExportAccountingContext",
    "account_export",
    "accounting_context",
    "accounting_scope",
]

#: Mangling sigils: Itanium C++ (``_Z…``) and MSVC (``?…``). A non-extern-C
#: declaration whose ``mangled`` lacks one of these is a castxml fallback to the
#: display name (notably for constructors/destructors), not a comparable symbol.
_MANGLE_SIGILS = ("_Z", "?")


def _candidate_symbols(decl: Function | Variable) -> tuple[str, ...]:
    """Export symbols *decl* could provide, for matching against the export table.

    Keyed on whether ``mangled`` is a *real* mangling (``_Z…`` / ``?…``): a C++
    function or namespace/global variable exports under its mangled name only, so
    its bare source spelling must **not** be added (an unrelated accidental export
    sharing that spelling would otherwise look documented — Codex review). An
    un-mangled decl (C / ``extern "C"`` / C data, where the extractor left the
    bare name) exports under that bare name.
    """
    if decl.mangled.startswith(_MANGLE_SIGILS):
        return (decl.mangled,)
    return tuple({s for s in (decl.mangled, decl.name) if s})


@dataclass(frozen=True)
class ExportAccount:
    """What one export is, and the evidence that says so.

    *category* is one of ``export_accounting``'s ``ACCOUNT_*`` buckets.
    *origin_lib* names the dependency an ``ACCOUNT_EXTERNAL_DEP`` leak came
    from; *public_template* the public template an
    ``ACCOUNT_PUBLIC_TEMPLATE`` export instantiates; *hint* where the header
    text declares an export no parsed declaration covers; *decl* a
    non-public declaration the snapshot does carry for it.
    """

    category: str
    origin_lib: str | None = None
    public_template: str | None = None
    hint: DeclarationHint | None = None
    decl: Function | Variable | None = None

    @property
    def documented(self) -> bool:
        """Whether the public headers (or the C++ ABI) speak for the export."""
        return self.category in (
            ACCOUNT_PUBLIC,
            ACCOUNT_CXX_ARTIFACT,
            ACCOUNT_ALLOCATOR_INTERPOSER,
            ACCOUNT_PUBLIC_TEMPLATE,
        )


@dataclass(frozen=True)
class ExportAccountingContext:
    """The per-snapshot inputs :func:`account_export` reads."""

    public_syms: frozenset[str]
    decl_by_sym: Mapping[str, Function | Variable]
    needed_libs: tuple[str, ...]
    self_names: tuple[str, ...]
    interposer: bool
    evidence: ExportDeclarationEvidence


_CONTEXTS = ScopedCache("abicheck.buildsource.export_accounting_context")


@contextmanager
def accounting_scope() -> Iterator[None]:
    """Reuse each snapshot's :class:`ExportAccountingContext` inside the block.

    Outside a scope :func:`accounting_context` simply builds a fresh one, so
    behavior is identical either way; only the repeat cost (reading and
    scanning every public header again) differs.
    """
    with _CONTEXTS.scope():
        yield


def accounting_context(
    snapshot: AbiSnapshot, exported: frozenset[str]
) -> ExportAccountingContext:
    """Build (or, inside :func:`accounting_scope`, reuse) *snapshot*'s context.

    *exported* is the export-name set the caller accounts; only whether it
    carries the allocator-interposer marker depends on it, and that bit is
    part of the memo key.
    """
    interposer = _ALLOCATOR_INTERPOSER_MARKER in exported
    return _CONTEXTS.get_or_compute(
        request_key(name="accounting_context", subject=id(snapshot), extra=interposer),
        lambda: _build_context(snapshot, interposer),
        pin=snapshot,
    )


def _build_context(snapshot: AbiSnapshot, interposer: bool) -> ExportAccountingContext:
    public_syms: set[str] = set()
    decl_by_sym: dict[str, Function | Variable] = {}
    decls: list[Function | Variable] = [
        *snapshot.declarations.functions,
        *snapshot.declarations.variables,
    ]
    for d in decls:
        for sym in _candidate_symbols(d):
            decl_by_sym.setdefault(sym, d)
            if d.origin == ScopeOrigin.PUBLIC_HEADER:
                public_syms.add(sym)
    return ExportAccountingContext(
        public_syms=frozenset(public_syms),
        decl_by_sym=decl_by_sym,
        # The binary's linked-library list (ELF DT_NEEDED / Mach-O
        # LC_LOAD_DYLIB / PE imports) feeds the external-dependency origin
        # finders, so a leaked C++-runtime symbol names the runtime the binary
        # actually links (e.g. ``libc++.1.dylib`` on macOS rather than a
        # hard-coded ELF soname).
        needed_libs=tuple(_linked_library_names(snapshot)),
        # The audited library's own identity — a vendored namespace
        # (fmt/boost/…) that is the library *being scanned* is native, not a
        # leaked dependency.
        self_names=_library_self_names(snapshot),
        interposer=interposer,
        evidence=build_export_declaration_evidence(snapshot),
    )


def account_export(sym: str, ctx: ExportAccountingContext) -> ExportAccount:
    """Account *sym* with the precise reason it is (or is not) documented."""
    if sym in ctx.public_syms:
        return ExportAccount(ACCOUNT_PUBLIC)
    # A malloc-proxy library deliberately exports allocator replacements
    # (``malloc``/``operator new``/…); they are native + intentional, so they
    # are legitimate and never advised hidden (Codex).
    if ctx.interposer and sym in _ALLOCATOR_INTERPOSER_SYMBOLS:
        return ExportAccount(ACCOUNT_ALLOCATOR_INTERPOSER)
    decl = ctx.decl_by_sym.get(sym)
    # The external-dependency check runs *before* the C++ compiler-artifact
    # exemption: a leaked libstdc++/{fmt} vtable or typeinfo (``_ZTVNSt…``,
    # ``_ZTIN3fmt…``) is exactly the leaked surface being measured, and
    # exempting it as a class artifact would undercount it (Codex review).
    # A std/vendored template instantiated over the library's own types is
    # the library's own vague-linkage copy, never a statically linked dep.
    own_inst = instantiated_over_owned_types(sym, ctx.evidence.owned_namespaces)
    if not own_inst:
        origin = _external_dependency_origin(sym, list(ctx.needed_libs), ctx.self_names)
        if origin is not None:
            return ExportAccount(ACCOUNT_EXTERNAL_DEP, origin_lib=origin, decl=decl)
    if is_cxx_class_artifact_symbol(sym):
        return ExportAccount(ACCOUNT_CXX_ARTIFACT)
    # An instantiation of a publicly declared template is public API a
    # consumer links against; "hide it" would break that consumer.
    template = public_template_for_export(sym, ctx.evidence)
    if template is not None:
        return ExportAccount(ACCOUNT_PUBLIC_TEMPLATE, public_template=template)
    category = (
        ACCOUNT_OWN_TYPE_INSTANTIATION
        if own_inst
        else _account_undocumented_export(sym)
    )
    return ExportAccount(
        category, hint=textual_declaration_hint(sym, ctx.evidence), decl=decl
    )

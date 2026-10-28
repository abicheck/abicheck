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

"""Mach-O's leading-underscore decoration codec.

Darwin's linker prepends one ``_`` to every global symbol. For an Itanium
name the decorated form ``__Z...`` is unambiguous on its own (no real
Itanium mangling begins with two underscores), so :func:`decode_itanium`
needs no further evidence. For a C-linkage name ``_foo`` is
indistinguishable from a real, distinct ``asm("_foo")`` label, so
:func:`decode_c` is only correct for a caller that already knows the
declaration is ``extern "C"`` on a Darwin target.

**Known gap** (carried from the strip this module replaced): the
"unambiguous" claim holds for a *compiler-produced* name, not for an explicit
GNU ``asm("__Zfake")`` label, which clang reports verbatim. A caller joining
against a symbol table should try the exact spelling first
(``buildsource.template_graph._resolve_emitted_symbol`` does).
"""

from __future__ import annotations

__all__ = [
    "decode_c",
    "decode_itanium",
    "encode",
    "is_c_linkage_mangling",
    "is_darwin_target",
    "shifted_spellings",
    "strip_darwin_itanium_decoration",
]


def encode(name: str) -> str:
    """The Darwin linker spelling of *name*: one leading ``_``."""
    return "_" + name


def decode_itanium(symbol: str) -> str:
    """``__Z...`` -> ``_Z...``; every other spelling is returned unchanged
    (an undecorated Itanium name, a C name, anything else)."""
    return symbol[1:] if symbol.startswith("__Z") else symbol


def decode_c(symbol: str) -> str | None:
    """The C name a Darwin-decorated ``_name`` spells, or ``None`` when
    *symbol* carries no leading ``_``. Only valid for a known
    ``extern "C"`` declaration on a Darwin target (see the module
    docstring)."""
    return symbol[1:] if symbol.startswith("_") and len(symbol) > 1 else None


def shifted_spellings(spelling: str) -> tuple[str, ...]:
    """The two spellings one Mach-O decoration step away from *spelling*:
    one leading ``_`` fewer (its :func:`decode_c`) and one more (its
    :func:`encode`). A join that cannot tell which side the trie or the
    producer already stripped tries both, refusing any spelling another
    declaration owns exactly."""
    shorter = decode_c(spelling)
    return tuple(c for c in (shorter, encode(spelling)) if c)


#: OS-component names (lowercased) that mark a Darwin/Apple target --
#: checked against each ``-``-separated triple COMPONENT, not the raw
#: string, and via ``startswith`` rather than exact equality, since the OS
#: component routinely carries a trailing version suffix (``"macosx13.0"``,
#: ``"ios15.0"``, ``"darwin20.6.0"``). Deliberately independent of the
#: VENDOR component ("apple") -- see :func:`is_darwin_target`'s own
#: docstring for the confirmed, real triple this distinction matters for.
_DARWIN_OS_NAMES = ("darwin", "macos", "ios", "tvos", "watchos")


def is_darwin_target(target_triple: str | None) -> bool:
    """Whether *target_triple* names a Darwin (macOS/iOS/...) target.

    Used to gate a leading-underscore de-prefixing decision that changes
    an *identity* determination (extern-"C" recognition), not merely an
    export-table membership test the way :func:`symbol_candidates`'s own
    unconditional tolerant match does (Codex review, sixteenth round,
    fresh evidence): on a NON-Darwin target, ``raw_mangled ==
    "_" + name`` is not a linker-decoration artifact at all -- it is
    exactly what a real, explicit ``asm("_foo")`` label or a genuinely
    underscore-prefixed real mangled name looks like, and reinterpreting
    that as C linkage would discard a real, distinct identity (castxml
    still carries a real ``("mangled", "_foo")`` for the same
    declaration, so treating it as ``("extern_c",)`` on clang's side
    would newly DISAGREE with castxml instead of agreeing with it). Only
    Darwin's linker actually prepends this underscore as pure platform
    decoration with no identity content of its own, so only there is
    stripping it safe.

    **Checks the triple's OS component, not merely an ``"apple"`` vendor
    (Codex review, nineteenth round, fresh evidence).** A target such as
    ``"x86_64-unknown-darwin"`` is a valid triple clang accepts and mangles
    exactly like a real Mach-O target (Darwin's OS-level linker behavior,
    not anything vendor-specific), but its vendor component is
    ``"unknown"``, not ``"apple"`` -- an ``"apple" in triple`` substring
    test missed it, so the de-prefix fallback was wrongly skipped and this
    normalizer fell back to a real, non-Darwin mangled identity for a
    genuinely plain-C declaration castxml still recognized as extern "C".
    Splitting on ``"-"`` and checking each COMPONENT (rather than a bare
    substring test over the whole triple) also avoids a false match from
    an unrelated component that merely happens to CONTAIN one of these
    tokens.

    A bare ``None``/empty *target_triple* means "no evidence" and always
    answers ``False`` here -- never guess Darwin from the absence of
    evidence (a direct unit-test construction of the parser with no
    ``target_triple`` at all relies on exactly this). A real probe
    failure inside the actual dump pipeline (``dumper._run_clang``) is a
    DIFFERENT situation this function cannot see from a bare ``None``
    alone -- that caller synthesizes its own ``sys.platform``-based
    fallback *string* (still just an ordinary triple-shaped value from
    this function's point of view) rather than asking this function to
    special-case ``None`` (Codex review, macOS CI, fresh evidence: an
    earlier revision special-cased ``None`` here, which also silently
    changed this function's answer for every OTHER caller including the
    direct-construction unit tests above).
    """
    if not target_triple:
        return False
    components = target_triple.lower().split("-")
    return "apple" in components or any(
        component.startswith(_DARWIN_OS_NAMES) for component in components
    )


def strip_darwin_itanium_decoration(
    raw_mangled: str | None,
    mangled: str,
    target_triple: str | None,
    *,
    name: str = "",
    is_extern_c: bool = False,
    has_asm_label: bool = False,
) -> str:
    """Strip a still-present Darwin linker-decoration underscore, for
    either a real Itanium mangled name (``"__Z..."`` -> ``"_Z..."``) or a
    plain C-linkage bare name (``"_foo"`` -> ``"foo"``).

    Darwin decorates every global symbol with one leading underscore; for
    a real Itanium name (always starting ``"_Z"``) that produces
    ``"__Z..."``. clang's ``mangledName``/``self._target_triple`` reports
    that decorated linker symbol, while castxml's own ``mangled`` is
    always the pure spelling -- stripped here so the model's own
    ``mangled`` field matches on every platform (shared by
    ``extract.headers.clang.functions.parse_functions`` and
    ``dumper_clang.parse_variables`` so a bare ``--ast-frontend clang``
    dump on Darwin gets it too, not only a castxml+clang hybrid merge --
    macOS CI review, fresh evidence).

    The Itanium case is gated on the doubly-underscore-decorated shape
    (``"__Z..."``) specifically, not "any leading underscore": that shape
    is unambiguous ON DARWIN (a real Itanium name always starts with a
    single ``"_Z"``), whereas a bare, single-underscore-prefixed name
    (``"_foo"``) is generally indistinguishable from a real, explicit
    ``asm("_foo")`` label naming a distinct identity. **Both cases still
    require ``is_darwin_target(target_triple)`` first** -- a literal
    ``"__ZN..."``-shaped ``mangledName`` is unusual but syntactically legal
    on a genuinely NON-Darwin target too (an explicit ``asm("__Zfoo")``
    label, however contrived), and unconditionally treating that shape as
    decoration regardless of platform previously broke exactly that,
    documented case (see ``tests/test_dumper_clang_extern_c_identity.py``'s
    ``test_parse_functions_mangled_field_unaffected_off_darwin``: an
    earlier, ungated revision of this strip stripped a literal
    off-Darwin ``"__Z..."`` name too). Delegates to :func:`~abicheck.model.
    name_decoration.macho.decode_itanium` for the shape test itself,
    the single canonical home for that structural check (also used by
    ``model.mangled_name``'s own Itanium-scope parsing and
    ``dumper_hybrid.macho_normalize_mangled``) -- but only calls it once
    ``is_darwin_target`` has already confirmed the platform.

    Both gates lean on the SAME ``is_darwin_target`` check for their
    platform confirmation, so a *target_triple* that never reaches this
    point at all (the external ``-print-target-triple`` compiler probe
    behind it failed) means neither case strips anything -- this is why
    ``dumper._run_clang``'s own probe-failure path synthesizes a real,
    ``sys.platform``-based fallback *triple string* to pass in here rather
    than leaving it bare ``None`` (see that call site's own comment):
    without it, a probe failure on a real Darwin host reproduces this
    exact class of bug even though the underlying evidence (a real
    compiled Mach-O binary) never changed.

    The plain-C-linkage case (macOS CI, fresh evidence: a real Darwin
    ``extern "C"`` declaration's decorated ``mangledName`` -- ``"_c_func"``
    for source-level ``c_func`` -- was left unstripped, disagreeing with
    castxml's undecorated spelling and the real Mach-O export table alike,
    for the identical reason the Itanium case above was fixed) needs an
    extra, narrower gate on top: *is_extern_c*, the caller's own already-
    computed boolean (``entry.extern_c``, ``raw_mangled == name``, or the
    existing Darwin/no-scope bare-name fallback -- see either caller's own
    docstring). Reusing that exact boolean, rather than re-deriving a
    separate condition here, means this only strips when the caller has
    ALREADY concluded -- from a real, explicit ``extern "C"`` AST node or
    an already-vetted heuristic -- that this declaration's true identity
    IS its bare name; it does not widen what counts as evidence. Still
    requires *mangled* to be EXACTLY ``"_" + name`` (not merely
    underscore-prefixed) so a real, distinct asm-label spelling that
    happens to differ from the bare name is never touched.

    *has_asm_label* (:func:`has_explicit_asm_label`) closes the residual
    gap the shape-only Itanium gate above still had ON Darwin: a literal
    ``asm("__Zfake")`` label is exactly as ``"__Z..."``-shaped as a real,
    compiler-generated decorated mangling, so the two are indistinguishable
    from the string alone once a genuine Darwin target is confirmed
    (Codex review, fresh evidence, empirically verified against a real
    Clang 18 install: both castxml's and clang's own actual Darwin-target
    AST report the literal label verbatim, undecorated). When set, this
    skips BOTH stripping branches unconditionally -- an explicit label is
    never linker decoration, in either shape.
    """
    if raw_mangled is None or not is_darwin_target(target_triple):
        return mangled
    if has_asm_label:
        return mangled
    stripped = decode_itanium(mangled)
    if stripped != mangled:
        return stripped
    if is_extern_c and name and mangled == "_" + name:
        return name
    return mangled


def is_c_linkage_mangling(
    raw_mangled: str | None,
    name: str,
    target_triple: str | None,
    *,
    unscoped: bool = True,
    has_asm_label: bool = False,
    is_cxx: bool = False,
) -> bool:
    """Whether clang's own ``mangledName`` *raw_mangled* spells the bare C
    name *name*, i.e. the declaration has C linkage (plain C, or ``extern
    "C"``).

    This is the one C-linkage test the header-AST backend
    (``extract.headers.clang.functions``) and the L4 source extractor
    (``buildsource.source_extractors.clang_nodes``) both apply, so the two
    agree on every Darwin spelling. ``raw_mangled == name`` is the
    platform-independent signal. On Darwin the linker prepends one ``_`` to
    every global symbol, so ``raw_mangled == "_" + name`` is C linkage too --
    but only where that underscore is pure decoration:

    * the target is confirmed Darwin (:func:`is_darwin_target` never guesses
      from a bare ``None``/empty triple);
    * the declaration is not namespace-scoped (*unscoped*); and
    * it carries no explicit ``asm("...")`` label in C++ mode (*has_asm_label*
      and *is_cxx*): a C++ asm label is a distinct identity, while a plain-C
      one is routine (glibc/POSIX symbol versioning) and is not a reason to
      stop reading the declaration as C.

    Off Darwin, ``"_" + name`` is a real, distinct spelling (an explicit asm
    label, or an underscore-prefixed name) and is never reinterpreted as C
    linkage.
    """
    if raw_mangled is None:
        return False
    if raw_mangled == name:
        return True
    return (
        unscoped
        and bool(raw_mangled)
        and raw_mangled.startswith("_")
        and raw_mangled[1:] == name
        and is_darwin_target(target_triple)
        and not (has_asm_label and is_cxx)
    )

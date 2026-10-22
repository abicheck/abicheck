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
"""One parsed compile invocation per translation unit (parent plan P1).

:func:`parse_compile_invocation` reads a :class:`~..build_evidence.CompileUnit`
once and returns a :class:`CompilerInvocation`: the emulated driver, the
forced language, the standard, the replay directory, and the include, define
and toolchain facts, with every relative path already rebased onto the real
effective directory. It also carries the replayed flags (ABI-relevant flags
and include-search options recovered from ``argv``), each tagged with the rule
that decides whether the structured argv already suppresses it.

Renderers consume this object and never rescan ``argv``: the clang AST-dump and
macro argv in :mod:`.clang_command`, and the public-root include probe in
:mod:`.clang_public_roots`. Backend-specific rendering (clang-cl driver mode,
castxml emulation) stays with its backend.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from ...header_utils import match_gnu_forced_include, match_msvc_forced_include
from ..abi_flags import is_split_operand_abi_flag_survivor, split_operand_survivor
from ..build_evidence import CompileUnit
from ._argv import (
    CXX_LANGS,
    STRUCTURED_TOOLCHAIN_FLAG_PREFIXES,
    effective_directory,
    is_msvc_mode,
    pick_compiler_binary,
)
from ._argv_shortopts import rebase_structured_path

#: GNU include-search options that take a directory operand and are NOT
#: normalized into the structured ``include_paths``/``system_include_paths``
#: buckets (those cover ``-I``/``-isystem`` only). Dropping them makes the
#: extractor search a different set of directories than the real compile (Codex
#: review #335). Both the separate (``-iquote dir``) and joined (``-iquote/dir``)
#: spellings are carried through.
#: (https://gcc.gnu.org/onlinedocs/gcc/Directory-Options.html)
_GNU_INCLUDE_SEARCH_OPTS = frozenset({"-iquote", "-idirafter"})


@dataclass(frozen=True)
class CarriedFlag:
    """One replayed token and the rule that decides whether structured argv suppresses it.

    ``deduplicated`` is True for a token recovered from ``abi_relevant_flags``
    (ABI flags and forced includes): it is dropped when the structured argv
    already carries the identical token, so a flag is never emitted twice
    under a different route. It is False for a token scanned from the raw
    ``argv`` include-search options, which is never deduplicated. The two
    rules differ on purpose; collapsing them would change the replayed argv.
    """

    token: str
    deduplicated: bool


@dataclass(frozen=True)
class CompilerInvocation:
    """The replay-relevant facts of one compile unit, parsed once.

    Every path here is already resolved against :attr:`run_directory` (the
    effective directory after any leading ``env -C`` prefix), so a renderer
    needs no second look at the compile unit's directory or ``argv``.
    """

    #: The translation unit's source operand, as the compile unit records it.
    source: str
    #: The compiler binary this invocation emulates (see ``pick_compiler_binary``).
    compiler: str
    #: CL-mode driver (``cl``/``clang-cl``): flags use ``/`` spellings.
    msvc: bool
    #: Language forced with ``-x`` for a GNU-style driver: ``"c"`` or ``"c++"``.
    language: str
    #: The effective ``-std`` value, or ``""`` when the unit records none.
    standard: str
    #: The directory relative operands resolve against, or ``None`` if unknown.
    run_directory: str | None
    #: ``-D`` / ``/D`` definitions in argv order; a bare define has an empty value.
    defines: tuple[tuple[str, str], ...]
    #: ``-U`` / ``/U`` undefines in argv order.
    undefines: tuple[str, ...]
    #: ``-I`` (``/I``) search directories, rebased onto :attr:`run_directory`.
    include_dirs: tuple[str, ...]
    #: ``-isystem`` directories, rebased onto :attr:`run_directory`.
    system_include_dirs: tuple[str, ...]
    sysroot: str
    target_triple: str
    #: Replayed flags in the order they were recovered: ABI-relevant flags first,
    #: then include-search options scanned from ``argv``.
    carried: tuple[CarriedFlag, ...]


def _carry_abi_relevant_flags(
    abi_relevant_flags: list[str], cc_id: str
) -> list[CarriedFlag]:
    """Recover the replayable tokens from ``abi_relevant_flags``.

    Skips a structured toolchain option (sysroot, target, isysroot): those are
    already emitted from the structured fields, and the split spelling would
    dangle if re-appended. A ``-target-abi``/``-target-cpu``/``-target-feature``/
    ``-target-linker-version`` survivor is the internal encoding of a split cc1
    flag, not argv syntax, so it is decoded first; the bare spelling would
    otherwise share the ``-target`` prefix filter and be dropped.

    Every occurrence is kept, including a repeat within ``abi_relevant_flags``:
    a layered build config can record the same toggle-style flag more than once
    (``-fno-sycl -fsycl -fno-sycl``), and the last occurrence decides the real
    compiler's state. Only a token already present in the structured argv is
    suppressed, which is what :attr:`CarriedFlag.deduplicated` records.
    """
    out: list[CarriedFlag] = []
    i = 0
    while i < len(abi_relevant_flags):
        flag = abi_relevant_flags[i]
        forced: list[str] = []
        new_i, _option, operand = match_gnu_forced_include(
            flag, abi_relevant_flags, i, forced
        )
        if new_i == i and cc_id == "msvc":
            new_i, _option, operand = match_msvc_forced_include(
                flag, abi_relevant_flags, i, forced
            )
        if new_i != i:
            # Build packs are untrusted evidence. A forced-include operand
            # that is a response file would be expanded by the new compiler
            # process L4 starts, so discard the complete option/operand pair.
            if operand and not operand.startswith(("-", "@")):
                out.extend(CarriedFlag(c, deduplicated=True) for c in forced)
            i = new_i
            continue
        if is_split_operand_abi_flag_survivor(flag):
            out.extend(
                CarriedFlag(t, deduplicated=False) for t in split_operand_survivor(flag)
            )
            i += 1
            continue
        if flag.startswith(STRUCTURED_TOOLCHAIN_FLAG_PREFIXES):
            i += 1
            continue
        # A bare response-file token is also compiler syntax, even when it
        # is not paired with a recognized forced-include option.
        if not flag.startswith("@"):
            out.append(CarriedFlag(flag, deduplicated=True))
        i += 1
    return out


def _match_gnu_include_search(tok: str, argv: list[str], i: int, out: list[str]) -> int:
    """Try to match a GNU include-search token (``-iquote``/``-idirafter``).

    Returns the new ``i`` after consumption, or the original ``i`` if no match.
    Both the separate (``-iquote dir``) and joined (``-iquotedir``) spellings are
    handled.
    """
    if tok in _GNU_INCLUDE_SEARCH_OPTS and i + 1 < len(argv):
        out += [tok, argv[i + 1]]  # -iquote / -idirafter <dir> (separate)
        return i + 2
    if tok not in _GNU_INCLUDE_SEARCH_OPTS and any(
        tok.startswith(opt) and len(tok) > len(opt) for opt in _GNU_INCLUDE_SEARCH_OPTS
    ):
        out.append(tok)  # -iquotedir / -idirafter/dir (joined)
        return i + 1
    return i


def _match_msvc_include_search(
    tok: str, argv: list[str], i: int, out: list[str]
) -> int:
    """Try to match an MSVC ``/I`` include-search token (MSVC mode only).

    Returns the new ``i`` after consumption, or the original ``i`` if no match.
    Both the separate (``/I dir``) and joined (``/Idir``) spellings are handled.
    """
    if tok == "/I" and i + 1 < len(argv):
        out += [tok, argv[i + 1]]  # MSVC /I dir (separate operand)
        return i + 2
    if len(tok) > 2 and tok.startswith("/I"):
        out.append(tok)  # MSVC /Idir (joined)
        return i + 1
    return i


def _scan_argv_for_extra_flags(argv: list[str], cc_id: str) -> list[str]:
    """Return the forced-include and include-search tokens recorded in ``argv``.

    Handles GNU and (when ``cc_id == "msvc"``) MSVC option families. Each option
    family is tried in priority order; the first match consumes the token(s) and
    advances the index.
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        # The matchers return (new_i, option, operand); only the index and the
        # tokens they append matter here, since replay hands them straight back
        # to the real compiler.
        forced: list[str] = []
        new_i, _opt, operand = match_gnu_forced_include(tok, argv, i, forced)
        if new_i != i and operand and not operand.startswith(("-", "@")):
            out.extend(forced)
        if new_i == i:
            new_i = _match_gnu_include_search(tok, argv, i, out)
        if new_i == i and cc_id == "msvc":
            new_i = _match_msvc_include_search(tok, argv, i, out)
        if new_i == i and cc_id == "msvc":
            forced = []
            new_i, _opt, operand = match_msvc_forced_include(tok, argv, i, forced)
            if new_i != i and operand and not operand.startswith(("-", "@")):
                out.extend(forced)
        i = new_i if new_i != i else i + 1
    return out


def replayed_tokens(
    invocation: CompilerInvocation, structured: Collection[str] = ()
) -> list[str]:
    """The replayed tokens that the structured argv in *structured* does not already carry.

    *structured* is the argv a renderer has emitted so far (the driver mode,
    language, standard, defines, include and toolchain options). A token marked
    :attr:`CarriedFlag.deduplicated` is dropped when it appears there; the rest
    are kept in order.
    """
    seen = set(structured)
    return [
        carried.token
        for carried in invocation.carried
        if not (carried.deduplicated and carried.token in seen)
    ]


def replayed_tokens_for(
    compile_unit: CompileUnit, compiler_binary: str | None = None
) -> list[str]:
    """The replayed tokens of *compile_unit*, with no structured argv to deduplicate against."""
    return replayed_tokens(parse_compile_invocation(compile_unit, compiler_binary))


def parse_compile_invocation(
    compile_unit: CompileUnit, compiler_binary: str | None = None
) -> CompilerInvocation:
    """Parse *compile_unit* into a :class:`CompilerInvocation`, once.

    *compiler_binary* is the explicit driver override; without it the binary is
    the one the build recorded, as ``pick_compiler_binary`` resolves it.

    The structured include directories are rebased from the compile unit's own
    recorded ``directory`` onto the effective directory. A leading ``env -C DIR``
    in ``argv`` makes the real compiler run from ``<directory>/DIR``, but the
    adapter resolved each structured path against ``directory`` before that
    prefix was considered, so a literal prefix substitution (see
    :func:`~._argv_shortopts.rebase_structured_path`) moves it onto the real one.
    A path outside ``directory`` (an absolute operand recorded verbatim) has no
    matching prefix and is left unchanged.
    """
    compiler = pick_compiler_binary(compile_unit, compiler_binary)
    msvc = is_msvc_mode(compiler)
    cc_id = "msvc" if msvc else "gnu"
    run_directory = effective_directory(compile_unit.argv, compile_unit.directory)

    include_explicit = compile_unit.explicit_or_unknown(system=False)
    system_include_explicit = compile_unit.explicit_or_unknown(system=True)
    include_dirs = tuple(
        rebase_structured_path(
            inc,
            compile_unit.directory,
            run_directory,
            explicit_absolute=is_explicit,
        )
        for inc, is_explicit in zip(compile_unit.include_paths, include_explicit)
    )
    system_include_dirs = tuple(
        rebase_structured_path(
            inc,
            compile_unit.directory,
            run_directory,
            explicit_absolute=is_explicit,
        )
        for inc, is_explicit in zip(
            compile_unit.system_include_paths, system_include_explicit
        )
    )
    carried = (
        *_carry_abi_relevant_flags(compile_unit.abi_relevant_flags, cc_id),
        *(
            CarriedFlag(token, deduplicated=False)
            for token in _scan_argv_for_extra_flags(compile_unit.argv, cc_id)
        ),
    )
    return CompilerInvocation(
        source=compile_unit.source,
        compiler=compiler,
        msvc=msvc,
        language="c++" if compile_unit.language.lower() in CXX_LANGS else "c",
        standard=compile_unit.standard,
        run_directory=run_directory,
        defines=tuple(compile_unit.defines.items()),
        undefines=tuple(compile_unit.undefines),
        include_dirs=include_dirs,
        system_include_dirs=system_include_dirs,
        sysroot=compile_unit.sysroot or "",
        target_triple=compile_unit.target_triple or "",
        carried=carried,
    )

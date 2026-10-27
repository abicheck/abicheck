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
    is_msvc_mode,
)
from ._argv_shortopts import (
    fold_chdir_into_operands as _fold_chdir_into_operands,
    is_absolute_path_token,
    join_path_token,
    normalize_path_token,
    rebase_structured_path,
    resolve_bare_token_with_default_path,
)
from .compile_action import CompileAction, SourceOperand, parse_gnu_operands
from .launcher_env import (
    _traverse_env_and_launcher_prefix,
    effective_directory,
    env_path_cleared_for_bare_token,
)
from .launcher_env_path import _apply_env_context

#: GNU include-search options that take a directory operand and are NOT
#: normalized into the structured ``include_paths``/``system_include_paths``
#: buckets (those cover ``-I``/``-isystem`` only). Dropping them makes the
#: extractor search a different set of directories than the real compile (Codex
#: review #335). Both the separate (``-iquote dir``) and joined (``-iquote/dir``)
#: spellings are carried through.
#: (https://gcc.gnu.org/onlinedocs/gcc/Directory-Options.html)
_GNU_INCLUDE_SEARCH_OPTS = frozenset({"-iquote", "-idirafter"})

#: MSVC/clang-cl include-search options, recognized only in CL-mode driver
#: context. ``/I`` and ``/external:I`` take a directory in the separate
#: (``/I dir``) or joined (``/Idir``) spelling. ``-I`` is the same search
#: option in the GNU spelling, which ``cl`` and ``clang-cl`` also accept.
#: ``/imsvc`` (a system bucket) is deliberately not listed here.
_MSVC_INCLUDE_SEARCH_OPTS = ("/I", "-I", "/external:I")


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
    #: Include-search operands the structured buckets do not hold. GNU mode:
    #: ``-iquote``/``-idirafter`` (both spellings), read from :attr:`carried`. MSVC
    #: mode: ``/I``, ``-I`` and ``/external:I`` (separate and joined spellings),
    #: read from the raw argv, so a ``-I`` or ``/external:I`` operand reaches the
    #: probe even though it is not replayed into the rendered argv (the carried
    #: set stays as it was; see :func:`_probe_operand_tokens`). Kept in argv
    #: order and in the operand's recorded spelling, not rebased, so a probe
    #: resolves them against the compile unit's ``directory`` exactly as it does
    #: :attr:`~..build_evidence.CompileUnit.include_paths`.
    replayed_include_dirs: tuple[str, ...] = ()
    #: What the driver does with its sources (``-c``/``-S``/``-E``/link), or
    #: ``None`` when the operand parse does not cover this driver (CL mode).
    action: CompileAction | None = None
    #: One entry per source the driver compiles, in argv order, each with its
    #: own language, action and output. Empty when :attr:`action` is ``None``.
    operands: tuple[SourceOperand, ...] = ()
    #: Non-source inputs (objects, archives, libraries) a link consumes, in argv
    #: order. Empty unless :attr:`action` is :attr:`CompileAction.LINK`.
    link_inputs: tuple[str, ...] = ()


def strip_launchers(argv: list[str], *, directory: str | None = None) -> list[str]:
    """Drop leading ``env``/compiler-launcher tokens (``ccache``/``sccache``/…).

    Also skips any ``KEY=VALUE`` per-invocation config-override tokens ccache
    accepts immediately after its own name (``ccache compiler_check=content
    gcc -c foo.c``) — otherwise the override token, not the real compiler, is
    left as the new ``argv[0]``.

    A leading POSIX ``env`` invocation (bare ``env``, or a path ending in
    ``/env``) is unwrapped first, along with any ``env``-specific flags and
    ``NAME=VALUE`` environment assignments that follow it (see
    :func:`_skip_env_prefix`) — and a compiler-cache/distribution launcher
    may itself follow ``env`` (``env FOO=1 sccache clang-cl ...``), or an
    ``env`` invocation may appear between launchers, so the two prefix kinds
    are unwrapped in a loop until neither can strip anything further, rather
    than each running only once.

    An ``env -C DIR``/``env PATH=...`` prefix's effect on how the resulting
    driver token must be interpreted -- not merely which token it is -- is
    folded into that token before it is returned (P2 review round 19, both
    findings; see :func:`_apply_env_context`'s own docstring for the full
    reasoning): a relative driver path is joined onto the *effective* chdir
    directory (the most recent ``-C``/``--chdir`` seen, closest to the
    driver, if more than one ``env`` prefix chains), and a bare driver name
    is resolved to an absolute path via the most recent env-supplied
    ``PATH`` when found there. Every existing caller of this function
    receives the corrected token automatically, with no signature change.

    Operates on a local **copy** of *argv* (Codex review, ``_argv.py:102``,
    fresh evidence): expanding an ``env -S``/``--split-string`` prefix (see
    :func:`_skip_env_prefix`) mutates its argv list in place, splicing the
    split tokens into it. Several callers pass ``compile_unit.argv`` -- a
    persisted-evidence field -- straight through without copying it first
    (:func:`pick_compiler_binary` here; ``adapters.base.
    _executable_token_positions``/``_msvc_driver_scan``), so mutating the
    caller's own list in place would silently corrupt that stored evidence
    for every later reader of the same ``CompileUnit``. Copying once here
    means every one of :func:`_skip_env_prefix`'s in-place splices are
    confined to this call's own local list.

    *directory*: the compile unit's own directory, when the caller has one
    (Codex review, ``_argv.py:576``, Finding 3, fresh evidence) -- used
    only to resolve a *relative* ``env``-supplied ``PATH`` entry (``env -C
    build PATH=../tool clang-cl ...``) against the directory GNU ``env``
    actually executes the command from, composed with any ``-C``/
    ``--chdir`` value seen (see :func:`_resolve_env_path_entries`).
    ``None`` (the default) for every caller with no compile-unit context to
    supply -- unchanged from the pre-Finding-3 behavior for those callers.

    **Chained ``env -C`` prefixes COMPOSE (Codex review, Finding 7, fresh
    evidence).** ``env -C a env -C b driver ...`` -- one ``env`` invocation
    wrapping another, each with its own ``-C`` -- really does chdir into
    ``a`` and then, from there, into ``b``: the effective directory is
    ``a/b``, not bare ``b`` alone. The previous revision simply overwrote
    ``chdir`` with the most recently seen ``-C`` value every iteration,
    silently discarding the outer ``-C`` entirely. A later, genuinely
    ABSOLUTE ``-C`` still fully replaces whatever was accumulated so far
    (an absolute chdir is a literal directory regardless of any earlier
    relative one) -- only two RELATIVE values compose.

    **The resolved ``chdir`` is also folded into every OTHER operand in the
    result, not merely the driver token (Codex review, Finding 8, fresh
    evidence).** GNU ``env -C DIR`` chdirs the whole invoked process before
    it execs the real command -- every relative filesystem argument the
    command receives (a source file operand, an ``-I``/``-isystem``-family
    include path, ...) is interpreted relative to that same chdir'd
    directory by the real OS, not merely the executable name. See
    :func:`_fold_chdir_into_operands`'s own docstring for exactly which
    tokens are (and, to avoid corrupting an unrelated flag value, are NOT)
    corrected.
    """
    argv = list(argv)
    i, chdir, env_path, _path_cleared = _traverse_env_and_launcher_prefix(argv)
    result = argv[i:]
    if not result or (chdir is None and env_path is None):
        return result
    resolved = _apply_env_context(result[0], chdir, env_path, directory)
    tail = _fold_chdir_into_operands(result[1:], chdir)
    if resolved == result[0] and tail == result[1:]:
        return result
    return [resolved, *tail]


def pick_compiler_binary(compile_unit: CompileUnit, override: str | None) -> str:
    """Pick the compiler binary an extractor should EMULATE for this TU.

    Prefers the compiler actually recorded in the build action (``argv[0]``,
    after unwrapping any launcher) so a clang/clang-cl/cross TU is replayed
    against its real builtin include paths, target defaults, and accepted flags.
    Falls back to g++/gcc by language when no command is available; an explicit
    ``override`` always wins.

    **This is emulation identity, not invocation identity — do not use it to
    gate whether a flag is safe to append to the command an extractor is
    about to run.** Without an explicit ``override``, this falls back to the
    real build's own recorded ``argv[0]`` (e.g. ``icpx``) purely so flag
    *shape* (MSVC ``/D``/``/I`` vs. GNU ``-D``/``-I``, language-standard
    spelling, …) matches what the real build used — it says nothing about
    which binary a given extractor is genuinely going to shell out to. A
    caller in `castxml.py` passes this value as castxml's own
    ``--castxml-cc-<id> <path>`` *argument* (castxml itself is invoked, and
    is explicitly designed to accept an arbitrary compiler identity to
    emulate), so appending a flag there based on this result is safe by
    construction. A caller in `clang.py`/`clang_public_roots.py` that
    instead builds an argv for **this same binary to execute directly**
    must gate any binary-capability-specific flag (e.g. an Intel-only
    ``-fsycl-host-only``) on the extractor's own actually-invoked binary
    (its own ``clang_bin``, distinct from this function's return value)
    instead — conflating the two let `-fsycl-host-only` get appended to a
    genuinely stock ``clang`` invocation whenever a SYCL TU's real build
    happened to use an Intel driver, which stock clang hard-rejects as
    "unknown argument" (Codex review, `source_extractors/clang.py`'s
    `_clang_context_args`). See that function's own docstring for the fix
    and `tests/test_source_extractors_clang.py`'s
    ``TestSyclHostOnlyGatedOnInvokedBinary`` for the regression matrix any
    future binary-capability-gated flag addition here should extend.

    **Never trusts a bare driver name PATH could not have resolved (round
    27 Finding 1, Codex review, fresh evidence).** ``strip_launchers``
    correctly leaves a bare token (e.g. ``"cc"`` from ``env -i cc ...``)
    unresolved rather than guessing -- but returning it here unchanged
    would let a downstream ``shutil.which``-style lookup (``dumper_clang.
    resolve_clang_bin``, or the eventual subprocess spawn itself) resolve
    it against ABICHECK'S OWN inherited ``PATH``, which the real recorded
    command -- genuinely PATH-less under ``env -i`` -- could never have
    done. See :func:`env_path_cleared_for_bare_token`'s own docstring for
    the full reasoning. Degrading to the same language-based ``g++``/``gcc``
    fallback used for a missing/flag-only command is the correct response:
    a name this provably unresolvable carries no more real toolchain
    identity than no recorded command at all -- UNLESS
    :func:`resolve_bare_token_with_default_path` (round 29) resolves it.
    """
    if override:
        return override
    argv = strip_launchers(compile_unit.argv, directory=compile_unit.directory)
    if argv and argv[0] and not argv[0].startswith("-"):
        if env_path_cleared_for_bare_token(compile_unit.argv, argv[0]):
            resolved = resolve_bare_token_with_default_path(argv[0])
            if resolved:
                return resolved
        else:
            return _resolve_relative_driver(argv[0], compile_unit.directory)
    return "g++" if compile_unit.language.lower() in CXX_LANGS else "gcc"


def _resolve_relative_driver(token: str, directory: str) -> str:
    """Compose *token* onto *directory* when it is still relative (round 26
    Finding 1, Codex review, fresh evidence).

    :func:`strip_launchers`'s ``env -C``/chdir handling
    (:func:`_apply_env_context`) deliberately returns a still-*relative*
    driver token for a relative path-shaped token -- its own docstring
    states this explicitly: "the result is still a relative path (DIR is
    not itself resolved against anything here), so the existing downstream
    join against ``cu.directory`` in ``header_compile_context.
    _resolve_driver_token`` composes correctly". That downstream join is
    real for the one caller that goes through
    ``header_compile_context._derived_gcc_path``, but :func:`pick_compiler_binary`
    -- the OTHER caller with a full ``CompileUnit`` (and therefore
    ``directory``) in scope -- returned ``strip_launchers``'s token
    completely unjoined, silently skipping that composition.

    Concretely wrong for a compile unit recorded in directory ``/work``
    with ``argv == ["env", "-C", "build", "../tool/clang++", ...]``: GNU
    ``env -C build`` chdirs to ``/work/build`` first, and THEN resolves the
    now-relative ``../tool/clang++`` from there -- landing back at
    ``/work/tool/clang++``. ``_apply_env_context`` already folds the ``-C``
    value into the token (``join_path_token("build", "../tool/clang++")``
    == ``"tool/clang++"``), but that result is still relative to the
    compile unit's OWN ``directory`` (``/work``), not to whatever cwd a
    caller happens to run a subprocess from -- returning it unjoined let
    ``CastxmlSourceExtractor`` (which runs from the effective ``-C`` cwd,
    ``/work/build``) resolve it a second, wrong time, landing on
    ``/work/build/tool/clang++`` instead.

    Mirrors ``header_compile_context._resolve_driver_token``'s own
    resolution (same host-independent absolute check/join grammar via
    :func:`is_absolute_path_token`/:func:`join_path_token`, both already
    defined in this module) rather than importing that private helper back
    from ``header_compile_context`` -- which imports ``adapters.base``,
    which in turn imports from this module at module scope, so a top-level
    import in the other direction here would be a real import cycle; only
    a function-local import would work, and duplicating the (already
    tiny, already-local) composition logic here avoids that indirection
    entirely. A bare token (no path separator -- a plain ``PATH``-searched
    name like ``clang-cl``) and an already-absolute token are both left
    unchanged, exactly as ``_resolve_driver_token`` treats them.
    """
    if "/" not in token and "\\" not in token:
        return token
    if is_absolute_path_token(token):
        return normalize_path_token(token)
    if not directory:
        return token
    return join_path_token(directory, token)


def _replayed_include_operands(tokens: list[str], msvc: bool) -> tuple[str, ...]:
    """The include-search operands in *tokens* that the structured buckets do not hold.

    Matches each spelling once, in token order: a separate operand consumes the
    next token, a joined one carries its directory inline. ``/I``, ``-I`` and
    ``/external:I`` are include options only in MSVC mode; in GNU mode ``-I`` is
    already structured.
    """
    out: list[str] = []
    msvc_options = _MSVC_INCLUDE_SEARCH_OPTS if msvc else ()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        raw: str | None = None
        if tok in _GNU_INCLUDE_SEARCH_OPTS or tok in msvc_options:
            if i + 1 < len(tokens):
                raw = tokens[i + 1]
                i += 2
            else:
                i += 1
        else:
            joined = next(
                (
                    opt
                    for opt in (*_GNU_INCLUDE_SEARCH_OPTS, *msvc_options)
                    if tok.startswith(opt) and len(tok) > len(opt)
                ),
                None,
            )
            if joined is not None:
                raw = tok[len(joined) :]
            i += 1
        if raw:
            out.append(raw)
    return tuple(out)


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


def _is_external_include_option(tok: str) -> bool:
    """True when *tok* is ``/external:I`` or ``-external:I``, in either spelling.

    MSVC option names are case-insensitive, so ``/EXTERNAL:i`` matches too. A
    joined form carries its directory after the option name; the bare option
    name and ``/external:I`` followed by a longer suffix are the only matches.
    Other ``/external:`` options (``W<n>``, ``anglebrackets``, ``env:``) are not.
    """
    head = tok.lower()
    return head.startswith(("/external:i", "-external:i"))


def _is_msvc_include_operand(argv: list[str], j: int) -> bool:
    """Whether ``argv[j]`` can be a separate include-search operand.

    A following option (``-x``, ``@file``, ``/FI``, ``/I`` or another
    ``/external:I``) is never one: consuming it as a directory would drop that
    option, and its own operand, from replay. A POSIX absolute directory such
    as ``/usr/include`` still is.
    """
    if j >= len(argv):
        return False
    nxt = argv[j]
    return not (
        not nxt
        or nxt.startswith(("-", "@"))
        or nxt.upper().startswith("/FI")
        or nxt.startswith("/I")
        or _is_external_include_option(nxt)
    )


def _match_msvc_include_search(
    tok: str, argv: list[str], i: int, out: list[str]
) -> int:
    """Try to match an MSVC include-search token (MSVC mode only).

    Handles ``/I``, ``/Idir`` and the ``/external:I`` / ``-external:I`` family,
    each in its separate (``/I dir``) or joined (``/Idir``) spelling. A
    separate spelling whose operand is missing or is another option is dropped
    alone, so the following option is still scanned. Returns the new ``i``
    after consumption, or the original ``i`` if no match.
    """
    if tok == "/I":
        if not _is_msvc_include_operand(argv, i + 1):
            return i + 1
        out += [tok, argv[i + 1]]  # MSVC /I dir (separate operand)
        return i + 2
    if len(tok) > 2 and tok.startswith("/I"):
        out.append(tok)  # MSVC /Idir (joined)
        return i + 1
    if _is_external_include_option(tok):
        # Carried verbatim: the replay driver is clang in ``--driver-mode=cl``,
        # which accepts ``/external:I`` itself, so no translation to ``-I`` or
        # ``-isystem`` is needed.
        option_length = len("/external:I")
        if len(tok) == option_length:
            if not _is_msvc_include_operand(argv, i + 1):
                return i + 1
            out += [tok, argv[i + 1]]  # /external:I dir (separate operand)
            return i + 2
        if len(tok) > option_length:
            out.append(tok)  # /external:Idir (joined)
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


def _source_operands(
    compile_unit: CompileUnit, msvc: bool
) -> tuple[CompileAction | None, tuple[SourceOperand, ...], tuple[str, ...]]:
    """The per-source operands of *compile_unit*, or nothing for the CL driver.

    The launcher-stripped argv's tail (the driver token removed) is parsed once
    by :func:`~.compile_action.parse_gnu_operands`. CL-mode operands are a
    separate slice, so an MSVC driver reports no action rather than a guess.
    """
    if msvc:
        return None, (), ()
    argv = strip_launchers(compile_unit.argv, directory=compile_unit.directory)
    parsed = parse_gnu_operands(argv[1:])
    return parsed.action, parsed.operands, parsed.link_inputs


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
    action, operands, link_inputs = _source_operands(compile_unit, msvc)
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
        replayed_include_dirs=_replayed_include_operands(
            _probe_operand_tokens(compile_unit, carried, msvc), msvc
        ),
        action=action,
        operands=operands,
        link_inputs=link_inputs,
    )


def _probe_operand_tokens(
    compile_unit: CompileUnit, carried: tuple[CarriedFlag, ...], msvc: bool
) -> list[str]:
    """The tokens :func:`_replayed_include_operands` reads for the public-root probe.

    GNU mode reads the carried tokens, as it always has. MSVC mode reads the raw
    ``argv``: the carried set holds only ``/I``, so ``-I`` and ``/external:I``
    would otherwise never reach the probe. A rendered replay token is never
    affected by this choice -- :attr:`CompilerInvocation.carried` is unchanged.
    """
    if msvc:
        return list(compile_unit.argv)
    return [carried_flag.token for carried_flag in carried]

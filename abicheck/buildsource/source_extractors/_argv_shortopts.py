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

"""Round 29/30 Codex/CodeRabbit review helpers for ``env``-prefix and
compiler-argv parsing, split into their own leaf module purely to keep
``_argv.py`` (every helper's one caller) under this repo's 2000-line
AI-readiness hard cap. Pure/stdlib-only -- no other dependency, so none of
these carry any of ``_argv.py``'s own documented import-cycle risk with
``header_compile_context.py``.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
from pathlib import PurePosixPath, PureWindowsPath

#: Single-CHARACTER short flags that may be GROUPED into one clustered
#: token (GNU clustering only ever combines no-operand flags -- excludes
#: every operand-taking short flag: ``-C``/``-u``/``-S``).
CLUSTERABLE_NO_OPERAND_SHORT_FLAGS = frozenset({"i", "v", "0"})


def is_short_flag_cluster(arg: str) -> bool:
    """True when *arg* is a GNU-clustered no-operand short-flag token,
    e.g. ``-iv`` == ``-i -v``."""
    return (
        len(arg) > 1
        and arg[0] == "-"
        and not arg.startswith("--")
        and all(c in CLUSTERABLE_NO_OPERAND_SHORT_FLAGS for c in arg[1:])
    )


def _windows_candidate_names(token: str) -> list[str]:
    """Every basename ``token`` could resolve to on Windows, honoring
    ``PATHEXT`` -- mirrors :func:`shutil.which`'s own extension-appending
    logic for the (deliberately narrower, single-directory) search this
    module does itself below."""
    if os.name != "nt":
        return [token]
    pathext = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    exts = [e for e in pathext.split(os.pathsep) if e]
    _, existing_ext = os.path.splitext(token)
    if existing_ext and existing_ext.lower() in {e.lower() for e in exts}:
        return [token]
    return [token + ext for ext in exts]


def resolve_bare_token_with_default_path(token: str) -> str | None:
    """Resolve against :data:`os.defpath` only, never inherited ``PATH``
    (a real PATH-less ``execvp`` still searches it) -- and, on Windows,
    never the process's own current working directory either (CodeRabbit
    review round 30, fresh evidence).

    ``shutil.which(token, path=...)`` is deliberately NOT used here: on
    Windows, ``os.defpath`` itself starts with ``.`` (the documented
    default is ``".;C:\\bin"``), and even after stripping that,
    ``shutil.which``'s own implementation (confirmed across CPython
    3.10-3.13 source, and the underlying ``NeedCurrentDirectoryForExePathW``
    Windows API behavior it mirrors) can still silently prepend the
    process's CURRENT WORKING DIRECTORY to the search regardless of an
    explicit ``path=`` argument. A real, PATH-less ``env -i``/``env -u
    PATH`` launch does NOT search the cwd either -- only ``os.defpath``'s
    own real directories -- so relying on ``shutil.which`` here could
    resolve a bare driver token against whatever abicheck's own analysis
    process happens to be running from, an unrelated, unintended
    directory. Filters ``.`` and empty components out of ``os.defpath``
    and checks each remaining directory directly (bypassing
    ``shutil.which`` entirely) instead.
    """
    for directory in os.defpath.split(os.pathsep):
        if not directory or directory == ".":
            continue
        for name in _windows_candidate_names(token):
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


#: Source/header suffixes that identify a genuine trailing POSITIONAL
#: compile-unit operand for :func:`fold_chdir_into_operands` (round 30
#: Finding 4, Codex review, fresh
#: evidence: ``env -C build clang -target armv7-none-eabi -meabi gnu -c
#: x.c`` folded ``gnu`` -- the bare, non-path VALUE of an unrecognized
#: separate-form flag, ``-meabi <value>`` -- into ``build/gnu``, because
#: that function previously treated ANY non-flag-shaped bare token as a
#: foldable positional path, not just a genuine source-file operand).
#: Enumerating every non-path-taking flag one at a time
#: (this module's own ``_CHDIR_FOLD_NON_PATH_VALUE_FLAGS``) is the wrong
#: shape of fix for this class of bug -- it only ever protects the
#: *specific* flags someone has already found and reported, and there are
#: dozens of real compiler flags that take a non-path value this module
#: has no way to enumerate in advance. This set flips the DEFAULT instead:
#: a bare, non-flag-shaped token is folded as a path only when it is
#: independently recognizable as a genuine source/header operand (the
#: actual case ``fold_chdir_into_operands``'s own motivating example
#: needs -- ``env -C build clang-cl -I../include /c ../src/x.cpp``); every
#: other bare token -- an unrecognized flag's own non-path value included,
#: no matter which flag -- is left exactly as the pre-existing,
#: conservative "unrecognized, don't touch it" fallback that module
#: already uses for everything else. Mirrors
#: ``source_replay._SOURCE_FILE_SUFFIXES`` (kept as an independent
#: literal, not a shared import: ``source_replay.py`` already imports
#: FROM ``_argv.py``, which imports from this module, so a reverse import
#: would be a cycle) -- keep the two lists in sync if either grows.
_SOURCE_FILE_OPERAND_SUFFIXES = (
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".c++",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".h++",
    ".inl",
    ".ipp",
    ".tcc",
    ".cu",
    ".cuh",
    ".m",
    ".mm",
)


def looks_like_source_file_operand(token: str) -> bool:
    """True when *token* -- a bare, non-flag-shaped argv token -- is
    recognizable as a genuine source/header positional operand by its
    suffix, the general replacement for treating every such token as a
    foldable path by default (round 30 Finding 4; see
    :data:`_SOURCE_FILE_OPERAND_SUFFIXES`'s own docstring)."""
    return token.replace("\\", "/").lower().endswith(_SOURCE_FILE_OPERAND_SUFFIXES)


def rebase_structured_path(
    path: str,
    old_base: str,
    new_base: str | None,
    *,
    explicit_absolute: bool = False,
) -> str:
    """Rebase an already-absolute structured ``CompileUnit`` path field from
    *old_base* (the compile unit's raw, un-chdir'd ``directory``) onto
    *new_base* (the real effective directory once a leading ``env -C DIR``
    prefix is accounted for).

    Shared by both header-AST backends' compile-context argv builders --
    ``clang.py``'s ``_clang_context_args()`` and ``castxml.py``'s
    ``build_castxml_command()`` (round 31 CodeRabbit Finding C, "the
    CastXML-backend sibling of round 30 Finding 2/round 31 Finding 1" --
    the two backends' compile-context builders replay the identical
    structured ``include_paths``/``system_include_paths`` fields and must
    rebase them identically, not just the Clang one) -- so it lives in this
    shared leaf module rather than being duplicated or kept private to one
    backend.

    A literal string-prefix substitution, not a lexical path join: *path*
    was itself already constructed by an adapter joining *old_base* onto a
    relative operand, so this exactly undoes that one join and re-applies
    it against *new_base*, without needing to re-derive or guess which
    join grammar (POSIX/Windows) the adapter originally used. Returns
    *path* unchanged whenever there is nothing to rebase: *new_base* is
    falsy/identical to *old_base* (no ``env -C`` prefix present), *old_base*
    itself is falsy (nothing to anchor the prefix match against), *path*
    does not actually start with *old_base* (an absolute path the real
    build recorded verbatim, never relative to the compile unit's own
    directory in the first place -- e.g. a system/sysroot include dir), or
    *explicit_absolute* is ``True``.

    *explicit_absolute* (round 30 Finding 2, Codex review, fresh evidence):
    a plain string-prefix match cannot tell an EXPLICITLY absolute operand
    (``-I/work/include``, recorded verbatim, never relative to
    ``compile_unit.directory`` in the first place -- must NOT move just
    because ``env -C`` changed the effective directory, since an absolute
    path is absolute regardless of cwd) apart from a RELATIVE operand
    (``-Iinclude``) an adapter already resolved to an absolute path by
    joining it onto *old_base* -- both look identical here: an absolute
    string that happens to start with *old_base*. The caller resolves this
    ambiguity via ``CompileUnit.explicit_or_unknown()`` (backed by the
    position-aligned ``include_paths_explicit``/
    ``system_include_paths_explicit``, captured at the one point in the
    pipeline -- before that join -- where the real answer is still known)
    and passes it through as this flag.
    """
    if explicit_absolute:
        return path
    if not new_base or new_base == old_base or not old_base:
        return path
    if path == old_base:
        return new_base
    if path.startswith(old_base + "/") or path.startswith(old_base + "\\"):
        return new_base + path[len(old_base) :]
    return path


#: Preprocessor macro define/undef option prefixes. Their *values* reach the
#: compiler verbatim (argv, no shell expansion), so a literal ``~`` in e.g.
#: ``-DDEFAULT_DIR=~/app`` must NOT be home-expanded during replay — unlike the
#: path operands (includes/sysroot/source), which carry redacted home prefixes.
MACRO_DEFINITION_PREFIXES = ("-D", "-U", "/D", "/U")


def is_absolute_path_token(token: str) -> bool:
    """Host-*independent* absolute-path check (Codex review, "Recognize
    foreign absolute driver paths", fresh evidence).

    L3 build evidence is not always collected on the same OS abicheck
    itself runs on -- a Windows compile database inspected from a Linux CI
    runner, or the reverse. ``os.path.isabs``/``pathlib.Path.is_absolute``
    only recognize the HOST's own grammar: a Windows-shaped absolute token
    (``C:\\LLVM\\bin\\clang-cl.exe``, or a UNC ``\\\\server\\share\\...``)
    reads as *relative* on a POSIX host (POSIX ``Path`` only recognizes a
    leading ``/`` as absolute), and the symmetric POSIX-shaped token
    (``/opt/llvm/bin/clang-cl``) reads as relative on a Windows host
    (``PureWindowsPath`` requires a drive letter or UNC root). Both
    grammars are checked here regardless of host, so a caller does not
    silently join an already-absolute *foreign* token onto a directory a
    second time -- corrupting it, and letting two otherwise-identical
    compile units acquire different driver signatures purely from that
    corruption (see :func:`~abicheck.buildsource.header_compile_context.
    _resolve_driver_token`, the original site of this finding, for the
    full ambiguity-grouping consequence).
    """
    return PureWindowsPath(token).is_absolute() or PurePosixPath(token).is_absolute()


def normalize_path_token(token: str) -> str:
    """Normalize *token* using ITS OWN grammar, not the host's.

    A Windows-shaped absolute token is normalized with ``ntpath`` (keeping
    its backslash convention); a POSIX-shaped absolute token is normalized
    with ``posixpath`` (keeping its forward-slash convention) -- using the
    host-native ``os.path.normpath`` for either would silently rewrite the
    *other* OS's separator convention (e.g. collapsing a genuine POSIX
    absolute path's forward slashes into backslashes when abicheck itself
    happens to run on Windows). Falls back to ``os.path.normpath`` only for
    a token matching neither absolute grammar (a bare relative token with
    no path separator reaching here at all would be a caller bug, since
    every caller already special-cases the separator-free case first).
    """
    if PureWindowsPath(token).is_absolute():
        return ntpath.normpath(token)
    if PurePosixPath(token).is_absolute():
        return posixpath.normpath(token)
    return os.path.normpath(token)


def _join_grammar(base: str, token: str) -> str:
    """Pick ``"nt"``/``"posix"``/``"host"`` -- the grammar :func:`join_path_token`
    should compose *base* and *token* with (Codex review, "Choose join
    grammar from an unambiguous base, not the relative token alone", fresh
    evidence).

    An earlier revision chose the join grammar from *token*'s own separator
    style alone. That is wrong whenever *base* is itself unambiguously
    absolute in a DIFFERENT grammar than *token* happens to be spelled in --
    e.g. a Windows compile-unit ``directory`` (``C:\\work\\build``) composed
    with a POSIX-spelled relative ``env``-supplied ``PATH=`` entry
    (``../tool``): the old code picked ``posixpath`` purely because the
    entry has no backslash, and ``posixpath.join`` then treats the entire
    Windows base as one opaque path component (it has no concept of ``\\``
    separators or drive letters), producing a corrupted result (``tool``
    instead of ``C:\\work\\tool``).

    *base* determines which OS's join/separator rules actually apply at
    resolution time -- not whichever style happens to spell the (possibly
    differently-sourced) token -- so *base*'s grammar wins whenever it is
    unambiguous (absolute in exactly one of the two grammars). Falls back to
    *token*'s own single-separator-style grammar (the pre-existing behavior)
    when *base* is not unambiguously one grammar or the other -- relative,
    empty, or mixing both separator styles itself.
    """
    base_win = PureWindowsPath(base).is_absolute()
    base_posix = PurePosixPath(base).is_absolute()
    if base_win and not base_posix:
        return "nt"
    if base_posix and not base_win:
        return "posix"
    if "\\" in token and "/" not in token:
        return "nt"
    if "/" in token and "\\" not in token:
        return "posix"
    return "host"


def join_path_token(base: str, token: str) -> str:
    """Join *token* onto *base*, then normalize -- choosing a SINGLE,
    consistent grammar for both operands rather than deriving it from
    *token* alone (see :func:`_join_grammar`).

    Relative *token*s recorded by a build on one OS (``../llvm/bin/
    clang-cl``, POSIX-style) must compose the same way regardless of which
    OS abicheck itself runs on -- joining/normalizing with host-native
    ``os.path``/``os.path.normpath`` on Windows would rewrite the result's
    separators to backslash even though *base* (an ``env -C`` chdir value,
    or a compile unit's own ``directory``) may itself carry no separator at
    all (e.g. a bare ``"build"``), giving a spurious cross-OS mismatch
    between two otherwise textually-equivalent joins.

    Once a grammar is chosen, BOTH operands are composed in it -- ``ntpath``
    natively accepts ``/`` as an alternate separator, so an ``nt``-grammar
    join needs no rewriting; a ``posix``-grammar join first rewrites any
    ``\\`` in either operand to ``/`` (``posixpath`` has no separator
    concept for ``\\`` at all, so leaving one un-rewritten would otherwise
    collapse a whole backslash-separated operand into one opaque component,
    the same corruption this function exists to prevent, just from the
    other direction). Neither operand being unambiguous in one grammar (both
    relative, or *base* itself mixing separator styles) falls back to
    host-native ``os.path.join``/``os.path.normpath``, the pre-existing
    behavior for that case.
    """
    grammar = _join_grammar(base, token)
    if grammar == "nt":
        return ntpath.normpath(ntpath.join(base, token))
    if grammar == "posix":
        return posixpath.normpath(
            posixpath.join(base.replace("\\", "/"), token.replace("\\", "/"))
        )
    return os.path.normpath(os.path.join(base, token))


#: COMBINED-form (single-token, ``-Ivalue``/``/Ivalue``) path-bearing option
#: prefixes recognized by :func:`fold_chdir_into_operands` (Codex review,
#: Finding 8, "propagate the effective cwd to whatever resolves trailing
#: path operands too, not just the compiler token", fresh evidence). Every
#: one of these genuinely supports the combined spelling with no space, per
#: GCC/Clang/MSVC's own documented option grammar. ``-include`` is
#: deliberately excluded -- GCC never accepts a combined ``-includeFILE``
#: spelling, only the separate ``-include FILE`` form (see
#: :data:`_CHDIR_FOLD_SEPARATE_PATH_FLAGS` below for that).
_CHDIR_FOLD_COMBINED_PATH_PREFIXES = (
    "-I",
    "-iquote",
    "-idirafter",
    "-isystem",
    "-isysroot",
    "-cxx-isystem",
    "/I",
    "/FI",
    "/imsvc",
    "/external:I",
)
#: ``=``-joined (``--sysroot=value``) path-bearing option prefixes recognized
#: by :func:`fold_chdir_into_operands`, alongside the combined-form set above.
_CHDIR_FOLD_EQUALS_PATH_PREFIXES = ("--sysroot=",)
#: SEPARATE-form (``-I value``, two tokens) path-bearing option spellings
#: recognized by :func:`fold_chdir_into_operands` -- the bare flag itself
#: (never a prefix match), whose immediately-following token is its path
#: VALUE and must be resolved regardless of whether that value happens to
#: contain a path separator (Codex review, Finding 12, fresh evidence -- see
#: that function's own docstring for why "does the token contain a
#: separator" was the wrong test to begin with). Includes ``-include``
#: (GCC's forced-include flag, separate-form only -- deliberately excluded
#: from the combined set above) and bare ``--sysroot`` (the equals-joined
#: spelling is handled by :data:`_CHDIR_FOLD_EQUALS_PATH_PREFIXES` instead).
_CHDIR_FOLD_SEPARATE_PATH_FLAGS = frozenset(
    {
        "-I",
        "-iquote",
        "-idirafter",
        "-isystem",
        "-isysroot",
        "-include",
        "-cxx-isystem",
        "--sysroot",
        "/I",
        "/FI",
        "/imsvc",
        "/external:I",
    }
)
#: SEPARATE-form value-taking flags whose value is confirmed NOT a path --
#: ``-x`` (language name), ``-target`` (bare driver-level triple), ``-arch``
#: (architecture name), ``-Xclang`` (forwards an arbitrary cc1 value
#: verbatim) (Codex review round 25, Finding 2). Superseded as the primary
#: defense by round 30 Finding 4's general fix below
#: (:func:`looks_like_source_file_operand` flips the default so an
#: unrecognized flag's value is safe without being named here) -- kept as
#: belt-and-suspenders for these four flags.
_CHDIR_FOLD_NON_PATH_VALUE_FLAGS = frozenset({"-x", "-target", "-arch", "-Xclang"})


def _fold_chdir_path_value(value: str, chdir: str) -> str:
    """Resolve a single path-bearing VALUE against *chdir*, the shared leaf
    :func:`fold_chdir_into_operands` uses for every shape (combined,
    equals-joined, separate-form, and bare positional) it recognizes.

    An empty or already-absolute value is returned unchanged -- unambiguous
    regardless of *chdir*. Deliberately does NOT require *value* to contain
    a path separator (Codex review, Finding 12, fresh evidence): a
    single-segment relative directory name (``include``, no ``/``/``\\`` at
    all) is exactly as real a path as a multi-segment one, and the caller
    already established via flag/positional CONTEXT (not string shape) that
    this value is meant as a path -- see :func:`fold_chdir_into_operands`'s
    own docstring for the context-tracking this replaces a separator-based
    guess with.
    """
    if not value or is_absolute_path_token(value):
        return value
    return join_path_token(chdir, value)


def fold_chdir_into_operands(tokens: list[str], chdir: str | None) -> list[str]:
    """Fold *chdir* (an ``env -C``/``--chdir`` effective directory) into
    the driver command's OWN arguments -- everything AFTER the driver
    token itself, which :func:`_apply_env_context` already handles (Codex
    review, Finding 8, fresh evidence).

    GNU ``env -C DIR`` chdirs the WHOLE invoked process before it
    ``execvp()``s the real command -- every relative filesystem argument
    the command receives, not merely its own executable name, is
    interpreted relative to that same chdir'd directory by the real OS.
    For ``env -C build clang-cl -I../include /c ../src/x.cpp``, real
    ``env`` genuinely resolves both ``../include`` and ``../src/x.cpp``
    against ``<cu.directory>/build``, not bare ``cu.directory``. A falsy
    *chdir* (no ``env -C``/``--chdir`` prefix was seen) is a complete
    no-op, returning *tokens* unchanged.

    **A stateful, FLAG-CONTEXT-AWARE scan, not a per-token string-shape
    guess (Codex review, Findings 11/12, fresh evidence).** An earlier
    revision classified each token independently by whether it *looked*
    like a path (started with ``-``/``/``, contained a separator) --
    proven wrong in two different, opposite directions:

    * **Finding 11 (false positive):** the separate-form VALUE of a
      ``-D``/``-U``/``/D``/``/U`` macro definition, e.g. ``-D
      FOO=a/b`` -- two tokens, ``"-D"`` and ``"FOO=a/b"`` -- has its own
      value token misread as a bare positional path operand purely
      because it contains a ``/``, corrupting the macro's literal text
      value (``FOO=a/b`` -> ``FOO=build/a/b``). Fixed by tracking, while
      walking, that a macro flag's OWN following token is never resolved
      -- mirroring this module's existing ``MACRO_DEFINITION_PREFIXES``
      precedent for the combined form, extended here to the separate one.
    * **Finding 12 (false negative):** a real relative path can have NO
      separator at all -- ``-Iinclude`` (combined) or ``-I include``
      (separate) both name a subdirectory one level down, exactly as
      real a path as ``-I../include``, but the previous "must contain a
      separator" gate silently left both unresolved. Fixed by deciding
      whether to fold from flag/positional CONTEXT (is this token, or the
      token immediately after a known path-taking flag, a path VALUE at
      all) rather than the token's own textual shape -- see
      :func:`_fold_chdir_path_value`, the shared leaf every recognized
      shape below funnels through, which folds unconditionally once
      context has established a value is a path.
    * **Round 25 Finding 2 (false positive, the general form of Finding
      11):** Finding 11 only exempted the macro-flag family
      (``-D``/``-U``); every OTHER separate-form value-taking flag's
      operand was still classified purely by textual shape (item 6
      below), so ``-x c++`` -- a language name, not a path -- was folded
      into ``-x build/c++``, and the identical corruption applied to
      ``-target <triple>``, ``-arch <name>``, and ``-Xclang <value>``.
      Fixed the same way Finding 11 was: a positively-confirmed-non-path
      flag (:data:`_CHDIR_FOLD_NON_PATH_VALUE_FLAGS`) consumes its flag
      and value token verbatim, checked ahead of the generic positional
      fallback -- not by trying to recognize every possible flag (an
      unrecognized flag's own operand still falls through to item 6,
      unchanged from before, the same conservative default this module
      has always used for a flag it cannot positively classify).

    Recognizes, in order, at each position:

    1. A macro flag (bare ``-D``/``-U``/``/D``/``/U``, separate-form) --
       consumes the flag AND its value token verbatim, never resolved.
    2. A macro flag's COMBINED form (``-DFOO=...``, longer than the bare
       prefix) -- passed through verbatim, same as (1).
    3. A confirmed-non-path SEPARATE-form value flag
       (:data:`_CHDIR_FOLD_NON_PATH_VALUE_FLAGS`) -- consumes the flag AND
       its value token verbatim, never resolved, same treatment as (1).
    4. An ``=``-joined path flag (:data:`_CHDIR_FOLD_EQUALS_PATH_PREFIXES`)
       -- the portion after ``=`` is resolved.
    5. A COMBINED-form path flag (:data:`_CHDIR_FOLD_COMBINED_PATH_PREFIXES`)
       -- the portion after the flag prefix is resolved.
    6. A SEPARATE-form path flag (:data:`_CHDIR_FOLD_SEPARATE_PATH_FLAGS`)
       -- the flag itself is passed through, and the FOLLOWING token
       (its value) is resolved regardless of its own shape.
    7. A response-file token (``@args.rsp``, Codex round 29): ``@``
       preserved, only its payload resolved.
    8. A bare, non-flag positional token recognizable as a source/header
       operand by its suffix (:func:`looks_like_source_file_operand`,
       round 30 Finding 4) -- OR, from CONTEXT rather than suffix, when an
       explicit ``-x <language>`` already appeared earlier in this same
       argv (round 31 Finding 2, Codex review): an otherwise-unrecognized
       bare token can genuinely be an EXTENSIONLESS source file
       (``clang -x c++ generated -Iinclude``) -- resolved either way.
       Anything else bare, with no preceding ``-x``, falls to (9).
    9. Anything else -- unchanged.
    """
    if not chdir:
        return tokens
    out: list[str] = []
    i = 0
    n = len(tokens)
    saw_explicit_language_flag = False
    while i < n:
        tok = tokens[i]
        if tok in MACRO_DEFINITION_PREFIXES and i + 1 < n:
            out.append(tok)
            out.append(tokens[i + 1])
            i += 2
            continue
        if tok.startswith(MACRO_DEFINITION_PREFIXES) and len(tok) > 2:
            out.append(tok)
            i += 1
            continue
        if tok in _CHDIR_FOLD_NON_PATH_VALUE_FLAGS and i + 1 < n:
            if tok == "-x":
                saw_explicit_language_flag = True
            out.append(tok)
            out.append(tokens[i + 1])
            i += 2
            continue
        matched = False
        for prefix in _CHDIR_FOLD_EQUALS_PATH_PREFIXES:
            if tok.startswith(prefix):
                out.append(prefix + _fold_chdir_path_value(tok[len(prefix) :], chdir))
                matched = True
                break
        if matched:
            i += 1
            continue
        for prefix in _CHDIR_FOLD_COMBINED_PATH_PREFIXES:
            if tok.startswith(prefix) and len(tok) > len(prefix):
                out.append(prefix + _fold_chdir_path_value(tok[len(prefix) :], chdir))
                matched = True
                break
        if matched:
            i += 1
            continue
        if tok in _CHDIR_FOLD_SEPARATE_PATH_FLAGS and i + 1 < n:
            out.append(tok)
            out.append(_fold_chdir_path_value(tokens[i + 1], chdir))
            i += 2
            continue
        if tok.startswith("@") and len(tok) > 1:
            out.append("@" + _fold_chdir_path_value(tok[1:], chdir))
            i += 1
            continue
        if not tok.startswith(("-", "/")) and (
            looks_like_source_file_operand(tok) or saw_explicit_language_flag
        ):
            out.append(_fold_chdir_path_value(tok, chdir))
            i += 1
            continue
        out.append(tok)
        i += 1
    return out

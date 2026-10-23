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
"""Launcher and ``env`` prefix traversal for compile argv (ADR-030 D2).

The leaf that :func:`~.compile_invocation.strip_launchers` drives: it walks a
leading ``env`` invocation (``-C``/``-u``/``-i``/``-S`` and ``NAME=VALUE``
assignments), compiler-cache launchers (``ccache``/``sccache``/``distcc``) and
their ``KEY=VALUE`` config overrides, and folds the resulting chdir and
``PATH`` into the driver token and its operands. Tool-independent and pure:
nothing here shells out.

Split out of ``_argv.py`` so the parse step in
:mod:`.compile_invocation` owns the launcher decision and ``_argv.py`` keeps
only the shared helpers and backend rendering.
"""

from __future__ import annotations

import re
import shlex

from ._argv import basename, unredact_home
from ._argv_shortopts import (
    is_absolute_path_token,
    is_short_flag_cluster,
    join_path_token,
    normalize_path_token,
)

# ``header_utils.is_msvc_driver_stem`` — ``is_msvc_mode`` below delegates to it,
# so the build-evidence adapter and this replay path cannot drift on which
# drivers are CL-mode (they had: ``dpcpp-cl`` and version-suffixed spellings
# such as ``clang-cl-20`` were known here and not there).

#: Compiler-launcher wrappers that prefix the real compiler in a build action
#: (``ccache clang++ -c foo.cpp``). The extractor must emulate the real compiler,
#: not the launcher, which would otherwise run without its compiler operand.
COMPILER_LAUNCHERS = frozenset(
    {"ccache", "sccache", "distcc", "icecc", "icerun", "buildcache"}
)
#: ccache's own documented per-invocation config-override form —
#: ``ccache KEY=VALUE ... compiler [compiler options]`` (ccache manual,
#: "Configuration" section, e.g. ``ccache compiler_check="%compiler%
#: --version" gcc -c foo.c``) — lets a caller override a config setting
#: without touching ``ccache.conf``/env vars. A bare ``KEY=VALUE`` token here
#: is never a flag (it never starts with ``-``), so it is unambiguous against
#: real compiler argv.
#:
#: Also reused, unchanged, by :func:`_skip_env_prefix` below for POSIX
#: ``env``'s own ``NAME=VALUE`` environment-assignment operands (P2 review,
#: "Unwrap environment prefixes before locating clang-cl", fresh evidence) --
#: it is the identical ``NAME=VALUE`` shape either tool accepts, so one
#: compiled pattern serves both.
_LAUNCHER_CONFIG_OVERRIDE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
#: The basename(s) that mean "this token is the POSIX ``env`` utility", the
#: same ``.exe``-suffix-tolerant convention :data:`COMPILER_LAUNCHERS`
#: matching already uses.
_ENV_BASENAMES = frozenset({"env", "env.exe"})


class _EnvPathCleared:
    """Sentinel: this ``env`` layer explicitly reset ``PATH`` to empty
    (round 26 Finding 3, Codex review, fresh evidence).

    ``-i``/``--ignore-environment`` (also its deprecated bare ``-`` alias)
    and ``-u PATH``/``--unset PATH`` both mean "the command that follows
    sees NO inherited ``PATH`` at all" -- confirmed against a real
    installed ``env --help`` (``-i, --ignore-environment``: "start with an
    empty environment"). :func:`_traverse_env_and_launcher_prefix` must be
    able to tell that apart from "this ``env`` layer stated no opinion on
    ``PATH``, inherit whatever an outer layer already established" -- a
    plain ``str | None`` return can't: ``None`` was already used for the
    latter. Without a distinct third state, a nested
    ``env PATH=/tmp/tool /usr/bin/env -i clang-cl ...`` incorrectly let the
    OUTER ``PATH=/tmp/tool`` survive through the inner ``-i``, even though
    real GNU ``env -i`` starts the wrapped command with a genuinely empty
    environment -- no ``PATH`` at all, inherited or otherwise.

    Confined to this module's internal traversal: :func:`_skip_env_prefix`
    produces it, :func:`_traverse_env_and_launcher_prefix` consumes it and
    resets its own accumulated ``env_path`` to ``None``, and it never
    escapes that function -- every other consumer of ``env_path``
    (:func:`_apply_env_context`, :func:`_resolve_env_path_entries`) still
    only ever sees a real ``str`` or ``None``, unchanged.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "_ENV_PATH_CLEARED"


#: The one instance of :class:`_EnvPathCleared` -- see its own docstring.
_ENV_PATH_CLEARED = _EnvPathCleared()
#: ``env`` flags that stand alone -- no operand of their own follows.
#: ``-i``/``--ignore-environment`` clears the inherited environment before
#: applying any ``NAME=VALUE`` assignments; the others are cosmetic/output
#: modifiers. None of these change which *token* is the real driver, only
#: how ``env`` itself behaves, so they are skipped rather than parsed.
#:
#: ``-v``'s long form is ``--debug``, NOT ``--verbose`` (Codex review,
#: ``_argv.py:87``, Finding 4, fresh evidence, confirmed against a real
#: installed ``env --help``/``man env``: GNU coreutils' documented option is
#: exactly ``-v, --debug`` -- "print verbose information for each
#: processing step" -- and the real CLI rejects ``--verbose`` outright
#: (exit code 125, "unrecognized option"). The previous, incorrect
#: ``--verbose`` spelling meant a real-world recorded command like
#: ``env --debug clang-cl /c foo.cc`` was never recognized as this flag at
#: all: :func:`_skip_env_prefix`'s flag loop fell through every other
#: branch and hit its terminal ``break``, treating the literal ``--debug``
#: token itself as if it were the compiler driver and losing the real
#: driver (``clang-cl``) and everything after it.
_ENV_NO_OPERAND_FLAGS = frozenset(
    {
        "-i",
        "--ignore-environment",
        "-0",
        "--null",
        "-v",
        "--debug",
        "--block-signal",
        "--default-signal",
        "--ignore-signal",
        "--list-signal-handling",
    }
)
#: The subset of the signal-handling flags above whose ``SIG`` argument is
#: OPTIONAL, spelled with an ``=`` when given (Codex review round 25,
#: Finding 3, fresh evidence, confirmed against a real installed ``env
#: --help``: ``--block-signal[=SIG]``, ``--default-signal[=SIG]``,
#: ``--ignore-signal[=SIG]`` are all documented with the bare form already
#: covered by :data:`_ENV_NO_OPERAND_FLAGS` above, plus this ``=SIG`` form
#: -- unlike ``--chdir``, which is only ever a SEPARATE-token operand, GNU
#: ``env`` documents no bare-flag-plus-separate-token spelling for any of
#: these three (``env --ignore-signal PIPE ...`` would misparse ``PIPE`` as
#: the command). ``--list-signal-handling`` takes no argument at all, in
#: either form, and needs no entry here.
#:
#: Without this, a real recorded command like ``env --ignore-signal=PIPE
#: clang-cl /c x.cc`` fell through every recognized branch in
#: :func:`_skip_env_prefix` and hit its terminal ``break``, misreading the
#: literal ``--ignore-signal=PIPE`` token itself as the compiler driver --
#: the identical failure shape the ``--debug``/``-v`` fix above and the
#: bare-``-``/``--`` fix both already document for this same function.
_ENV_OPTIONAL_SIG_FLAG_PREFIXES = (
    "--block-signal=",
    "--default-signal=",
    "--ignore-signal=",
)
#: ``env`` flags that take a following, separate-token operand: POSIX
#: ``-u NAME`` (unset one variable) and GNU's ``-C``/``--chdir DIR``
#: extension (change directory before running the command).
#:
#: ``-C``/``--chdir`` is matched separately, ahead of this generic set, by
#: :func:`_skip_env_prefix` -- unlike ``-u``/``--unset``, its *value* is
#: needed downstream (see :data:`_ENV_CHDIR_FLAGS`'s own docstring), not
#: merely skipped.
_ENV_OPERAND_FLAGS = frozenset({"-u", "--unset", "-C", "--chdir"})
#: The subset of :data:`_ENV_OPERAND_FLAGS` whose operand is captured (not
#: just skipped) by :func:`_skip_env_prefix`, feeding
#: :func:`_apply_env_context`'s chdir-folding below (P2 review, "Apply env
#: chdir before resolving relative compiler paths", fresh evidence).
_ENV_CHDIR_FLAGS = frozenset({"-C", "--chdir"})
#: The subset of :data:`_ENV_OPERAND_FLAGS` that UNSETS a variable (POSIX
#: ``-u NAME``/``--unset NAME``) -- when *NAME* is exactly ``PATH``, this
#: env layer explicitly clears any inherited ``PATH``, the same as
#: ``-i``/``--ignore-environment`` (round 26 Finding 3, Codex review, fresh
#: evidence) -- see :class:`_EnvPathCleared`'s own docstring for why this
#: must be tracked as a distinct third state, not merely skipped like
#: unsetting any other variable.
_ENV_UNSET_FLAGS = frozenset({"-u", "--unset"})
#: ``env`` flags that clear the ENTIRE inherited environment, ``PATH``
#: included -- POSIX ``-i``/``--ignore-environment`` and its deprecated
#: bare ``-`` alias (matched separately, in :func:`_skip_env_prefix`'s own
#: ``arg == "-"`` branch) both mean "start with an empty environment" per a
#: real installed ``env --help`` (round 26 Finding 3, Codex review, fresh
#: evidence).
_ENV_IGNORE_ENV_FLAGS = frozenset({"-i", "--ignore-environment"})
#: The env-assignment name whose value is captured (not just skipped) by
#: :func:`_skip_env_prefix`, feeding :func:`_apply_env_context`'s
#: PATH-resolution below (P2 review, "Resolve drivers using the
#: env-supplied PATH", fresh evidence). Only a *bare* driver name (no path
#: separator) is ever looked up against it -- see that function's own
#: docstring.
_ENV_PATH_ASSIGNMENT_PREFIX = "PATH="
#: GNU ``env``'s ``-S``/``--split-string`` flag: "process and split S into
#: separate arguments" (installed ``env --help``), documented for shebang
#: lines that can only pass one argument
#: (e.g. ``#!/usr/bin/env -S clang-cl /c``). Unlike every other ``env``
#: flag recognized above, this one does not merely stand alone or carry a
#: single opaque operand -- its value is itself a *further* command line
#: that must be split and spliced into ``argv`` in place of the flag and
#: its raw string, or the apparent "compiler" the rest of this module (and
#: every downstream launcher/driver-detection consumer) sees is the literal
#: flag token / unsplit string rather than the real driver
#: (Codex review, ``_argv.py:102``, fresh evidence).
_ENV_SPLIT_STRING_SHORT = "-S"
_ENV_SPLIT_STRING_LONG = "--split-string"
#: Value-taking toolchain flags already normalized into the structured
#: ``sysroot``/``target_triple`` fields. They must NOT be carried through from
#: ``abi_relevant_flags``: the adapter records only the bare option token for the
#: split spelling (``-isysroot /sdk`` → just ``-isysroot``, operand dropped), so
#: re-appending it dangles and swallows the following argv token.
#:
#: A survivor for one of :data:`SPLIT_OPERAND_ABI_FLAGS`
#: (``-target-abi``/``-target-cpu``/``-target-feature``/
#: ``-target-linker-version``, and their ``-Xclang``-wrapped spelling) shares
#: this same ``-target``/``-Xclang `` prefix but is NOT represented by any
#: structured field, so :func:`_carry_abi_relevant_flags` checks
#: :func:`is_split_operand_abi_flag_survivor` first and exempts it from this
#: filter -- see that predicate's own docstring.


def _expand_env_split_string(value: str) -> list[str]:
    """Split *value* the way GNU ``env -S``/``--split-string`` does --
    approximately: see the **Known gap** paragraph below for exactly what
    is and is not covered (Codex review round 25, Finding 1, fresh
    evidence).

    The installed ``env --help`` documents ``-S, --split-string=S`` as
    "process and split S into separate arguments; used to pass multiple
    arguments on shebang lines". :func:`shlex.split` (POSIX mode, the
    default) is used here rather than a byte-for-byte reimplementation of
    GNU coreutils' own parser, and correctly handles the common,
    realistic case this flag exists for: plain space-separated arguments,
    and single/double-quoted arguments that contain a space
    (``-S "clang-cl /c 'a file with spaces.cc'"``).

    **Known gap: GNU ``env -S`` has its own, ENV-SPECIFIC escape grammar
    that is not POSIX-shell-word-splitting, and this function does not
    implement it.** Verified directly against a real installed GNU
    coreutils 9.4 ``env`` (``env -v -S '...'``, which prints its own
    parse trace): ``\\_`` (backslash-underscore) means "split an argument
    here", i.e. it becomes a token boundary, NOT a literal underscore --
    ``env -S 'a\\_b'`` splits into two arguments ``a`` and ``b``, while
    :func:`shlex.split` (ordinary POSIX word-splitting, which has no
    concept of ``\\_`` at all) produces one token ``a_b``. GNU ``env`` also
    documents/exhibits several further escapes with no POSIX-shell
    equivalent: ``\\t``/``\\n``-family escapes are preserved as literal
    control characters rather than word-split; ``\\c`` truncates the rest
    of the string as a comment terminator (``env -S 'a\\cREST'`` yields
    only ``a``, dropping ``REST`` entirely); and its quote handling,
    while superficially POSIX-shell-shaped for the simple cases, is its
    own parser, not actually POSIX ``sh``.

    This is deliberately NOT implemented as a full custom parser here,
    per this repo's own established "known gaps over risky reactive
    patches" convention (``AGENTS.md``): ``-S``/``--split-string`` exists
    specifically for shebang-line use (``#!/usr/bin/env -S clang-cl
    /c``), a context with no relationship to how a real build system's
    compile database records an already-fully-expanded ``argv`` -- a
    captured build action essentially never contains a *literal,
    unexpanded* ``env -S`` invocation with one of these exotic escapes in
    its own recorded command line (the shebang is what the shell/exec
    layer already expanded away long before any build tool recorded
    ``argv``). Building and maintaining a byte-for-byte reimplementation
    of GNU coreutils' own ``-S`` grammar (quote handling, ``\\_``
    mid-argument splitting, the ``\\c`` comment terminator, and every
    other escape GNU documents) is a genuinely large, narrowly-scoped
    subsystem for a case with no known real-world reproduction in
    captured build evidence. The plain space/quote-separated common case
    -- what a hand-written or generated ``env -S '...'`` invocation
    realistically looks like -- is handled correctly today; only the
    backslash-escape grammar specifically is the gap.

    **Degrades to whitespace splitting on malformed input rather than
    raising (round 30 Finding CR4, CodeRabbit review).**
    ``shlex.split()`` raises ``ValueError`` on e.g. an unbalanced quote --
    a persisted, untrusted ``-S`` value must not abort collection for
    every other compile unit. A ``.split()`` fallback never crashes.
    """
    try:
        return shlex.split(value)
    except ValueError:
        return value.split()


def _skip_env_prefix(
    argv: list[str], i: int
) -> tuple[int, str | None, str | _EnvPathCleared | None]:
    """Skip a leading POSIX ``env`` invocation and its flags/assignments.

    A real build recipe recorded via an environment-scoped invocation or
    wrapper script commonly looks like ``env SDKROOT=/sdk clang-cl /c
    foo.cc`` (P2 review, "Unwrap environment prefixes before locating
    clang-cl", fresh evidence): ``env --help``/POSIX confirm the executable
    always follows the leading ``env`` token, its own flags, and any
    ``NAME=VALUE`` assignments -- ``env [-i] [-u NAME]... [NAME=VALUE]...
    command [args]``. Before this, :func:`strip_launchers` recognized only
    the six compiler-cache/distribution launcher names, so an
    ``env``-prefixed command computed driver index 0 (``env`` itself) and
    never examined the actual compiler: ``_msvc_driver_scan`` found no
    ``cl``/``clang-cl``-basename token at any recognized executable
    position, ``msvc_driver_token`` returned ``None``, ``_derived_gcc_path``
    fell back to ``argv[0]`` (``"env"``), and ``abicheck.extract.compiler_driver.resolve_clang_bin``
    rejected that name and silently substituted plain ``clang++`` -- losing
    the recorded toolchain's built-ins, default headers, and target
    defaults.

    Returns ``(i, chdir, env_path)``: *i* unchanged (and ``chdir``/
    ``env_path`` both ``None``) when ``argv[i]`` does not name ``env``
    itself, so a caller can call this unconditionally ahead of
    launcher-prefix stripping. Reuses :data:`_LAUNCHER_CONFIG_OVERRIDE_RE`
    for the ``NAME=VALUE`` assignments -- the identical shape ccache's own
    config-override tokens already use, see that pattern's docstring.

    *chdir* and *env_path* are two of ``env``'s own effects that change how
    the token :func:`strip_launchers` returns as the driver must be
    interpreted, not merely which token it is (P2 review round 19, "Apply
    env chdir before resolving relative compiler paths" /
    "Resolve drivers using the env-supplied PATH", fresh evidence): an
    earlier revision recognized and *discarded* both ``-C DIR``/
    ``--chdir[=DIR]`` and a ``PATH=...`` assignment identically to every
    other skipped flag/assignment, correctly finding the real driver token
    but silently dropping information needed to resolve THAT token
    correctly --

    * ``-C DIR`` (GNU ``env``'s documented "change working directory to DIR
      before running the command" extension) means a *relative* driver path
      following it (``env -C build ../llvm/bin/clang-cl ...``) is relative
      to ``<cu.directory>/DIR``, not bare ``cu.directory`` --
      :func:`~abicheck.buildsource.header_compile_context._derived_gcc_path`
      previously resolved it against ``cu.directory`` alone, reporting a
      genuinely executable compiler as missing (or, worse, silently
      resolving to a *different*, wrong file that happens to exist at the
      un-chdir'd location).
    * ``PATH=/opt/llvm/bin`` (env's documented ``NAME=VALUE`` command-scoped
      environment override) means a *bare* driver name that follows
      (``env PATH=/opt/llvm/bin clang-cl ...``) may only be resolvable
      through that overridden ``PATH``, not abicheck's own inherited one --
      every downstream ``shutil.which``-style lookup (``resolve_clang_bin``,
      replay subprocess spawning) searches the wrong list of directories
      without it.

    Returned rather than acted on here: this function only locates the
    prefix and its two carried values; :func:`strip_launchers` (the one
    caller) applies them to the actual driver token once it is known, via
    :func:`_apply_env_context`.

    **``env_path``'s third state (round 26 Finding 3, Codex review, fresh
    evidence).** ``-i``/``--ignore-environment`` (and its deprecated bare
    ``-`` alias) or ``-u PATH``/``--unset PATH`` mean this ``env`` layer
    explicitly starts the wrapped command with NO ``PATH`` at all, which is
    a different thing from "this layer said nothing about ``PATH``,
    inherit whatever an outer layer already established" -- plain ``None``
    already means the latter, so a third value,
    :data:`_ENV_PATH_CLEARED` (an instance of :class:`_EnvPathCleared`), is
    returned for the former. See that class's own docstring for the full
    reasoning and the nested-``env -i`` case it exists to fix. This
    function itself does nothing with the distinction beyond returning it
    -- :func:`_traverse_env_and_launcher_prefix` (the one caller) is where
    it actually resets any already-accumulated ``env_path``.

    **Mutates** *argv* **in place for** ``-S``/``--split-string``
    (Codex review, ``_argv.py:102``, fresh evidence): unlike every other
    flag this function recognizes, ``-S``/``--split-string``'s value is
    itself a further, unexpanded command line (``env -S 'clang-cl /c
    x.cc'``) that must be split (:func:`_expand_env_split_string`) and
    spliced into *argv* in place of the flag and its raw string -- there is
    no scalar "carried value" to return for it the way ``chdir``/
    ``env_path`` are, since the whole point is that downstream
    launcher/driver detection must see the *expanded* tokens as if they had
    been passed directly. :func:`strip_launchers` therefore passes this
    function a local, already-copied ``argv`` (never the caller's own
    ``compile_unit.argv``) precisely so this in-place splice cannot corrupt
    persisted evidence.
    """
    if i >= len(argv) or basename(argv[i]).lower() not in _ENV_BASENAMES:
        return i, None, None
    i += 1
    chdir: str | None = None
    env_path: str | _EnvPathCleared | None = None
    while i < len(argv):
        arg = argv[i]
        if arg == "-":
            # GNU/POSIX `env -` is a deprecated, documented equivalent of
            # `env -i` (clears the inherited environment) -- confirmed
            # against a real installed `env --help`/`man env` (Codex
            # review, Finding 6, fresh evidence). Neither this bare `-`
            # form nor `--` (below) matched any recognized flag/assignment
            # shape, so the scan fell through to the terminal `break` and
            # treated the literal `-`/`--` token itself as the command --
            # losing the real driver and everything after it, the same
            # failure shape the incorrect `--verbose` spelling produced.
            # No operand of its own -- like `-i`, only skipped. Also clears
            # PATH the same as `-i` itself (round 26 Finding 3) -- it is
            # documented as exactly that flag's equivalent, not a lesser
            # form of it.
            env_path = _ENV_PATH_CLEARED
            i += 1
            continue
        if arg == "--":
            # GNU `env`'s `--` ends OPTION parsing only, NOT the
            # `NAME=VALUE...` list before the command (round 30 Finding 1:
            # `env -- FOO=bar clang-cl -c x.cc` was mistaking `"FOO=bar"`
            # for the driver). Consume `--`, keep consuming `NAME=VALUE`
            # tokens (still tracking `PATH=`) without reinterpreting any
            # as an env OPTION -- the first non-assignment token is the
            # real command.
            i += 1
            while i < len(argv):
                nxt = argv[i]
                if nxt.startswith(_ENV_PATH_ASSIGNMENT_PREFIX):
                    env_path = nxt.removeprefix(_ENV_PATH_ASSIGNMENT_PREFIX)
                    i += 1
                    continue
                if _LAUNCHER_CONFIG_OVERRIDE_RE.match(nxt):
                    i += 1
                    continue
                break
            break
        if arg in _ENV_NO_OPERAND_FLAGS:
            if arg in _ENV_IGNORE_ENV_FLAGS:
                # `-i`/`--ignore-environment` starts the wrapped command
                # with a genuinely empty environment -- PATH included --
                # not merely "no opinion" (round 26 Finding 3; see
                # `_EnvPathCleared`'s own docstring).
                env_path = _ENV_PATH_CLEARED
            i += 1
            continue
        if is_short_flag_cluster(arg):  # e.g. -iv == -i -v (Codex round 29)
            if "i" in arg[1:]:
                env_path = _ENV_PATH_CLEARED
            i += 1
            continue
        if arg.startswith(_ENV_OPTIONAL_SIG_FLAG_PREFIXES):
            i += 1
            continue
        if arg in _ENV_CHDIR_FLAGS and i + 1 < len(argv):
            chdir = argv[i + 1]
            i += 2
            continue
        if arg.startswith("-C") and len(arg) > 2 and not arg.startswith("--"):
            chdir = arg[2:]  # -Cbuild == -C build (Codex round 29)
            i += 1
            continue
        if arg.startswith("--chdir="):
            chdir = arg.removeprefix("--chdir=")
            i += 1
            continue
        if arg == _ENV_SPLIT_STRING_SHORT and i + 1 < len(argv):
            argv[i : i + 2] = _expand_env_split_string(argv[i + 1])
            continue
        if (
            arg.startswith(_ENV_SPLIT_STRING_SHORT)
            and len(arg) > len(_ENV_SPLIT_STRING_SHORT)
            and not arg.startswith("--")
        ):
            argv[i : i + 1] = _expand_env_split_string(
                arg[len(_ENV_SPLIT_STRING_SHORT) :]
            )
            continue
        if arg == _ENV_SPLIT_STRING_LONG and i + 1 < len(argv):
            argv[i : i + 2] = _expand_env_split_string(argv[i + 1])
            continue
        if arg.startswith(_ENV_SPLIT_STRING_LONG + "="):
            argv[i : i + 1] = _expand_env_split_string(
                arg.removeprefix(_ENV_SPLIT_STRING_LONG + "=")
            )
            continue
        if arg in _ENV_OPERAND_FLAGS and i + 1 < len(argv):
            if arg in _ENV_UNSET_FLAGS and argv[i + 1] == "PATH":
                # `-u PATH`/`--unset PATH` explicitly unsets PATH for the
                # wrapped command -- the same "no PATH at all" state `-i`
                # produces, not "no opinion" (round 26 Finding 3; see
                # `_EnvPathCleared`'s own docstring). Any OTHER `-u NAME`
                # unsets an unrelated variable this module doesn't track.
                env_path = _ENV_PATH_CLEARED
            i += 2
            continue
        if arg.startswith("--unset="):
            if arg.removeprefix("--unset=") == "PATH":
                # The joined `--unset=PATH` spelling of the same `-u PATH`
                # case handled above -- must clear PATH identically (round
                # 26 Finding 3 follow-through: the separate-token form was
                # fixed but this equally-real joined form was not).
                env_path = _ENV_PATH_CLEARED
            i += 1
            continue
        if arg.startswith(_ENV_PATH_ASSIGNMENT_PREFIX):
            env_path = arg.removeprefix(_ENV_PATH_ASSIGNMENT_PREFIX)
            i += 1
            continue
        if _LAUNCHER_CONFIG_OVERRIDE_RE.match(arg):
            i += 1
            continue
        break
    return i, chdir, env_path


def _traverse_env_and_launcher_prefix(
    argv: list[str],
) -> tuple[int, str | None, str | None, bool]:
    """Walk *argv* from the start, unwrapping every interleaved ``env``
    prefix (splicing any ``-S``/``--split-string`` value in place via
    :func:`_skip_env_prefix`) and compiler-launcher prefix -- the ONE
    shared traversal :func:`strip_launchers`, :func:`expand_env_split_prefixes`,
    and :func:`effective_directory` all build on, rather than three
    independently-drifting copies of the same interleaving logic (Codex
    review, Finding 9/10 follow-up, "the same traversal, one
    implementation" -- extracted after Finding 9 showed a second,
    independently-written copy of this exact loop had silently drifted
    from :func:`strip_launchers`'s own).

    **Mutates** *argv* **in place for** ``-S``/``--split-string`` splicing,
    same as :func:`_skip_env_prefix` itself -- callers that must not
    mutate the caller's own list (every one of this function's current
    callers) pass an already-copied ``argv``.

    Returns ``(i, chdir, env_path, path_cleared)``: *i* is the index of the
    first token that is neither an ``env`` invocation nor a recognized
    compiler launcher (the real driver position, or ``len(argv)`` if the
    whole list was prefix); *chdir* is the fully COMPOSED effective
    ``-C``/``--chdir`` value across every ``env`` layer seen -- two
    RELATIVE values compose (``env -C a env -C b ...`` -> ``a/b``), a later
    ABSOLUTE value fully replaces whatever was accumulated (P2 review round
    19 + Codex review Finding 7); *env_path* is the most recently seen
    ``PATH=`` override -- or ``None`` again once a later, nested ``env``
    layer explicitly clears it (``-i``/``-u PATH``, round 26 Finding 3 --
    see :class:`_EnvPathCleared`'s own docstring). Only a real ``str`` or
    ``None`` is ever returned for *env_path*; the sentinel itself is
    resolved away inside this function's own loop, never handed to a
    caller.

    **``path_cleared`` (round 27 Finding 1, Codex review, fresh evidence,
    continuing round 26 Finding 3).** Round 26 correctly told apart "PATH
    was explicitly cleared" from "no opinion" WITHIN this function's own
    loop (:data:`_ENV_PATH_CLEARED`), but the distinction was then
    discarded the moment this function returned: every caller only ever
    saw ``env_path is None`` for both states, indistinguishable from "no
    ``env`` prefix ever mentioned PATH at all, inherit whatever abicheck's
    own process PATH already is" -- a state a real ``execvp()`` of the
    identical recorded command could never reach. This flag survives that
    boundary: ``True`` when the LAST env layer's PATH state was "cleared"
    (an ``-i``/``-u PATH`` seen with no later, real ``PATH=`` override to
    un-clear it), ``False`` otherwise -- including the ordinary
    no-``env``-prefix-at-all case. A caller resolving a still-bare driver
    token (no path separator) against a PATH search must treat
    ``path_cleared and not env_path`` as "this token is UNRESOLVABLE by any
    real PATH search, do not consult abicheck's own inherited ``PATH`` for
    it" -- see :func:`env_path_cleared_for_bare_token`, the one predicate
    every such caller should use rather than re-deriving this logic.
    """
    i = 0
    chdir: str | None = None
    env_path: str | None = None
    path_cleared = False
    progressed = True
    while progressed:
        progressed = False
        new_i, new_chdir, new_path = _skip_env_prefix(argv, i)
        if new_i != i:
            i = new_i
            progressed = True
            if new_chdir is not None:
                if chdir and not is_absolute_path_token(new_chdir):
                    chdir = join_path_token(chdir, new_chdir)
                else:
                    chdir = new_chdir
            if isinstance(new_path, _EnvPathCleared):
                # This `env` layer explicitly reset PATH (`-i`/`-u PATH`)
                # -- any PATH= accumulated from an OUTER layer must not
                # survive through it (round 26 Finding 3). A later,
                # further-nested layer can still set its own real PATH=
                # afterward -- that arrives as an ordinary `str` on a
                # subsequent iteration and overwrites this `None` normally,
                # via the branch below. `isinstance`, not `is`, so mypy can
                # narrow `new_path` to plain `str` in the `elif` below
                # (identity comparison against a singleton value doesn't
                # narrow a `str | _EnvPathCleared | None` union the same
                # way).
                env_path = None
                path_cleared = True
            elif new_path is not None:
                env_path = new_path
                # A real, later PATH= override un-clears PATH (round 27
                # Finding 1) -- there is now a genuine directory list to
                # search again, so a bare token is no longer provably
                # unresolvable.
                path_cleared = False
        while (
            i < len(argv)
            and basename(argv[i]).lower().removesuffix(".exe") in COMPILER_LAUNCHERS
        ):
            i += 1
            while i < len(argv) and _LAUNCHER_CONFIG_OVERRIDE_RE.match(argv[i]):
                i += 1
            progressed = True
    return i, chdir, env_path, path_cleared


def env_path_cleared_for_bare_token(argv: list[str], token: str) -> bool:
    """Whether *token* -- a driver name resolved (by any means) from
    *argv* -- could never be found by a real PATH search, because the
    ``env`` prefix wrapping *argv* explicitly cleared PATH (round 27
    Finding 1, Codex review, fresh evidence; continues round 26 Finding
    3's ``_EnvPathCleared`` sentinel work).

    :func:`_traverse_env_and_launcher_prefix` tracks "PATH was cleared"
    correctly while walking an ``env``/launcher prefix, but every caller
    of :func:`strip_launchers` previously saw only a plain ``str | None``
    ``env_path`` -- collapsing "PATH explicitly cleared" and "no ``env``
    prefix ever mentioned PATH, inherit abicheck's own process PATH" into
    the same ``None``. Concretely: ``strip_launchers(["env", "-i", "cc",
    ...])`` returns the bare, unresolved token ``"cc"`` -- correct, since
    :func:`_apply_env_context` only resolves a bare token when a real
    ``env_path`` override exists -- but nothing stopped a DOWNSTREAM
    consumer (``pick_compiler_binary``, ``header_compile_context.
    _derived_gcc_path``) from later handing that same bare ``"cc"`` to a
    ``shutil.which``-style lookup against abicheck's OWN inherited
    ``PATH`` -- which a real ``env -i cc ...`` invocation could never do:
    with no ``PATH`` at all, ``execvp`` cannot find a bare command name by
    search, only an absolute/relative path (a token containing a
    separator) resolves. Doing so anyway risks silently substituting a
    completely different, unrelated compiler that merely happens to share
    the recorded name, corrupting the L2/L4 replay's toolchain identity
    without any visible error.

    A path-shaped *token* (containing ``/``/``\\``) is unaffected --
    always ``False`` -- since it is resolved directly, never via a PATH
    search, regardless of what any ``env`` layer did to PATH. For a
    genuinely bare *token*, ``True`` exactly when the traversal's own
    ``path_cleared`` state survived to the very end of *argv*'s ``env``
    prefix with no later real ``PATH=`` override to un-clear it -- the
    same ``path_cleared and not env_path`` condition
    :func:`_traverse_env_and_launcher_prefix`'s own docstring states.

    Deliberately re-derives the traversal from *argv* rather than
    threading a new return value through :func:`strip_launchers` itself
    (whose stripped-argv-only return shape several other callers already
    depend on unchanged) -- callers pass the SAME ``argv`` they already
    called :func:`strip_launchers`/:func:`msvc_driver_token` with, so this
    is one extra, cheap traversal of an already-short prefix, not a
    second, independently-drifting implementation of the traversal logic
    itself (both call into the identical :func:`_traverse_env_and_launcher_prefix`).
    """
    if "/" in token or "\\" in token:
        return False
    _i, _chdir, env_path, path_cleared = _traverse_env_and_launcher_prefix(list(argv))
    return path_cleared and not env_path


def effective_directory(argv: list[str], directory: str | None) -> str | None:
    """The real directory the wrapped command executes from, once any
    leading ``env -C``/``--chdir`` prefix in *argv* is accounted for
    (Codex review, Finding 10, fresh evidence).

    GNU ``env -C DIR`` genuinely chdirs the whole invoked process before it
    ``execvp()``s the real command -- so for ``env -C build clang -iquote
    ../include -c x.cc`` recorded with ``directory=/work``, the compiler
    ACTUALLY runs from ``/work/build``, not bare ``/work``. Round 23's
    Finding 7/8 fixes folded this into the STRING VALUES
    :func:`strip_launchers` returns, but two real L4-replay call sites
    (``source_extractors.castxml.CastxmlExtractor.extract``,
    ``source_extractors.clang.ClangSourceExtractor``) never consumed that
    correction at all: both still spawn their subprocess with
    ``cwd=compile_unit.directory`` (the raw, un-chdir'd directory)
    unchanged, and ``replayed_tokens()``'s own ``-iquote``/``-I``/etc.
    scan (:func:`_scan_argv_for_extra_flags`) deliberately reads
    ``compile_unit.argv`` VERBATIM -- so those RAW relative flag values,
    unmodified, get handed to a subprocess running from the WRONG
    directory. Once the subprocess ``cwd`` itself is corrected via this
    function, those raw, unmodified relative flag values resolve
    correctly on their own (the same way real ``env -C`` makes them work
    for the real compiler) -- no separate flag-value folding is needed on
    this path, only the ``cwd`` fix.

    Returns *directory* unchanged when there is no ``env -C``/``--chdir``
    prefix in *argv* at all (the common case, and every non-``env``
    compile unit) -- a complete no-op. Returns ``None``/falsy *directory*
    unchanged too (nothing to compose against).
    """
    if not directory:
        return directory
    _i, chdir, _env_path, _path_cleared = _traverse_env_and_launcher_prefix(list(argv))
    if not chdir:
        return directory
    # `chdir` is read straight from possibly-redacted evidence (ADR-032
    # D7) -- unredact any `~` home placeholder before composing, the same
    # treatment every other path-shaped `CompileUnit`-derived value in
    # this module already gets (`unredact_home`'s own docstring).
    chdir = unredact_home(chdir)
    if is_absolute_path_token(chdir):
        return normalize_path_token(chdir)
    return join_path_token(directory, chdir)


def expand_env_split_prefixes(argv: list[str]) -> list[str]:
    """Return a COPY of *argv* with every leading ``env -S``/``--split-string``
    prefix's value expanded and spliced in place -- without stripping the
    ``env``/launcher tokens themselves (Codex review, Finding 2, fresh
    evidence).

    ``strip_launchers`` already performs this exact splicing internally
    (via :func:`_skip_env_prefix`) as ONE step of unwrapping the whole
    ``env``/launcher prefix down to the bare driver. This function exposes
    just that one step's *result* -- the fully-expanded token stream, still
    carrying its ``env``/launcher prefix -- for a caller that needs to scan
    and INDEX INTO the real, post-expansion argv directly, not merely learn
    which single token is the driver.

    **Why this is needed as its own primitive**: an index computed by
    subtracting lengths between the ORIGINAL (unexpanded) argv and
    ``strip_launchers``'s stripped result
    (``len(original_argv) - len(strip_launchers(original_argv))``) silently
    breaks whenever ``-S``/``--split-string`` changes the token count -- the
    two lists no longer correspond position-for-position at all, so the
    subtraction can land on any token, including one that never existed in
    the original argv's own prefix. Confirmed with a minimal repro: for
    ``["env", "-S", "clang-cl /c x.cc"]`` (length 3), the expanded/stripped
    result is also length 3 (``["clang-cl", "/c", "x.cc"]``), so the
    subtraction spuriously computes index 0 -- ``argv[0]`` (``"env"``), not
    the real driver -- purely by length coincidence, not because index 0 is
    actually correct. See
    :func:`~abicheck.buildsource.adapters.adapter_shared._executable_token_positions`
    for the caller this primitive fixes: it now computes its own
    length-subtraction index BETWEEN two results of calling
    :func:`strip_launchers` on THIS function's return value, so both sides
    of that subtraction are guaranteed to correspond to the same expanded
    list.

    A nested ``env -S '... env -C build ...'`` (the split string itself
    happens to start with another ``env`` invocation) is expanded
    recursively -- the loop below re-checks ``argv[i]`` after each
    expansion and keeps unwrapping as long as progress is still being made.

    **Also follows an interleaved compiler-launcher, not just a leading
    ``env`` (Codex review, Finding 9, fresh evidence).** An earlier
    revision only ever checked position 0 -- for ``sccache env -S
    'clang-cl /c x.cc'`` (a launcher preceding a NESTED ``env -S``),
    ``argv[0]`` is ``"sccache"``, not ``"env"``, so the loop's own
    condition never even entered and the ``-S`` value was left completely
    unexpanded. This function's one caller,
    :func:`~abicheck.buildsource.adapters.adapter_shared._msvc_driver_scan`, then
    scanned the raw, unexpanded 4-token argv -- seeing neither a real
    ``/c`` marker nor a ``clang-cl``-basename token at any position --
    and returned ``(False, None)`` for a genuinely MSVC-dialect command.
    Mirrors :func:`strip_launchers`'s OWN interleaved traversal exactly
    (alternating an ``env``-prefix attempt with a compiler-launcher-prefix
    attempt, looping while either one still makes progress) so ``-S`` is
    expanded at every layer this module already knows how to unwrap a
    prefix at -- just without :func:`strip_launchers`'s own final
    slicing/env-context-folding step, since this function's whole
    contract is "return the full, still-prefixed, EXPANDED token stream"
    rather than "return the bare driver and its own arguments".
    """
    argv = list(argv)
    _traverse_env_and_launcher_prefix(argv)
    return argv

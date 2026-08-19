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

"""Shared compile-context → argv helpers for source ABI extractors (ADR-030 D2).

Every extractor backend must replay a translation unit under the *same* compile
context the real build used — same compiler emulation, language standard,
defines, include paths, forced includes, sysroot/target, and ABI-relevant flags
(ADR-030 D2). castxml (phase 2) and clang (phase 5) need identical logic for the
fiddly parts — unwrapping ``env``/``ccache``/``sccache`` launchers, detecting MSVC mode
from a (possibly Windows, possibly cross) compiler path, carrying argv-only
forced includes, and reversing the redaction policy's ``~`` home placeholder for
the replay only (ADR-032 D7). Keeping that here means one tested implementation,
not one per backend.

Pure and tool-independent: nothing here shells out.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
import shlex
import shutil
from collections.abc import Sequence
from pathlib import PurePosixPath, PureWindowsPath

from ...header_utils import (
    is_msvc_driver_stem,
    match_gnu_forced_include,
    match_msvc_forced_include,
)
from ..build_evidence import CompileUnit

#: Languages that make the GNU fallback compiler ``g++`` rather than ``gcc``.
CXX_LANGS = frozenset({"cxx", "c++", "cpp"})
# The CL-mode driver vocabulary this module once owned now lives in
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
#: Preprocessor macro define/undef option prefixes. Their *values* reach the
#: compiler verbatim (argv, no shell expansion), so a literal ``~`` in e.g.
#: ``-DDEFAULT_DIR=~/app`` must NOT be home-expanded during replay — unlike the
#: path operands (includes/sysroot/source), which carry redacted home prefixes.
MACRO_DEFINITION_PREFIXES = ("-D", "-U", "/D", "/U")
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
STRUCTURED_TOOLCHAIN_FLAG_PREFIXES = ("--sysroot", "-isysroot", "--target", "-target")

#: ABI-relevant flags whose value is a *separate*, following argv token
#: rather than a combined ``=``/immediate-suffix spelling -- confirmed
#: against a real ``clang -cc1 --help``: ``-target-abi <value>``,
#: ``-target-cpu <value>``, ``-target-feature <value>``,
#: ``-target-linker-version <value>`` are all two-token forms (unlike
#: ``-target-sdk-version=<value>``, already combined). Each shares the
#: ``-target`` prefix already matched by
#: ``adapters.base.ABI_RELEVANT_FLAG_PREFIXES``, so without special handling
#: there a naive prefix match captures only the flag token itself and
#: silently discards its operand -- a real, information-losing bug (P2
#: review, ``discussion_r3787772666``): two compile units disagreeing only on
#: e.g. ``-target-abi aapcs`` vs. ``-target-abi aapcs16`` read as identical
#: once the operand was dropped, and any caller replaying the bare,
#: valueless survivor got a syntactically incomplete command.
#:
#: Deliberately **not** extended to the bare ``-target``/``--sysroot``/
#: ``-isysroot`` split forms those same three prefixes also match: unlike
#: the four flags here, each of those already has its own dedicated
#: structured ``CompileUnit`` field (``target_triple``/``sysroot``) derived
#: by a separate, correct parse of the same argv (``build_context.py``), so
#: their raw split-form survivor carries no additional information.
#:
#: Normalized to one internal ``<flag>=<value>`` token, not two separate
#: list entries -- ``-target-abi=<value>`` is not real clang syntax (unlike
#: ``-D<KEY>=<VALUE>``, which genuinely is both split- and combined-form
#: valid), it is a purely-internal, round-trippable encoding produced by
#: ``adapters.base.extract_abi_relevant_flags`` and decoded back into real
#: argv token(s), for every consumer, by :func:`split_operand_survivor`
#: below.
#:
#: **Every one of these four is a cc1-only flag, and a normal Clang** *driver*
#: **invocation never passes one bare -- each is individually wrapped in its
#: own ``-Xclang``, not just written after a single leading ``-Xclang``**
#: (P2 review, ``discussion_r3788073752``, fresh evidence): confirmed with the
#: installed CLI that ``clang -cc1 --help`` documents ``-target-abi <value>``,
#: while ``clang -target-abi aapcs`` is rejected outright ("unknown argument
#: '-target-abi'; did you mean '-Xclang -target-abi'"), and the real,
#: supported spelling is ``-Xclang -target-abi -Xclang aapcs`` -- each token
#: individually prefixed. **Both real argv shapes now normalize to the SAME
#: internal ``<flag>=<value>`` encoding** (P2 review, "Canonicalize
#: equivalent cc1 survivor spellings", fresh evidence): an earlier revision
#: had ``extract_abi_relevant_flags`` distinguish the wrapped shape with a
#: leading ``-Xclang `` marker (see :data:`_XCLANG_WRAPPED_ABI_FLAG_MARKER`)
#: so :func:`split_operand_survivor` could tell which of the two real argv
#: shapes (``["-target-abi", "<value>"]`` vs. ``["-Xclang", "-target-abi",
#: "-Xclang", "<value>"]``) to reconstruct at replay time -- but
#: :func:`split_operand_survivor` reconstructs the identical
#: ``-Xclang``-wrapped output for either encoding regardless (see its own
#: docstring), so the distinction bought nothing at decode time while
#: actively breaking two consumers that compare/key on this raw string
#: *without* decoding it first (``header_compile_context.
#: _EffectiveContextSignature``, :func:`~abicheck.buildsource.adapters.base.
#: derive_build_options`) -- two semantically-equivalent units captured via
#: different argv shapes compared unequal for those. The marker is still
#: recognized on *decode* (:data:`_XCLANG_WRAPPED_ABI_FLAG_MARKER`,
#: :func:`split_operand_survivor`, :func:`is_split_operand_abi_flag_survivor`)
#: purely for backward compatibility with evidence packs persisted by an
#: earlier revision that did emit it; the producer never emits it now.
#:
#: Lives here rather than in ``adapters.base`` -- where it is *produced*, by
#: ``extract_abi_relevant_flags`` -- because :func:`_carry_abi_relevant_flags`
#: below (the L4 source-replay decode path, P2 review "Decode normalized cc1
#: flags in every replay path") needs it too, and ``adapters.base`` already
#: imports :func:`strip_launchers` from this module at its own top level
#: (:func:`~abicheck.buildsource.adapters.base._executable_token_positions`)
#: -- a reverse ``_argv -> adapters.base`` import would be a genuine import
#: cycle (the ai-readiness ``import-cycle-growth`` gate walks the full AST,
#: function-local imports included, so a deferred import does not avoid it
#: either). Keeping the encoding's constants and decode function in this
#: leaf, tool-independent module -- which both replay paths already depend
#: on -- keeps the dependency a one-way DAG: ``adapters.base`` imports from
#: here, never the reverse. ``adapters.base`` re-exports this under its own
#: (legacy, private) ``_SPLIT_OPERAND_ABI_FLAGS`` name for any existing
#: reader of that spelling.
SPLIT_OPERAND_ABI_FLAGS = frozenset(
    {
        "-target-abi",
        "-target-cpu",
        "-target-feature",
        "-target-linker-version",
    }
)

#: The literal marker an earlier revision of
#: ``adapters.base.extract_abi_relevant_flags`` used to prepend to a
#: ``-Xclang``-wrapped split-operand flag's internal encoding, before both
#: capture forms were canonicalized onto one identical encoding (see
#: :data:`SPLIT_OPERAND_ABI_FLAGS`'s own docstring, "Canonicalize
#: equivalent cc1 survivor spellings"). The producer never emits it now;
#: kept here purely so :func:`split_operand_survivor`/
#: :func:`is_split_operand_abi_flag_survivor` still decode it correctly if
#: it appears in an evidence pack persisted by an earlier revision.
_XCLANG_WRAPPED_ABI_FLAG_MARKER = "-Xclang "


def split_operand_survivor(flag: str) -> list[str]:
    """Expand one ``cu.abi_relevant_flags`` survivor into its literal argv token(s).

    ``adapters.base.extract_abi_relevant_flags`` normalizes a genuinely
    split two-token flag (``-target-abi <value>`` and its siblings in
    :data:`SPLIT_OPERAND_ABI_FLAGS`) into one internal ``<flag>=<value>``
    token -- see that set's own docstring for why this is a purely-internal
    encoding rather than real clang syntax (unlike ``-D<KEY>=<VALUE>``, which
    is valid either way). This is the one place that encoding is
    reconstructed into the real, separate argv tokens (``["-target-abi",
    "<value>"]``) a real compiler invocation needs -- every other flag is
    returned unchanged as a single-element list.

    A cc1-only split-operand flag is, in real usage, always individually
    wrapped in ``-Xclang`` on both sides (``-Xclang -target-abi -Xclang
    aapcs``, never the bare two-token form -- confirmed against a real
    ``clang -cc1 --help``/driver error). ``extract_abi_relevant_flags``
    normalizes that wrapped shape onto the SAME canonical
    ``<flag>=<value>`` encoding as the bare capture; an earlier revision
    marked it with a leading ``-Xclang `` prefix (see
    :data:`_XCLANG_WRAPPED_ABI_FLAG_MARKER`, still decoded here for
    backward compatibility only). Either encoding is reconstructed into the
    full four-token ``["-Xclang", "<flag>", "-Xclang", "<value>"]`` form --
    a bare, unwrapped ``-target-abi aapcs`` is not valid
    on a normal ``clang`` driver invocation (rejected with "unknown
    argument"), so replaying the wrapped survivor without its ``-Xclang``
    forwarding would produce an invalid command.

    **Shared, not per-consumer** (P2 review, "Decode normalized cc1 flags in
    every replay path", fresh evidence): this decode was originally
    L2-header-path-specific, living only in ``header_compile_context.
    _split_operand_survivor``. The identical normalized encoding also flows
    through L4 source replay (:func:`replay_extra_flags` /
    :func:`_carry_abi_relevant_flags` below), which used to read the raw
    ``abi_relevant_flags`` survivors verbatim -- so a TU whose real build
    recorded a ``-Xclang``-wrapped ``-target-abi`` reached L4 replay as one
    malformed argv token (``-Xclang -target-abi=aapcs``, not four real
    tokens), and the bare-encoding sibling was separately dropped outright by
    :data:`STRUCTURED_TOOLCHAIN_FLAG_PREFIXES` (it starts with ``-target``,
    the same structured-field prefix that filter drops as redundant for the
    *unrelated*, already-structured ``-target``/``--sysroot``/``-isysroot``
    survivors). This single implementation is what both the L2
    (``header_compile_context._split_operand_survivor``, a thin re-export)
    and L4 (:func:`_carry_abi_relevant_flags`) consumers now share, instead
    of drifting independently.

    **Both the bare and the ``-Xclang``-wrapped internal encodings decode to
    the SAME ``-Xclang``-wrapped output (P2 review, "Forward bare cc1 flags
    through the replay driver", fresh evidence).** The two internal
    encodings this function decodes record two different *original capture*
    shapes -- the bare form (``<flag>=<value>``) is what
    ``adapters.base.extract_abi_relevant_flags`` recorded for a direct
    ``clang -cc1 -target-abi aapcs`` invocation (``-cc1`` mode accepts these
    flags bare, no ``-Xclang`` wrapping needed), while the ``-Xclang
    <flag>=<value>`` form is what it recorded when the *original* command
    was already an ordinary-driver invocation forwarding the flag via
    ``-Xclang`` on both sides. But every consumer of this function's return
    value (:func:`~abicheck.buildsource.header_compile_context._context_flags`
    for L2, :func:`_carry_abi_relevant_flags` below for L4) replays through
    an **ordinary Clang driver, never ``-cc1`` directly** -- confirmed
    empirically: installed ``clang -cc1 --help`` documents bare
    ``-target-abi <value>``, but ``clang -target-abi aapcs`` (the ordinary
    driver) rejects it outright ("unknown argument '-target-abi'; did you
    mean '-Xclang -target-abi'"), requiring the ``-Xclang``-wrapped
    four-token form regardless of which shape the flag was originally
    captured in. Reconstructing the bare two-token form for the
    bare-encoding branch (the previous behavior) therefore produced a
    command that fails against the replay driver even though the *decode*
    itself was correct -- the reconstructed shape simply doesn't match the
    shape the replay driver accepts. Both branches now converge on the same
    ``-Xclang``-wrapped output, computed from whichever encoding matched
    (the ``-Xclang `` marker is stripped first, if present, before parsing
    ``name``/``value``, so the two branches share one reconstruction).
    """
    remainder = flag.removeprefix(_XCLANG_WRAPPED_ABI_FLAG_MARKER)
    name, sep, value = remainder.partition("=")
    if sep and name in SPLIT_OPERAND_ABI_FLAGS:
        return ["-Xclang", name, "-Xclang", value]
    return [flag]


def is_split_operand_abi_flag_survivor(flag: str) -> bool:
    """True when *flag* is one of :data:`SPLIT_OPERAND_ABI_FLAGS`'s own
    normalized survivor encodings (P2 review, ``discussion_r3788...``
    follow-up, fresh evidence).

    ``adapters.base.extract_abi_relevant_flags`` normalizes a genuinely
    split two-token flag like ``-target-abi <value>`` (and its
    ``-Xclang``-wrapped real-world spelling, ``-Xclang -target-abi -Xclang
    <value>``) into one internal ``<flag>=<value>``/``-Xclang
    <flag>=<value>`` token -- see :data:`SPLIT_OPERAND_ABI_FLAGS`'s own
    docstring for the full reasoning. Every one of these four flags
    (``-target-abi``/``-target-cpu``/``-target-feature``/
    ``-target-linker-version``) shares the ``-target`` prefix
    ``adapters.base._TOOLCHAIN_PATH_FLAG_PREFIXES`` matches to drop a raw
    ``-target``/``--sysroot``/``-isysroot`` survivor as fully redundant with
    the structured ``target_triple``/``sysroot`` fields -- but unlike those,
    none of these four is represented by any structured ``CompileUnit``
    field at all, so dropping one silently discards real, independent
    ABI-relevant information (a real regression this predicate exists to
    prevent -- see ``adapters.base._add_generic_flag_option``'s own call
    site, and :func:`_carry_abi_relevant_flags` below for the L4 sibling).
    """
    candidate = flag.removeprefix(_XCLANG_WRAPPED_ABI_FLAG_MARKER)
    name, sep, _value = candidate.partition("=")
    return sep != "" and name in SPLIT_OPERAND_ABI_FLAGS


#: The forced-include option vocabulary and its two matchers now live in
#: ``abicheck.header_utils`` (the leaf that already owns this codebase's
#: include-flag vocabulary, and which both this module and
#: ``buildsource.header_compile_context`` already sit above), so the L4 replay
#: path here and the L2 header-parse path there recognize exactly the same
#: spellings from one implementation. Re-bound to the historical private names
#: so the call sites below read unchanged.
_match_gnu_forced_include = match_gnu_forced_include
_match_msvc_forced_include = match_msvc_forced_include
#: GNU include-search options that take a directory operand and are NOT
#: normalized into the structured ``include_paths``/``system_include_paths``
#: buckets (those cover ``-I``/``-isystem`` only). Dropping them makes the
#: extractor search a different set of directories than the real compile (Codex
#: review #335). Both the separate (``-iquote dir``) and joined (``-iquote/dir``)
#: spellings are carried through.
#: (https://gcc.gnu.org/onlinedocs/gcc/Directory-Options.html)
_GNU_INCLUDE_SEARCH_OPTS = frozenset({"-iquote", "-idirafter"})


def unredact_home(value: str) -> str:
    """Expand a redacted home placeholder (``~``) back to the real home dir.

    The evidence redaction policy (ADR-032 D7) rewrites the user's home prefix
    to ``~`` wherever it appears before persisting paths/argv. ``subprocess``
    does not expand ``~`` (no shell), so replaying a redacted ``CompileUnit``
    would treat ``~/...`` / ``-I~/...`` as literal paths and fail. Reverse the
    substitution for the replay only (persisted values stay redacted).

    Only a ``~`` that stands in for a home *directory* is expanded: the
    placeholder is always either the whole token or immediately followed by a
    path separator. A ``~`` followed by anything else is untouched, so Windows
    8.3 short names such as ``RUNNER~1`` are never mangled.
    """
    if "~" not in value:
        return value
    home = os.path.expanduser("~")
    if not home or home == "~":
        return value
    return re.sub(r"~(?=[\\/]|$)", lambda _m: home, value)


def split_public_roots(roots: Sequence[str]) -> tuple[list[str], list[str]]:
    """Partition public-header roots into ``(file_roots, dir_roots)``.

    The CLI accepts a public header *file or directory* (``--headers include/``).
    ``provenance.build_public_set`` needs files and directories in separate
    arguments — a directory passed as a "header" file never suffix-matches a decl
    under it (``include`` vs ``include/api.h``), so the whole public include tree
    would be classified non-public and dropped (Codex review #339, P2). A root is
    a directory when it ends in a path separator or resolves to a directory on
    disk (un-redacting a ``~`` home placeholder first, ADR-032 D7). The original
    (unexpanded) root string is kept for segment matching, which compares against
    the paths the compiler actually reports.
    """
    files: list[str] = []
    dirs: list[str] = []
    for root in roots:
        if not root:
            continue
        expanded = os.path.expanduser(unredact_home(root))
        if root.endswith(("/", "\\")) or os.path.isdir(expanded):
            dirs.append(root)
        else:
            files.append(root)
    return files, dirs


def resolve_read_files(files: set[str], directory: str) -> list[str]:
    """Absolute, de-duplicated read-file paths resolved against ``directory``.

    An extractor records the files it read (``SourceAbiTu.read_files``) for the
    per-TU cache dependency set (ADR-030 D8). A compiler emits *relative* paths
    for headers found via a relative ``-I``, which the cache — running in a
    possibly different CWD — could not otherwise read, silently dropping the
    dependency. Resolve each against the TU's build directory (un-redacting a
    ``~`` home placeholder first, ADR-032 D7) so the path matches the CWD the
    tool actually ran in.
    """
    base = unredact_home(directory) if directory else ""
    out: set[str] = set()
    for f in files:
        path = os.path.expanduser(unredact_home(f))
        if not os.path.isabs(path) and base:
            path = os.path.join(base, path)
        out.add(os.path.normpath(path))
    return sorted(out)


def basename(path: str) -> str:
    """Final path component, splitting on both ``/`` and ``\\`` (host-independent).

    ``Path(path).name`` on POSIX does not treat ``\\`` as a separator, so a
    Windows compiler path from a cross/off-Windows compile database
    (``C:\\VS\\bin\\cl.exe``) would otherwise return the whole string and miss
    MSVC-mode detection.
    """
    return re.split(r"[\\/]", path)[-1]


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
    """
    return shlex.split(value)


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
    fell back to ``argv[0]`` (``"env"``), and ``dumper_clang._resolve_clang_bin``
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
      every downstream ``shutil.which``-style lookup (``_resolve_clang_bin``,
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
            # GNU `env`'s option-parsing terminator: everything after `--`
            # is the command and its arguments, even if it looks like a
            # flag or a `NAME=VALUE` assignment (Codex review, Finding 6,
            # fresh evidence, confirmed against a real installed `env
            # --help`). Consume the terminator itself and stop scanning --
            # the next token is unconditionally the command.
            i += 1
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
        if arg.startswith(_ENV_OPTIONAL_SIG_FLAG_PREFIXES):
            i += 1
            continue
        if arg in _ENV_CHDIR_FLAGS and i + 1 < len(argv):
            chdir = argv[i + 1]
            i += 2
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
    unchanged, and ``replay_extra_flags()``'s own ``-iquote``/``-I``/etc.
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
    :func:`~abicheck.buildsource.adapters.base._executable_token_positions`
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
    :func:`~abicheck.buildsource.adapters.base._msvc_driver_scan`, then
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


#: Matches a Windows drive-letter-and-separator sequence (``C:\`` / ``C:/``)
#: at the START of the string it is tested against -- used by
#: :func:`_split_posix_path_value` to recognize the ONE legitimate reason a
#: literal ``:`` inside a POSIX-style ``:``-joined PATH value must NOT be
#: treated as a separator: a single ``PATH`` entry that is itself an
#: absolute Windows path.
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _split_posix_path_value(value: str) -> list[str]:
    """Split *value* on a LITERAL ``:`` -- POSIX ``env``'s own, fixed
    ``PATH`` entry separator, regardless of the analyzing host's own
    ``os.pathsep`` (Codex review, live Windows CI signal, fresh evidence;
    see :func:`_resolve_env_path_entries`'s own docstring for why
    host-native ``os.pathsep`` is wrong here at all).

    **Does NOT split a Windows drive-letter colon** (``C:\\...``/
    ``C:/...``): a genuine POSIX-recorded multi-entry value never contains
    one (POSIX has no drive letters), but this module's own tests --
    and, plausibly, real evidence gathered by an `env` invocation captured
    while a build itself ran under Windows -- legitimately use a single
    Windows-absolute-path ``PATH`` entry, which a blind "split on every
    ``:``" would otherwise cut in two (``C:\\Users\\x`` ->
    ``["C", "\\Users\\x"]``). Detected the same way ``is_absolute_path_token``
    already recognizes a Windows-absolute token (a single ASCII letter
    immediately followed by ``:`` and a path separator) -- checked
    positionally against each candidate colon (exactly one preceding,
    unsplit character that is itself a letter), not merely "the string
    somewhere contains a drive-letter pattern", so a colon anywhere else
    in the same value still splits normally.
    """
    parts: list[str] = []
    current: list[str] = []
    n = len(value)
    for i, ch in enumerate(value):
        is_drive_colon = (
            ch == ":"
            and len(current) == 1
            and current[0].isalpha()
            and i + 1 < n
            and value[i + 1] in "\\/"
        )
        if ch == ":" and not is_drive_colon:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _resolve_env_path_entries(
    env_path: str, chdir: str | None, directory: str | None
) -> list[str]:
    """Compose each *relative* entry of an ``env``-supplied ``PATH``
    override against the directory GNU ``env`` actually executes the
    command from, before it is handed to :func:`shutil.which` (Codex
    review, ``_argv.py:576``, Finding 3, fresh evidence).

    GNU ``env`` applies ``-C DIR`` (chdir) *before* it ``execvp()``s the
    command, and ``execvp`` searches a relative ``PATH`` entry relative to
    the process's *current* (i.e. already-chdir'ed) working directory --
    not abicheck's own CWD. For ``env -C build PATH=../tool clang-cl ...``
    recorded for a compile unit whose own ``directory`` is ``/work``, GNU
    ``env`` searches ``/work/build/../tool`` (== ``/work/tool``), never
    ``<abicheck's own CWD>/../tool``. Without this composition,
    :func:`_apply_env_context`'s ``shutil.which(token, path=env_path)`` call
    resolved a relative entry against abicheck's own process CWD instead --
    leaving the driver bare (not found) or, worse, silently resolving to a
    *different* executable that happens to exist at the wrong, un-chdir'ed
    location.

    *directory* is only available to callers that already have the full
    :class:`~abicheck.buildsource.build_evidence.CompileUnit` in scope
    (:func:`pick_compiler_binary` here) -- ``None`` for every other,
    directory-agnostic caller of :func:`strip_launchers`, which leaves an
    entry composed against *chdir* alone (relative to an unknown base, the
    pre-existing best-effort behavior) rather than fabricating a directory
    that was never supplied.

    An already-absolute entry is passed through unchanged -- unambiguous
    regardless of *chdir*/*directory*. Uses :func:`join_path_token`, not
    host-native ``os.path.join``, for the same cross-OS-evidence reasons as
    every other join in this module.

    **An EMPTY entry means the effective current directory, per POSIX/GNU
    ``PATH`` semantics** (Codex review, ``_argv.py:708``, Finding 3, fresh
    evidence): ``PATH=:`` or ``PATH=/a::/b`` both contain a component
    between two colons (or a leading/trailing colon) that names no
    directory at all -- POSIX and GNU coreutils both document this as
    equivalent to ``.``, the shell's/process's own current directory (here,
    *base* -- ``directory`` composed with any ``-C``/``--chdir``, exactly
    the value a genuine ``.`` entry already resolves against). The previous
    behavior left an empty component unchanged, so it fell through to
    :func:`_apply_env_context`'s ``shutil.which(token, path=env_path)`` with
    an empty ``PATH`` entry -- which ``shutil.which`` resolves against
    ITS OWN caller's (abicheck's) process CWD, not the compile unit's
    effective directory, the identical wrong-CWD failure mode this whole
    function exists to prevent for a genuinely relative entry.

    **Splits on a LITERAL ``:``, never host-native ``os.pathsep`` (live
    Windows CI signal, fresh evidence).** A recorded ``env``-supplied
    ``PATH=`` value is POSIX ``env``'s own evidence text -- colon-separated
    by that utility's OWN, fixed convention, regardless of which host OS
    later ANALYZES this evidence. ``os.pathsep`` is host-native (``;`` on
    Windows), so splitting on it there left a colon-separated evidence
    string as one single, unsplit, bogus entry -- confirmed empirically: a
    Windows CI run split ``":"`` on ``";"`` (no match) into ``[":"]``, one
    literal-colon "entry", corrupting every downstream join. Returns a
    LIST of composed directory candidates now (not a ``:``-joined string)
    for the identical reason one level up: :func:`_apply_env_context`
    used to hand the joined string straight to a single
    ``shutil.which(token, path=...)`` call, which ALSO splits its own
    ``path`` argument on host-native ``os.pathsep`` internally -- so even a
    correctly ``:``-joined POSIX-style string was mis-split again by
    ``shutil.which`` itself on Windows (its own drive-letter colon,
    ``C:\\...``, made this doubly ambiguous: splitting on a literal ``:``
    there would also wrongly cut a drive letter in two). Returning a list
    and letting the caller search each directory with its own
    single-directory ``shutil.which`` call sidesteps ANY pathsep
    representation question entirely -- a lone directory string handed to
    ``shutil.which`` is never split at all, regardless of what characters
    (``:`` or otherwise) it happens to contain.
    """
    if not directory:
        return _split_posix_path_value(env_path)
    base = directory
    if chdir:
        base = (
            join_path_token(directory, chdir)
            if not is_absolute_path_token(chdir)
            else chdir
        )
    entries = _split_posix_path_value(env_path)
    return [
        entry
        if is_absolute_path_token(entry)
        else base
        if not entry
        else join_path_token(base, entry)
        for entry in entries
    ]


def _apply_env_context(
    token: str,
    chdir: str | None,
    env_path: str | None,
    directory: str | None = None,
) -> str:
    """Fold a leading ``env -C DIR``/``env PATH=...`` prefix's effect into
    *token*, the driver argv token :func:`strip_launchers` is about to
    return (P2 review round 19, both findings, fresh evidence).

    Chosen deliberately as the ONE place both effects are resolved, rather
    than threading ``chdir``/``env_path`` as new fields through every one of
    :func:`strip_launchers`'s several call sites
    (``header_compile_context._derived_gcc_path``, ``adapters.base``'s
    driver-position scan, ``cc_wrapper.py``, ``include_graph.py``,
    ``build_context.py``): folding the effect into the token itself here
    means every existing caller gets the corrected token for free, with no
    signature change and no new field for a caller that doesn't care about
    ``env`` to thread through unused.

    **Chdir** (``-C DIR``): only meaningful for a *relative, separator-
    bearing* token -- a bare PATH name (``clang-cl``, matched by
    :func:`is_msvc_mode` and friends via basename lookup) is looked up on
    ``PATH``, never relative to any directory, and an already-absolute
    token needs no adjustment. For a relative, path-shaped token, DIR is
    folded onto it via a plain lexical join+normalize (``os.path.join`` +
    ``os.path.normpath``, matching ``header_compile_context.
    _resolve_driver_token``'s own lexical, symlink-blind convention for
    every other redacted/relative ``CompileUnit`` path field) -- the result
    is still a *relative* path (DIR is not itself resolved against
    anything here), so the existing downstream join against
    ``cu.directory`` in ``_resolve_driver_token`` composes correctly
    without needing to know an ``env -C`` prefix was ever involved: joining
    ``build`` onto ``../llvm/bin/clang-cl`` and normalizing collapses to
    ``llvm/bin/clang-cl``, which ``_resolve_driver_token`` then joins onto
    ``cu.directory`` exactly as it already does for any other relative
    driver token.

    **PATH** (``PATH=...``): only meaningful for a *bare* token (no path
    separator at all) -- a path-shaped token is unambiguous regardless of
    which ``PATH`` would have been searched. Resolved immediately via a
    ``shutil.which(token, path=<one directory>)`` call PER composed
    candidate directory (see :func:`_resolve_env_path_entries`'s own
    docstring for why one call per directory, not one call with a
    pathsep-joined string, is what a Windows-analysis-host correctness fix
    required) into an absolute path when found, so no downstream consumer
    needs to know the env override existed at all; left unchanged (the
    pre-fix, bare-name behavior) when the name isn't found in any
    candidate directory either -- the same conservative, no-worse-than-
    before fallback every other best-effort resolution in this module
    already uses.

    A token that is itself a flag (starts with ``-``) -- meaning
    :func:`strip_launchers` found no real driver token at all -- is left
    completely untouched; neither effect applies to a non-existent driver.

    **Host-independent absolute/join grammar (Codex review, "Recognize
    foreign absolute driver paths", fresh evidence).** The chdir branch
    below used to check ``os.path.isabs(token)`` and join with plain
    ``os.path.join``/``os.path.normpath`` -- both host-native. Evidence
    collected on one OS and analyzed on another (a POSIX build's compile
    database inspected from a Windows CI runner, or the reverse) means
    *token* is not always spelled in the host's own grammar: a POSIX
    absolute token like ``/opt/llvm/bin/clang-cl`` reads as *relative* to
    ``os.path.isabs`` on Windows, so it was wrongly re-joined onto *chdir*
    a second time, and a relative token joined via host-native
    ``os.path.join`` on Windows produced a backslash-separated result even
    when *chdir*/*token* were both recorded in POSIX form. Delegates to
    :func:`is_absolute_path_token`/:func:`join_path_token`, which check and
    compose using the token's own separator grammar instead of the host's.
    """
    if not token or token.startswith("-"):
        return token
    if "/" in token or "\\" in token:
        if chdir and not is_absolute_path_token(token):
            return join_path_token(chdir, token)
        return token
    if env_path:
        for candidate_dir in _resolve_env_path_entries(env_path, chdir, directory):
            resolved = shutil.which(token, path=candidate_dir)
            if resolved:
                return resolved
    return token


#: COMBINED-form (single-token, ``-Ivalue``/``/Ivalue``) path-bearing option
#: prefixes recognized by :func:`_fold_chdir_into_operands` (Codex review,
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
    "/I",
    "/FI",
)
#: ``=``-joined (``--sysroot=value``) path-bearing option prefixes recognized
#: by :func:`_fold_chdir_into_operands`, alongside the combined-form set above.
_CHDIR_FOLD_EQUALS_PATH_PREFIXES = ("--sysroot=",)
#: SEPARATE-form (``-I value``, two tokens) path-bearing option spellings
#: recognized by :func:`_fold_chdir_into_operands` -- the bare flag itself
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
        "--sysroot",
        "/I",
        "/FI",
    }
)
#: SEPARATE-form value-taking flags whose value is confirmed NOT a path --
#: the general form of the macro-flag exemption Finding 11 established,
#: extended to every other such flag this module can positively confirm
#: (Codex review round 25, Finding 2, fresh evidence). The old scan treated
#: ANY non-flag-shaped token as a positional path operand, which corrupts a
#: value like ``-x c++`` into ``-x build/c++`` -- ``c++`` is a language
#: name, not a path, exactly the same failure shape Finding 11 already
#: fixed for ``-D``/``-U`` macro values, just for a different flag family
#: that scan never accounted for.
#:
#: * ``-x`` -- GCC/Clang's ``-x <language>`` (``c``, ``c++``, ``assembler``,
#:   ``none``, …), never a filesystem path.
#: * ``-target`` -- Clang's bare (non-``=``, non-``--target``) driver-level
#:   target-triple spelling (``-target x86_64-linux-gnu``); the triple
#:   string, not a path. Deliberately narrower than
#:   :data:`STRUCTURED_TOOLCHAIN_FLAG_PREFIXES`'s own ``-target`` handling
#:   (a *different* concern, in a different function, dropping an
#:   already-structured survivor from ``abi_relevant_flags`` entirely) --
#:   this set only stops the VALUE from being misread as a path here.
#: * ``-arch`` -- Apple's universal-build architecture selector
#:   (``-arch x86_64``/``-arch arm64``), an architecture name, not a path.
#: * ``-Xclang`` -- forwards its own following token verbatim to ``-cc1``;
#:   the forwarded token is itself an arbitrary cc1 flag/value, not
#:   generally a path (and folding it here would be independently wrong for
#:   the ``-Xclang -target-abi -Xclang aapcs`` shape :data:`SPLIT_OPERAND_
#:   ABI_FLAGS` already handles through a completely different mechanism).
#:
#: Every other, unrecognized value-taking flag is left exactly as before --
#: the same conservative, no-worse-than-before default this module already
#: documents for item 7 below; this set only grows for a flag positively
#: confirmed non-path, mirroring the macro-flag precedent rather than
#: attempting to enumerate every possible flag.
_CHDIR_FOLD_NON_PATH_VALUE_FLAGS = frozenset({"-x", "-target", "-arch", "-Xclang"})


def _fold_chdir_path_value(value: str, chdir: str) -> str:
    """Resolve a single path-bearing VALUE against *chdir*, the shared leaf
    :func:`_fold_chdir_into_operands` uses for every shape (combined,
    equals-joined, separate-form, and bare positional) it recognizes.

    An empty or already-absolute value is returned unchanged -- unambiguous
    regardless of *chdir*. Deliberately does NOT require *value* to contain
    a path separator (Codex review, Finding 12, fresh evidence): a
    single-segment relative directory name (``include``, no ``/``/``\\`` at
    all) is exactly as real a path as a multi-segment one, and the caller
    already established via flag/positional CONTEXT (not string shape) that
    this value is meant as a path -- see :func:`_fold_chdir_into_operands`'s
    own docstring for the context-tracking this replaces a separator-based
    guess with.
    """
    if not value or is_absolute_path_token(value):
        return value
    return join_path_token(chdir, value)


def _fold_chdir_into_operands(tokens: list[str], chdir: str | None) -> list[str]:
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
    7. A bare, non-flag (no leading ``-``/``/``) positional token not
       already consumed as a flag's value above (a source-file operand)
       -- resolved unconditionally.
    8. Anything else (an unrecognized flag, or a flag this module cannot
       positively confirm is path- or non-path-valued) -- passed through
       unchanged, the same conservative, no-worse-than-before default this
       module already uses throughout; its own possible operand (if any)
       is not specially tracked either and falls through to whichever of
       (7)/(8) it itself matches on the next iteration.
    """
    if not chdir:
        return tokens
    out: list[str] = []
    i = 0
    n = len(tokens)
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
        if not tok.startswith(("-", "/")):
            out.append(_fold_chdir_path_value(tok, chdir))
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


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
    _resolve_clang_bin``, or the eventual subprocess spawn itself) resolve
    it against ABICHECK'S OWN inherited ``PATH``, which the real recorded
    command -- genuinely PATH-less under ``env -i`` -- could never have
    done. See :func:`env_path_cleared_for_bare_token`'s own docstring for
    the full reasoning. Degrading to the same language-based ``g++``/``gcc``
    fallback used for a missing/flag-only command is the correct response:
    a name this provably unresolvable carries no more real toolchain
    identity than no recorded command at all.
    """
    if override:
        return override
    argv = strip_launchers(compile_unit.argv, directory=compile_unit.directory)
    if (
        argv
        and argv[0]
        and not argv[0].startswith("-")
        and not env_path_cleared_for_bare_token(compile_unit.argv, argv[0])
    ):
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


def is_msvc_mode(cc_bin: str) -> bool:
    """Whether the compiler basename means MSVC (``cl``/``clang-cl``) mode.

    Checks the exact (possibly ``.exe``-suffixed) name first, then falls
    back to the same check with a trailing version suffix stripped, so a
    packaged ``clang-cl-20``/``clang-cl-20.exe`` is still recognized.
    """
    # The name test itself is header_utils.is_msvc_driver_stem -- the one
    # vocabulary buildsource.adapters.base._is_msvc_command now shares, after
    # the two drifted apart (Codex review, PR D). Behaviour here is unchanged:
    # that function is this test, relocated. Only the basename derivation
    # stays local, since the adapter's is backslash-aware and this one is not.
    return is_msvc_driver_stem(basename(cc_bin).lower())


def _carry_abi_relevant_flags(
    abi_relevant_flags: list[str], seen: set[str], out: list[str]
) -> None:
    """Append ``abi_relevant_flags`` not already in ``seen`` to ``out``.

    Skips flags whose prefix matches a structured toolchain option (sysroot,
    target, isysroot) because those are already emitted from the structured
    fields and the split spelling would dangle if re-appended.

    A ``-target-abi``/``-target-cpu``/``-target-feature``/
    ``-target-linker-version`` survivor -- bare (``-target-abi=aapcs``) or
    ``-Xclang``-wrapped (``-Xclang -target-abi=aapcs``) -- is
    ``adapters.base.extract_abi_relevant_flags``'s own purely-internal,
    round-trippable encoding of a genuinely split two-token (or
    ``-Xclang``-wrapped four-token) cc1 flag; it is not real argv syntax on
    its own. This L4 replay path used to append it unchanged (P2 review,
    "Decode normalized cc1 flags in every replay path", fresh evidence):
    Clang then received one malformed token instead of the required literal
    tokens, and the bare spelling was separately dropped outright by the
    ``STRUCTURED_TOOLCHAIN_FLAG_PREFIXES`` check below (it shares the
    ``-target`` prefix that filter drops as redundant for the *unrelated*,
    already-structured ``target_triple``/``sysroot`` survivors -- but none of
    these four flags has a structured field of its own). Decoded first, via
    the same :func:`~abicheck.buildsource.adapters.base.split_operand_survivor`
    the L2 header-compile-context replay path already used, so both replay
    paths reconstruct the identical, real argv token(s) from one shared
    implementation.

    *seen* is checked but never updated by this loop (Codex review, fresh
    evidence): an earlier revision added each carried flag to *seen* as it
    went, which silently dropped every REPEAT of an already-carried literal
    token. That is wrong for a toggle-style flag pair sharing one spelling
    across BOTH states (``-fsycl``/``-fno-sycl``, ``-fexceptions``/
    ``-fno-exceptions``, …): a real, layered build config can legitimately
    record the identical flag more than once
    (``extract_abi_relevant_flags`` preserves every occurrence, in order,
    with no dedup of its own), and only the LAST occurrence decides the
    real compiler's effective state. Deduping WITHIN this carry-through
    collapsed a sequence like ``-fno-sycl -fsycl -fno-sycl`` down to
    ``-fno-sycl -fsycl`` (dropping the second, decisive ``-fno-sycl`` as a
    "duplicate" of the first) — silently reversing the real effective
    state from disabled to enabled. Checking only the CALLER's initial
    *seen* (flags already emitted from the structured fields) still avoids
    re-emitting those, which is this function's only documented purpose;
    every occurrence of a flag genuinely repeated within
    ``abi_relevant_flags`` itself is now carried through faithfully, in
    the real build's own order and multiplicity, so last-flag-wins
    scanning downstream (e.g. ``dumper_clang._needs_sycl_host_only``) sees
    the same sequence the real compiler did.
    """
    for flag in abi_relevant_flags:
        if is_split_operand_abi_flag_survivor(flag):
            out.extend(split_operand_survivor(flag))
            continue
        if flag.startswith(STRUCTURED_TOOLCHAIN_FLAG_PREFIXES):
            continue
        if flag not in seen:
            out.append(flag)


def _match_gnu_include_search(tok: str, argv: list[str], i: int, out: list[str]) -> int:
    """Try to match a GNU include-search token (``-iquote``/``-idirafter``).

    Returns the new ``i`` after consumption, or the original ``i`` if no match.
    Both the separate (``-iquote dir``) and joined (``-iquotedir``) spellings are
    handled. Returns the original index when the token does not match either form.
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


def _scan_argv_for_extra_flags(argv: list[str], cc_id: str, out: list[str]) -> None:
    """Walk ``argv`` and append forced-include / include-search tokens to ``out``.

    Handles GNU and (when ``cc_id == "msvc"``) MSVC option families.  Each
    option family is tried in priority order; the first match consumes the
    token(s) and advances the index.
    """
    i = 0
    while i < len(argv):
        tok = argv[i]
        # The two forced-include matchers are shared with the L2 header-parse
        # path (header_utils) and return (new_i, option, operand); only the
        # index matters here, since replay hands `out`'s verbatim tokens
        # straight back to the real compiler.
        new_i, _opt, _operand = _match_gnu_forced_include(tok, argv, i, out)
        if new_i == i:
            new_i = _match_gnu_include_search(tok, argv, i, out)
        if new_i == i and cc_id == "msvc":
            new_i = _match_msvc_include_search(tok, argv, i, out)
        if new_i == i and cc_id == "msvc":
            new_i, _opt, _operand = _match_msvc_forced_include(tok, argv, i, out)
        i = new_i if new_i != i else i + 1


def replay_extra_flags(
    compile_unit: CompileUnit, already: list[str], cc_id: str
) -> list[str]:
    """Carry through ABI/parse-relevant options not in the structured fields.

    ``abi_relevant_flags`` (e.g. ``-fms-extensions``, ``-fabi-version``,
    ``-fvisibility``, ``-m32``), forced-include options, and unnormalized
    include-search options (GNU ``-iquote``/``-idirafter``, MSVC ``/I``) from
    ``argv`` change the parsed translation unit / header search; dropping them
    makes the extractor parse a different TU than the real build (ADR-030 D2).
    De-duplicated against the flags already emitted from the structured fields.
    MSVC ``/FI`` forced includes and ``/I`` search dirs are carried only in MSVC
    mode so a GNU ``-F``/``-I``-family flag is never mistaken for one.
    """
    seen = set(already)
    out: list[str] = []
    _carry_abi_relevant_flags(compile_unit.abi_relevant_flags, seen, out)
    _scan_argv_for_extra_flags(compile_unit.argv, cc_id, out)
    return out

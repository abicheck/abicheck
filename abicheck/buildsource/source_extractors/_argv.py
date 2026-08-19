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

import os
import re
import shutil
from collections.abc import Sequence

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
#: ``env`` flags that stand alone -- no operand of their own follows.
#: ``-i``/``--ignore-environment`` clears the inherited environment before
#: applying any ``NAME=VALUE`` assignments; the others are cosmetic/output
#: modifiers. None of these change which *token* is the real driver, only
#: how ``env`` itself behaves, so they are skipped rather than parsed.
_ENV_NO_OPERAND_FLAGS = frozenset(
    {"-i", "--ignore-environment", "-0", "--null", "-v", "--verbose"}
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
#: The env-assignment name whose value is captured (not just skipped) by
#: :func:`_skip_env_prefix`, feeding :func:`_apply_env_context`'s
#: PATH-resolution below (P2 review, "Resolve drivers using the
#: env-supplied PATH", fresh evidence). Only a *bare* driver name (no path
#: separator) is ever looked up against it -- see that function's own
#: docstring.
_ENV_PATH_ASSIGNMENT_PREFIX = "PATH="
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


def _skip_env_prefix(argv: list[str], i: int) -> tuple[int, str | None, str | None]:
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
    """
    if i >= len(argv) or basename(argv[i]).lower() not in _ENV_BASENAMES:
        return i, None, None
    i += 1
    chdir: str | None = None
    env_path: str | None = None
    while i < len(argv):
        arg = argv[i]
        if arg in _ENV_NO_OPERAND_FLAGS:
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
        if arg in _ENV_OPERAND_FLAGS and i + 1 < len(argv):
            i += 2
            continue
        if arg.startswith("--unset="):
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


def _apply_env_context(token: str, chdir: str | None, env_path: str | None) -> str:
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
    which ``PATH`` would have been searched. Resolved immediately via
    ``shutil.which(token, path=env_path)`` into an absolute path when
    found, so no downstream consumer needs to know the env override
    existed at all; left unchanged (the pre-fix, bare-name behavior) when
    the name isn't found on that PATH either -- the same conservative,
    no-worse-than-before fallback every other best-effort resolution in
    this module already uses.

    A token that is itself a flag (starts with ``-``) -- meaning
    :func:`strip_launchers` found no real driver token at all -- is left
    completely untouched; neither effect applies to a non-existent driver.
    """
    if not token or token.startswith("-"):
        return token
    if "/" in token or "\\" in token:
        if chdir and not os.path.isabs(token):
            return os.path.normpath(os.path.join(chdir, token))
        return token
    if env_path:
        resolved = shutil.which(token, path=env_path)
        if resolved:
            return resolved
    return token


def strip_launchers(argv: list[str]) -> list[str]:
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
    """
    i = 0
    progressed = True
    chdir: str | None = None
    env_path: str | None = None
    while progressed:
        progressed = False
        new_i, new_chdir, new_path = _skip_env_prefix(argv, i)
        if new_i != i:
            i = new_i
            progressed = True
            if new_chdir is not None:
                chdir = new_chdir
            if new_path is not None:
                env_path = new_path
        while (
            i < len(argv)
            and basename(argv[i]).lower().removesuffix(".exe") in COMPILER_LAUNCHERS
        ):
            i += 1
            while i < len(argv) and _LAUNCHER_CONFIG_OVERRIDE_RE.match(argv[i]):
                i += 1
            progressed = True
    result = argv[i:]
    if not result or (chdir is None and env_path is None):
        return result
    resolved = _apply_env_context(result[0], chdir, env_path)
    if resolved == result[0]:
        return result
    return [resolved, *result[1:]]


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
    """
    if override:
        return override
    argv = strip_launchers(compile_unit.argv)
    if argv and argv[0] and not argv[0].startswith("-"):
        return argv[0]
    return "g++" if compile_unit.language.lower() in CXX_LANGS else "gcc"


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

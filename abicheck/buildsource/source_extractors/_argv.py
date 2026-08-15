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
fiddly parts — unwrapping ``ccache``/``sccache`` launchers, detecting MSVC mode
from a (possibly Windows, possibly cross) compiler path, carrying argv-only
forced includes, and reversing the redaction policy's ``~`` home placeholder for
the replay only (ADR-032 D7). Keeping that here means one tested implementation,
not one per backend.

Pure and tool-independent: nothing here shells out.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from ..build_evidence import CompileUnit

#: Languages that make the GNU fallback compiler ``g++`` rather than ``gcc``.
CXX_LANGS = frozenset({"cxx", "c++", "cpp"})
#: Compiler basenames that mean the extractor should run in MSVC mode.
#: ``dpcpp-cl``/``dpcpp-cl.exe`` is Intel's oneAPI DPC++/C++ CL-compatible
#: driver (the same CL-mode convention as ``clang-cl``, just Intel-branded);
#: without it here, ``dumper_clang.resolve_source_frontend_clang_bin``'s
#: ``exclude_cl_style=False`` (L4 source-ABI replay) resolves ``--gcc-path
#: dpcpp-cl`` correctly, but this module still built a GNU-shaped command for
#: it instead of adding ``--driver-mode=cl``, so the CL-mode override never
#: actually reached the driver (Codex review).
MSVC_BINARIES = frozenset(
    {"cl", "cl.exe", "clang-cl", "clang-cl.exe", "dpcpp-cl", "dpcpp-cl.exe"}
)
#: The same names with any ``.exe`` suffix stripped, for matching after a
#: version suffix has also been removed (``is_msvc_mode`` normalizes both).
_MSVC_STEMS = frozenset(name.removesuffix(".exe") for name in MSVC_BINARIES)
#: Matches a trailing numeric version suffix LLVM/Debian packaging commonly
#: appends to an unversioned driver name (``clang-cl-20``, ``clang-20.1``) --
#: without stripping it, ``is_msvc_mode("clang-cl-20")`` would miss a real
#: CL-mode driver just because it carries its LLVM major-version suffix
#: (Codex review): the S2 pre-scan (and, via ``pick_compiler_binary``, L4
#: replay) would then drive it with GNU-only flags it silently ignores.
_VERSION_SUFFIX_RE = re.compile(r"-\d+(?:\.\d+)*$")
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
_LAUNCHER_CONFIG_OVERRIDE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
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
#: individually prefixed. ``extract_abi_relevant_flags`` normalizes that
#: wrapped shape into a second, distinct internal encoding, ``-Xclang
#: <flag>=<value>`` (see :data:`_XCLANG_WRAPPED_ABI_FLAG_MARKER`), so
#: :func:`split_operand_survivor` can tell which of the two real argv shapes
#: (``["-target-abi", "<value>"]`` vs. ``["-Xclang", "-target-abi", "-Xclang",
#: "<value>"]``) to reconstruct at replay time.
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

#: The literal marker ``adapters.base.extract_abi_relevant_flags`` prepends
#: to a ``-Xclang``-wrapped split-operand flag's internal encoding.
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
    normalizes that wrapped shape into a second, distinct internal encoding
    (a leading ``-Xclang `` marker, see
    :data:`_XCLANG_WRAPPED_ABI_FLAG_MARKER`), which this function
    reconstructs into the full four-token ``["-Xclang", "<flag>", "-Xclang",
    "<value>"]`` form -- a bare, unwrapped ``-target-abi aapcs`` is not valid
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
    """
    if flag.startswith(_XCLANG_WRAPPED_ABI_FLAG_MARKER):
        remainder = flag[len(_XCLANG_WRAPPED_ABI_FLAG_MARKER) :]
        name, sep, value = remainder.partition("=")
        if sep and name in SPLIT_OPERAND_ABI_FLAGS:
            return ["-Xclang", name, "-Xclang", value]
        return [flag]
    name, sep, value = flag.partition("=")
    if sep and name in SPLIT_OPERAND_ABI_FLAGS:
        return [name, value]
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


#: GNU forced-include options. Only ``-include``/``-imacros`` also have a joined
#: ``-include<file>`` spelling; ``-include-pch`` is separate-operand only (clang
#: ``-include-pch <file>``) and must not be read as a joined ``-include``.
_GNU_FORCED_INCLUDE_OPTS = frozenset({"-include", "-imacros"})
_GNU_SEPARATE_INCLUDE_OPTS = frozenset({"-include", "-imacros", "-include-pch"})
#: MSVC/clang-cl forced-include options in their separate-operand spelling
#: (``/FI file`` or ``-FI file``); the joined ``/FIfile`` form is handled by
#: prefix.
_MSVC_FORCED_INCLUDE_OPTS = frozenset({"/FI", "-FI"})
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


def strip_launchers(argv: list[str]) -> list[str]:
    """Drop leading compiler-launcher tokens (``ccache``/``sccache``/…).

    Also skips any ``KEY=VALUE`` per-invocation config-override tokens ccache
    accepts immediately after its own name (``ccache compiler_check=content
    gcc -c foo.c``) — otherwise the override token, not the real compiler, is
    left as the new ``argv[0]``.
    """
    i = 0
    while (
        i < len(argv)
        and basename(argv[i]).lower().removesuffix(".exe") in COMPILER_LAUNCHERS
    ):
        i += 1
        while i < len(argv) and _LAUNCHER_CONFIG_OVERRIDE_RE.match(argv[i]):
            i += 1
    return argv[i:]


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
    stem = basename(cc_bin).lower()
    if stem in MSVC_BINARIES:
        return True
    return _VERSION_SUFFIX_RE.sub("", stem.removesuffix(".exe")) in _MSVC_STEMS


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


def _match_gnu_forced_include(tok: str, argv: list[str], i: int, out: list[str]) -> int:
    """Try to match a GNU forced-include token (``-include``/``-imacros``/``-include-pch``).

    Returns the new ``i`` after consumption, or the original ``i`` if no match.
    The separate-operand spelling (all three options) and the joined spelling
    (``-include``/``-imacros`` only, never ``-include-pch``) are both handled.
    """
    if tok in _GNU_SEPARATE_INCLUDE_OPTS and i + 1 < len(argv):
        out += [tok, argv[i + 1]]  # -include / -imacros / -include-pch <file>
        return i + 2
    if tok not in _GNU_SEPARATE_INCLUDE_OPTS and any(
        tok.startswith(opt) and len(tok) > len(opt) for opt in _GNU_FORCED_INCLUDE_OPTS
    ):
        out.append(tok)  # -includefile / -imacrosfile (joined)
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


def _match_msvc_forced_include(
    tok: str, argv: list[str], i: int, out: list[str]
) -> int:
    """Try to match an MSVC ``/FI`` forced-include token (MSVC mode only).

    Returns the new ``i`` after consumption, or the original ``i`` if no match.
    Both the separate (``/FI file``) and joined (``/FIfile``, ``-FIfile``)
    spellings are handled.
    """
    if tok in _MSVC_FORCED_INCLUDE_OPTS and i + 1 < len(argv):
        out += [tok, argv[i + 1]]  # /FI file (separate operand)
        return i + 2
    if len(tok) > 3 and (tok.startswith("/FI") or tok.startswith("-FI")):
        out.append(tok)  # /FIfile (joined)
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
        new_i = _match_gnu_forced_include(tok, argv, i, out)
        if new_i == i:
            new_i = _match_gnu_include_search(tok, argv, i, out)
        if new_i == i and cc_id == "msvc":
            new_i = _match_msvc_include_search(tok, argv, i, out)
        if new_i == i and cc_id == "msvc":
            new_i = _match_msvc_forced_include(tok, argv, i, out)
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

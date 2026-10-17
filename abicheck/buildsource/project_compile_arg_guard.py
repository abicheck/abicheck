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
"""Compile-option atom guard for ``profiles.<id>.compile`` (ADR-047 §3/G34).

Owns the denylist of compiler flags that reach a plugin, response-file,
spec-substitution or subprocess-forwarding mechanism, and the shlex-safety
check every ``compile.*`` atom passes before it is composed into a single
``compile_gcc_options`` string. Extracted from
:mod:`abicheck.buildsource.project_targets` so the parser and this
security-relevant denylist can be reviewed as one unit; the parser remains
the only caller of :func:`safe_profile_atom`.
"""

from __future__ import annotations

#: Flags that reach a compiler frontend's own plugin/response-file/spec-
#: substitution/config-file/subprocess-forwarding machinery rather than an
#: ABI-relevant compile axis. Each is a single whitespace-free atom (so
#: ``safe_profile_atom``'s smuggling check alone does not reject it) that
#: falls into one of four families:
#:
#: - loads arbitrary code directly into the compiler process
#:   (``-Xclang``/``-load``/``-fplugin=``/``-fpass-plugin=``);
#: - re-expands attacker-controlled file content into more argv
#:   (``@response-file``; Clang's ``--config``/``--config=<file>`` -- a
#:   "configuration file" of additional command-line options, including
#:   ``-fplugin=``, so leaving it unblocked would let it re-smuggle back in
#:   everything else this denylist rejects; the ``--config`` prefix below
#:   also catches ``--config-system-dir=``/``--config-user-dir=``, which
#:   redirect where an implicit/explicit config file is looked up);
#: - substitutes the compiler driver's own trusted built-in command line
#:   for one read from disk (``-specs=``/``--specs=`` -- GCC's driver
#:   accepts both spellings, translating the GNU-style ``--specs``
#:   long-option form to ``-specs`` internally, so both must be blocked --
#:   and ``-wrapper``); or
#: - **forwards its entire payload verbatim into a different subprocess**
#:   (preprocessor/assembler/linker), which can smuggle any of the above
#:   past a prefix check on the *outer* atom alone: GCC's ``-Wa,``/
#:   ``-Wp,``/``-Wl,`` pass their comma-joined payload straight to the
#:   assembler/preprocessor/linker (confirmed via ``gcc --help``) --
#:   ``-Wp,-fplugin=./evil.so`` reaches cc1 and loads the plugin exactly as
#:   a bare ``-fplugin=`` would, and ``-Wl,-plugin=./evil.dso`` loads an
#:   LTO linker plugin the same way (confirmed via ``ld --help``); Clang's
#:   separate-argument equivalents ``-Xpreprocessor``/``-Xassembler``/
#:   ``-Xlinker`` are the same mechanism as the already-blocked
#:   ``-Xclang``, so blocking the forwarding flag itself (its target is a
#:   separate, otherwise-inert atom) is sufficient, same as ``-Xclang``.
#:
#: ``profiles.<id>.compile.args`` is documented (this module's docstring,
#: ``ProfileCompileSpec``'s own docstring) as a "normalized extra args"
#: escape hatch for ABI-relevant flags an auto-discovered, untrusted
#: ``.abicheck.yml`` may declare — never an executable-code path. Checked as
#: an exact match or a prefix (for the ``=``-/``,``-joined spellings)
#: against each ``args`` atom; case-sensitive, matching how compiler CLIs
#: themselves parse these flags. This denylist targets the *delivery
#: mechanism* (a flag whose whole job is loading code or re-expanding more
#: argv), not every dangerous flag those mechanisms could carry -- a new
#: subprocess-forwarding mechanism found later belongs here as another
#: blocked prefix, not as a growing list of the individual flags it could
#: smuggle. (Codex review, PR #639: the initial denylist
#: omitted ``--config``, the ``--specs`` double-dash spelling of
#: ``-specs``, the whole ``-Wa,``/``-Wp,``/``-Wl,``/``-Xpreprocessor``/
#: ``-Xassembler``/``-Xlinker`` subprocess-forwarding family, and
#: ``--castxml-cc-``.
#:
#: ``--castxml-cc-`` is a different case from the rest of this list: it
#: does not smuggle an already-blocked flag past a prefix check, it
#: targets the trusted ``--castxml-cc-<id> <path>`` pair
#: ``dumper_ast_config.py`` itself composes *ahead of* this denylist's
#: ``args`` -- a second occurrence naively looks like it could replace the
#: verified compiler path with an attacker-controlled one. Verified
#: empirically against the installed castxml (0.6.3) that this is not
#: actually exploitable: castxml hard-rejects any repeated
#: ``--castxml-cc-*`` occurrence at argv-parse time --
#: ``error: '--castxml-cc-<id>' may be given at most once!`` -- regardless
#: of whether the ``<id>`` matches the first occurrence, so the scan fails
#: outright rather than silently invoking a substituted binary. Blocked
#: here anyway for defense-in-depth/a clearer abicheck-level error instead
#: of relying on that castxml-internal invariant holding across every
#: supported castxml version.
#:
#: ``-B<dir>``/``-B <dir>`` is the same shape of finding as
#: ``--castxml-cc-`` above: real for the mechanism it names, but verified
#: empirically to not reach abicheck's actual pipeline as claimed. GCC's
#: ``-B<dir>`` really does add *dir* to its compiler-component search path
#: and really does execute an attacker-supplied ``cc1``/``cc1plus`` placed
#: there instead of the real one (confirmed: ``gcc -B./tools/ -E`` ran a
#: planted ``./tools/cc1``) -- but every consumer of this composed string
#: (castxml's internal bundled Clang, and the direct ``--ast-frontend
#: clang`` backend) is Clang, not GCC, and Clang has no separate,
#: ``-B``-discoverable ``cc1`` to substitute: it re-execs itself via
#: ``-cc1`` instead. Confirmed empirically that ``-B./tools/`` does not
#: run a planted ``./tools/cc1`` for either castxml or a direct ``clang -E``
#: invocation, even though ``clang -B./tools/ -print-prog-name=cc1`` shows
#: ``-B`` does influence *where Clang would look* for a tool named that.
#: Blocked anyway: cheap, and closes the door in case a future toolchain-
#: execution-contract change (AGENTS.md's "Known gaps" entry) ever forwards
#: these flags to a real GCC invocation directly, which would restore the
#: attack.
#:
#: ``/clang:<arg>`` is clang-cl's (Clang's MSVC-compatible driver mode,
#: reachable via a Windows toolchain binding whose path stem contains
#: "clang" -- e.g. ``clang-cl``/``clang-cl.exe``, which
#: ``abicheck.extract.compiler_driver.is_clang_family_binary`` recognizes) own escape hatch
#: for passing an argument straight to the underlying clang driver,
#: bypassing clang-cl's normally-MSVC-shaped option parsing entirely --
#: confirmed **actually exploitable** (unlike ``--castxml-cc-``/``-B``
#: above): ``clang --driver-mode=cl "/clang:-fplugin=./evil.so" -c t.h``
#: really does load and run the planted plugin. ``/link <options>`` is the
#: same driver's documented "forward options to the linker" escape hatch
#: -- the cl-mode spelling of the already-blocked ``-Wl,`` mechanism above
#: -- blocked for the same reason, on the same LTO-linker-plugin grounds,
#: even without a from-scratch clang-cl empirical repro of that specific
#: sub-case.
#:
#: ``-cc1``/``-cc1as`` are a different shape of finding again: Clang's
#: driver only enters its internal ``cc1``/``cc1as`` frontend mode when
#: ``-cc1``/``-cc1as`` is literally the *first* argument after the program
#: name -- confirmed empirically (``clang -c t.h -o t.o -cc1 -load
#: ./evil.so`` rejects ``-cc1`` as "unknown argument"; ``clang -I. -cc1
#: -load ./evil.so`` does too, since ``-I`` from a real header scan's
#: ``extra_includes`` already occupies that first-argument slot), but
#: ``clang -cc1 -load ./evil.so -plugin foo`` (nothing before ``-cc1``)
#: really does run the planted plugin's constructor before failing on the
#: unresolvable plugin name. This module's caller (``dumper.py``'s
#: ``_build_clang_header_command``) builds argv as ``[cc_bin, *-I dirs,
#: --sysroot, -nostdinc, *gcc_options tokens, ...]`` -- when a scan has no
#: ``extra_includes``/``sysroot``/``nostdinc`` (a header with no separate
#: ``-I`` search path, no cross sysroot), a leading ``-cc1`` in
#: ``compile.args`` genuinely lands in that first-argument slot. Once in
#: cc1 mode, ``-load``/``-fpass-plugin=`` are still blocked above, but cc1
#: mode exposes an entirely different, much larger argument namespace this
#: denylist was never designed to enumerate (Codex review found
#: ``-fcas-plugin-path``, a cc1-only flag not in every Clang build, doing
#: the identical thing) -- reject the *mode switch itself*, the same
#: "block the delivery mechanism, not every payload it could carry"
#: reasoning as ``--config`` above.
_DANGEROUS_ARG_PREFIXES = (
    "-Xclang",
    "-Xpreprocessor",
    "-Xassembler",
    "-Xlinker",
    "-load",
    "-fplugin=",
    "-fplugin-arg-",
    "-fpass-plugin=",
    "-specs=",
    "-specs",
    "--specs=",
    "--specs",
    "-wrapper",
    "--config",
    "-Wa,",
    "-Wp,",
    "-Wl,",
    "--castxml-cc-",
    "-B",
    "/clang:",
    "/link",
    "-cc1",
    "@",
)


def _reject_dangerous_arg(where: str, key: str, value: str) -> None:
    if any(value == p or value.startswith(p) for p in _DANGEROUS_ARG_PREFIXES):
        raise ValueError(
            f"{where}.{key} entry {value!r} is not allowed: it reaches a "
            "compiler plugin/response-file/spec-substitution mechanism, not "
            "an ABI-relevant compile flag — an auto-discovered "
            "profiles.compile.args cannot declare executable configuration"
        )


#: Characters ``shlex.split()`` (POSIX mode) treats specially: quoting and
#: escaping. ``run_plan._compose_gcc_options`` space-joins every atom from
#: every ``compile.*`` field (``standard``/``stdlib``/``target``/
#: ``abi_macros``/``args``) into ONE string, and the eventual consumer
#: (``dumper.py``'s ``--gcc-options`` handling) re-splits that whole string
#: with ``abicheck._compiler_options.split_gcc_options`` (plain
#: ``shlex.split(text, posix=True)`` on POSIX, real quote/backslash-run
#: parity on Windows — quote removal AND backslash escaping both apply on
#: either platform) to recover argv.
#: An atom containing a quote or backslash survives every check above
#: unchanged (neither is whitespace, and a quote-wrapped dangerous flag like
#: ``"'-fplugin=./evil.so'"`` does not start with a bare ``-fplugin=``) but
#: is not inert: POSIX shlex quote-removal reconstitutes it into the exact
#: blocked flag once the composed string is re-split, and because shlex
#: parses the WHOLE joined string in one pass, an unbalanced quote in one
#: atom can also shift where token boundaries fall in a neighboring atom.
#: Rejecting these characters in every atom (not just ``args``) keeps the
#: "one atom == one post-shlex token, independent of its neighbors"
#: invariant the denylist above and the whitespace check both already rely
#: on. (Codex review, PR #639.)
_SHLEX_UNSAFE_CHARS = frozenset("'\"\\")


def safe_profile_atom(
    where: str, key: str, value: str, *, reject_dangerous: bool = False
) -> str:
    """Reject a compiler-option atom that could smuggle multiple flags/args.

    Mirrors ``buildsource.build_config.BuildConfig``'s own ``_safe_compile_atom``:
    a ``profiles:`` block is read from the same untrusted, auto-discovered
    ``.abicheck.yml`` as ``compile:``/``build.query`` (CLAUDE.md M1's trust
    boundary — see this module's docstring), and its ``compile.*`` overlay
    fields are documented to eventually reach real compiler argv the same way
    ``compile.std``/``compile.defines`` already do. Whitespace would let one
    YAML scalar become several argv tokens (e.g. ``"gnu++17 -Xclang -load
    ./evil.so"``), so it is rejected here at parse time regardless of whether
    a consumer resolves this field into argv yet. A quote or backslash
    character is rejected for the same reason — see
    :data:`_SHLEX_UNSAFE_CHARS`'s docstring — for every field, not just
    ``args``, since all of them land in the same shlex-re-split string.

    ``reject_dangerous`` additionally runs :func:`_reject_dangerous_arg` —
    set only for ``args`` (the one field appended to compiler argv as
    standalone flags rather than folded into a fixed ``-std=``/``-D<name>=``
    prefix, so it is the sole atom-level plugin/response-file injection
    vector; see :data:`_DANGEROUS_ARG_PREFIXES`).
    """
    if not value or any(ch.isspace() for ch in value):
        raise ValueError(f"{where}.{key} must be a single option atom, got {value!r}")
    if any(ch in _SHLEX_UNSAFE_CHARS for ch in value):
        raise ValueError(
            f"{where}.{key} must not contain quote/backslash characters, got "
            f"{value!r} — these survive re-parsing as inert but are not: the "
            "composed compile_gcc_options string is later re-split with "
            "shlex, which would reconstitute them into a different, "
            "unvalidated token"
        )
    if reject_dangerous:
        _reject_dangerous_arg(where, key, value)
    return value

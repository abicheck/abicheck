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
"""The ABI-affecting compiler-flag vocabulary, shared by every argv replay.

A leaf module: the prefix list the build-evidence adapters use to decide which
flags are ABI-relevant, and the split-operand encoding the replay paths decode.
Both :mod:`.adapters.adapter_shared` and :mod:`.source_extractors.compile_invocation` read it,
and neither may be imported from here, so it sits below both.
"""

from __future__ import annotations

#: ABI/API-affecting compiler-flag prefixes (ADR-029 D9). Drift in any of these
#: is treated as a risk signal by the build-evidence diff, not mere noise.
#:
#: A flag here is captured per-TU into ``CompileUnit.abi_relevant_flags`` and
#: projected into a global ``BuildOption`` by :func:`derive_build_options`; the
#: build-evidence diff (``build_diff._diff_options``) then surfaces any drift as
#: ``ABI_RELEVANT_BUILD_FLAG_CHANGED`` (unless the flag is a runtime-mode or
#: toolchain flag with a dedicated finding). Per ADR-028 D3 these are risk/
#: source-level signals — the artifact diff proves any concrete break.
#:
#: **Forced pre-includes (``-include``/``-imacros``/``/FI``) are deliberately
#: NOT in this tuple, and the fix an earlier draft of this comment proposed --
#: "a new spaced-value-flag branch in :func:`extract_abi_relevant_flags`" --
#: was investigated and found to be actively wrong (PR D / plan PR 3B).** The
#: L2 gap it described is real: ``header_compile_context``'s derived L2
#: ``CompileContext`` never saw a matched compile unit's own macro-controlling
#: forced-include header, so the P0.3 L3->L2 fold could report a real match
#: (``matched_unit_count > 0``, ``parsed_with_build_context`` stamped) while
#: still parsing without it. But routing the fix through *this* list would
#: have broken L4 replay, which already handles forced includes correctly and
#: by a different route: ``source_extractors.compile_invocation.replayed_tokens``
#: carries ``abi_relevant_flags`` through
#: (``_carry_abi_relevant_flags``) **and**, separately and unconditionally,
#: re-scans the unit's raw ``argv`` for forced-include/include-search tokens
#: (``_scan_argv_for_extra_flags``, which is not passed the ``seen`` set the
#: first pass builds). Capturing a forced include here would therefore have
#: made every L4 replay command carry ``-include config.h`` **twice** -- a
#: silent double-inclusion that a header without include guards turns into a
#: hard redefinition error.
#:
#: The gap is closed at the layer that actually had it instead:
#: ``header_utils.forced_include_operands`` is the one shared recognizer (the
#: same option vocabulary and the same separate/joined spellings ``_argv``'s
#: own replay matchers use, since those matchers live there now too), and
#: ``header_compile_context._context_flags`` renders its result into the L2
#: command directly from ``cu.argv`` -- never through
#: ``abi_relevant_flags``, so L4 replay is untouched.
#:
#: Residual, deliberately-unclosed half: because a forced include is not in
#: this tuple, it is still not projected into a ``BuildOption`` by
#: :func:`derive_build_options`, so swapping one build's forced-include header
#: for another does not raise ``ABI_RELEVANT_BUILD_FLAG_CHANGED`` (ADR-029
#: D9's build-evidence drift signal). Closing *that* needs a structured
#: ``CompileUnit`` field every adapter populates plus a
#: ``BUILD_EVIDENCE_VERSION`` bump and ``build_diff`` wiring -- a schema slice
#: of its own, not a follow-on to the L2 rendering fix. See ``AGENTS.md``.
ABI_RELEVANT_FLAG_PREFIXES: tuple[str, ...] = (
    "-std=",
    "/std:",
    "-stdlib=",
    "--target=",
    "-target",
    "-mabi=",
    "/arch:",
    "-m32",
    "-m64",
    # Microarchitecture / float ABI — can change struct alignment (vector ABI)
    # or the hard/soft-float calling convention (ARM), so a flip is a risk.
    "-march=",
    "-mtune=",
    "-mfloat-abi=",
    "-mfpmath=",
    "--sysroot",
    "-isysroot",
    "-fvisibility",
    "-fvisibility-inlines-hidden",
    "-fpack-struct",
    "/Zp",
    "-fshort-enums",
    "-fno-short-enums",
    "-fshort-wchar",
    "-fsigned-char",
    "-funsigned-char",
    "-fabi-version",
    "-fno-rtti",
    "-frtti",
    "-fno-exceptions",
    "-fexceptions",
    "-flto",
    "-fno-lto",
    "-fwhole-program-vtables",
    "-fno-whole-program-vtables",
    "-ftls-model",
    "-fextern-tls-init",
    "-fno-extern-tls-init",
    "-fno-threadsafe-statics",
    "-fthreadsafe-statics",
    "-freg-struct-return",
    "-fpcc-struct-return",
    # Sanitizers change object layout/instrumentation and the runtime contract.
    "-fsanitize=",
    "-fno-sanitize=",
    # Position-independence / TLS access model and frame-pointer presence.
    "-fPIC",
    "-fpic",
    "-fPIE",
    "-fpie",
    "-fno-pic",
    "-fno-pie",
    "-fno-PIC",
    "-fno-PIE",
    "-fomit-frame-pointer",
    "-fno-omit-frame-pointer",
    # SYCL language mode (icpx/dpcpp -fsycl/-fsycl-*): enables an entirely
    # different parse (implicit SYCL builtins/attributes, kernel-lambda
    # codegen), so its presence/absence is exactly the kind of parse-affecting
    # signal this list already tracks for -std=/-fvisibility/etc. Without
    # this, a resolved --compiler override that now actually invokes icpx/
    # dpcpp for L4 replay (previously always a bare "clang") would replay a
    # SYCL TU as plain C++, silently missing built-in SYCL state (Codex
    # review). Only the flag's *presence*, carried via replayed_tokens(),
    # not a from-scratch reconstruction of the real build's host/device
    # multi-pass invocation -- see the "Known gaps" entry in AGENTS.md.
    # "-fno-sycl" is a separate, non-prefix-matching spelling (it does not
    # start with "-fsycl") that explicitly disables SYCL mode again -- an
    # "icpx -fsycl -fno-sycl" (last-flag-wins) command recorded only
    # "-fsycl" without it, so replay reconstructed the wrong (SYCL-enabled)
    # AST for what the real build actually compiled as plain C++ (Codex
    # review, second round). Extraction is argv-order-preserving, so
    # capturing both spellings lets the same last-flag-wins semantics reach
    # replay unchanged.
    "-fsycl",
    "-fno-sycl",
)

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
#: _EffectiveContextSignature``, :func:`~abicheck.buildsource.adapters.adapter_shared.
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
#: imports :func:`strip_launchers` from :mod:`.source_extractors.compile_invocation` at its own top level
#: (:func:`~abicheck.buildsource.adapters.adapter_shared._executable_token_positions`)
#: -- a reverse ``compile_invocation -> adapters.base`` import would be a genuine import
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
    through L4 source replay (:func:`replayed_tokens` /
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


#: Option prefixes that load code into the compiler. ``-fplugin=`` and
#: ``-fpass-plugin=`` name a shared object clang dlopens during option handling.
_CODE_LOADING_PREFIXES: tuple[str, ...] = ("-fplugin", "-fpass-plugin")

#: cc1 flags that load or run code, spelled ``-Xclang <flag> [-Xclang <operand>]``
#: (``-plugin-arg-<name>`` takes an operand too). ``-load`` and ``-add-plugin``
#: are also dropped bare, with their operand, though a driver rejects them.
_CC1_CODE_LOADING_FLAGS: tuple[str, ...] = (
    "-load",
    "-add-plugin",
    "-plugin-arg-",
)


def drop_code_loading_options(argv: list[str]) -> list[str]:
    """*argv* without any option that makes the compiler load code.

    A build pack is untrusted evidence, and a flag it carries (``-fplugin=…``,
    ``-Xclang -load -Xclang …``) would run a shared object while clang is still
    processing options, before any parse. Only these forms are removed; every
    other token -- ABI flags, forced includes, split-operand survivors -- passes
    through in order, so a genuine build is unchanged.
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith(_CODE_LOADING_PREFIXES):
            i += 1
            continue
        if tok in ("-load", "-add-plugin"):
            # The bare driver spelling, with its operand.
            i += 2
            continue
        if (
            tok == "-Xclang"
            and i + 1 < len(argv)
            and argv[i + 1].startswith(_CC1_CODE_LOADING_FLAGS)
        ):
            # ``-Xclang <flag>`` and, when the flag takes one, its ``-Xclang <value>``.
            i += 2
            if i + 1 < len(argv) and argv[i] == "-Xclang":
                i += 2
            continue
        out.append(tok)
        i += 1
    return out

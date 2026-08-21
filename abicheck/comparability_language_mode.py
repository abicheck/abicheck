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

"""ADR-050 D1/D2 comparability gate -- the two C/C++ ``language_standard``
corroboration carve-outs, split out of ``comparability.py`` (Codex review,
eighth review round on the same carve-out cluster: ``comparability.py``
was already at its 2000-line AI-readiness hard cap, with no docstring
trimming left to absorb the hybrid-frontend-corroboration fix this module
was extracted alongside). :func:`check_contracts_comparable` in
``comparability.py`` is this module's only caller.

Two carve-outs live here, each answering a narrower question than the raw
``language_standard`` profile-fingerprint field can on its own:

- :func:`language_standard_probe_upgrade_corroborated` -- a baseline
  persisted before ``dumper_toolchain``'s standard-probing existed reads as
  a genuine toolchain difference against a freshly re-dumped snapshot of
  the identical input, purely because abicheck itself was upgraded.
- :func:`language_standard_content_divergence_corroborated` -- old and new
  headers auto-detect into genuinely different C/C++ language *modes*
  purely from their own content (losing an ``extern "C"`` wrapper, gaining
  a C++-only construct), which is real ABI-relevant signal, not evidence
  of a different extraction environment, once the toolchain identity that
  produced both sides is independently corroborated.
"""

from __future__ import annotations

from pathlib import PureWindowsPath

from .model import AbiSnapshot

#: The literal ``language_standard`` prefix a probed (never explicitly
#: pinned) default carries -- see ``dumper_toolchain._probe_default_
#: language_standard``'s own docstring. Mirrored here (not imported) since
#: this module has no other reason to depend on ``dumper_toolchain``.
_PROBED_STANDARD_PREFIX = "probed:"

#: The literal standard both header-AST command builders unconditionally
#: force for an unpinned C/gnu-dialect parse -- mirrors
#: ``dumper_toolchain._FORCED_C_STANDARD`` (not imported, same reasoning as
#: ``_PROBED_STANDARD_PREFIX`` above).
_FORCED_C_STANDARD = "gnu11"

#: Mirrors ``dumper_toolchain._resolve_standard_provenance``'s ``"gnu++20"``
#: literal for a ``force_cpp20`` heuristic hit (not imported, same
#: reasoning as ``_PROBED_STANDARD_PREFIX`` above).
_FORCED_CPP20_STANDARD = "gnu++20"

#: Per resolved language *mode*, the one bare (non-``"probed:"``-prefixed)
#: ``language_standard`` literal an *unpinned* parse can ever actually
#: produce -- used by
#: :func:`language_standard_content_divergence_corroborated` to reject a
#: bare value that merely happens to collide with one of these (Codex
#: review: ``-std=gnu11``/``-std=gnu++17`` given without ``--lang`` produces
#: the identical bare literal an unpinned parse would, and
#: ``_language_standard_is_lang_pinned`` only recognizes an explicit *lang
#: tag*, not an explicit *standard* given without one). Any other bare
#: literal can only have come from an explicit pin.
_KNOWN_AUTO_RESOLVED_BARE_STANDARDS = {
    "c": _FORCED_C_STANDARD,
    "c++": _FORCED_CPP20_STANDARD,
}

#: Every ``language_standard_field`` spelling an *unpinned, no-resolved-
#: standard* dump could have produced before either the probe or the
#: forced-``gnu11`` report existed, that this carve-out can still safely
#: corroborate: an explicit ``--lang`` with nothing else resolved (bare
#: ``"c"``/``"c++"``/``"cpp"`` -- ``"cpp"`` is a second, still-supported
#: spelling for C++ alongside ``"c++"``, per :func:`_resolve_force_cpp`'s
#: own ``lang.upper() in ("C++", "CPP")`` check; ``language_standard_field``
#: lowercases but does not otherwise canonicalize the tag, so the two
#: spellings persist as distinct strings, not merely distinct casings —
#: Codex review, fresh evidence). Deliberately excludes the bare empty
#: string (no ``--lang`` given at all, pure content-based auto-detection)
#: -- see :func:`_newly_resolved_standard_remainder`'s own docstring for why.
_UNRESOLVED_STANDARD_SPELLINGS = ("c", "c++", "cpp")


def _newly_resolved_standard_remainder(old_std: str, new_std: str) -> str | None:
    """If *old_std* is one of :data:`_UNRESOLVED_STANDARD_SPELLINGS` and
    *new_std* carries the identical lang tag plus something newly resolved,
    return that "something" (the part after the tag); otherwise ``None``.

    Split out of :func:`language_standard_probe_upgrade_corroborated` so
    the "same lang tag, newly populated" structural check has one
    definition, checked in both directions there (Codex review, fresh
    evidence: an explicit-``--lang`` baseline moves from a bare ``"c"``/
    ``"c++"`` to ``"c:gnu11"``/``"c++:probed:..."``, not from an empty
    string, which an earlier version of this check could not recognize).

    Deliberately does **not** accept a bare empty *old_std* (no ``--lang``
    given at all) the way an earlier version of this function did (Codex
    review, fresh evidence): an empty ``language_standard`` carries *no*
    signal about which language mode a pre-upgrade, pure-auto-detection
    dump actually resolved to (:func:`_resolve_force_cpp`'s decision is a
    function of the header *content*, which this carve-out has no access
    to) -- so a header that later gains enough C++-only syntax to flip
    ``_resolve_force_cpp``'s decision (a real language-mode change, not an
    upgrade artifact) would otherwise be indistinguishable from the
    upgrade-only case this carve-out exists to waive, and a matching
    ``compiler_family``/``compiler_version``/``compiler_sha256`` says
    nothing about which mode either side's *headers* actually resolved to.
    An explicit ``--lang c++``/``"cpp"`` has no such ambiguity:
    :func:`_resolve_force_cpp` returns C++ mode unconditionally for those,
    with no retry that could revise it downward, so the lang-tag-equality
    check above is sufficient corroboration on its own. An explicit
    ``--lang c`` is a narrower exception this function's own signature
    cannot see: ``dumper.py``'s C->C++ self-heal retry can still override
    it mid-parse when a header turns out to need a C++ stdlib header, so a
    ``"c"`` tag alone does not prove the *actual* parse stayed C --
    :func:`language_standard_probe_upgrade_corroborated` checks the
    resolved remainder's own content for that case specifically (Codex
    review, fresh evidence).
    """
    if old_std not in _UNRESOLVED_STANDARD_SPELLINGS:
        return None
    prefix = f"{old_std}:"
    if not new_std.startswith(prefix):
        return None
    return new_std[len(prefix) :]


def language_standard_probe_upgrade_corroborated(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_fields: dict[str, str],
    new_fields: dict[str, str],
) -> bool:
    """Whether a differing ``language_standard`` is fully explained by this
    probe (or the sibling forced-``gnu11`` report) having been *added* by an
    abicheck upgrade, not by a genuine toolchain difference (Codex review,
    fresh evidence).

    A baseline persisted before ``dumper_toolchain._probe_default_language_
    standard``/the forced-C-standard report existed recorded a bare
    ``"c"``/``"c++"`` (:data:`_UNRESOLVED_STANDARD_SPELLINGS`) for an
    unpinned (no explicit ``-std=``, no forced-C++20-heuristic) dump given
    an explicit ``--lang`` -- that was the only value this field could ever
    take for that shape of input. A freshly re-dumped snapshot of the
    identical input under the identical toolchain now records a real,
    newly-resolved value there instead (:data:`_FORCED_C_STANDARD` for an
    unpinned C/gnu parse, or a ``"probed:..."`` value for an unpinned C++
    parse or an MSVC-dialect C parse), purely because the tool was upgraded
    -- comparing the two would otherwise raise ``ProfileMismatchError`` on
    every such baseline, solely from the upgrade itself, not from anything
    about the library changing. :func:`_newly_resolved_standard_remainder`
    checks both directions share the same lang tag and isolates the
    newly-resolved remainder for the check below -- see that function's own
    docstring for why a bare *empty* ``language_standard`` (no ``--lang`` at
    all) is deliberately **not** eligible here, unlike an earlier version of
    this carve-out.

    Waived only when independently corroborated by an EXACT, non-empty
    ``compiler_family``/``compiler_version`` match on both sides: the
    probed default is a deterministic function of the exact resolved
    compiler binary, so an unchanged ``compiler_version`` is strong,
    specific evidence the unpinned default did not actually change either
    -- mirroring the platform carve-out's own "verify each specific
    differing field, not just some other field on the same axis" discipline
    (``comparability._platform_identity_confirmed``). A *changed*
    ``compiler_version`` already raises today (no carve-out exists for it,
    and none is added here), which is correct: upgrading the compiler
    between the baseline and the comparison is a real profile change this
    gate should still catch.

    When both sides' ``AbiSnapshot.ast_toolchain`` carry a non-empty
    ``compiler_sha256`` (Codex review, fresh evidence), that content-address
    is also required to match: a compiler *wrapper* replaced at the same
    path can report an identical ``compiler_family``/``compiler_version``
    string while actually selecting a different default dialect --
    ``compiler_version`` is text a wrapper's own ``--version`` output
    chooses to emit, not a fact this tool independently verifies, whereas
    ``compiler_sha256`` is the resolved binary's own content hash
    (``dumper_toolchain._tool_identity_metadata``) and cannot lie the same
    way. Checked only when available on *both* sides -- an older/legacy
    snapshot predating this field, or a side whose compiler resolution
    itself failed (``ast_toolchain["compiler_error"]``, no ``compiler_*``
    keys at all), falls back to the family/version check above rather than
    failing closed on missing evidence it was never in a position to carry.

    The ``"probed:"`` marker is checked by *containment*, not
    ``str.startswith`` (Codex review, fresh evidence): when a dump also
    pins an explicit ``--lang``, ``language_standard_field`` prefixes the
    probed value with ``"c++:"``/``"c:"`` (e.g.
    ``"c++:probed:__cplusplus=201703L"``), so the marker does not
    necessarily sit at position 0 -- mirroring
    ``dumper_toolchain._cplusplus_macro_for_standard``'s identical fix.

    **Known, narrower residual (Codex review, fresh evidence), not fixed
    here**: the ``compiler_family``/``compiler_version`` corroboration
    above reads ``dumper_toolchain._stamp_ast_parser``'s ``compiler_*``
    stamping, which this same PR also fixed to resolve against the
    header-AST parse's *actual* post-retry compiler (``resolved_compiler``)
    rather than the caller's original, unresolved request -- see that
    function's own docstring. For a castxml dump where those two diverge
    (a force_cpp self-heal/remap changed which binary was actually
    invoked), a **legacy baseline persisted by pre-fix abicheck** recorded
    ``compiler_selected``/``compiler_family``/``compiler_version`` from the
    *wrong*, unresolved binary -- so comparing it against a freshly
    re-dumped snapshot of the identical input under the identical
    toolchain installation can now see a *changed* ``compiler_family``/
    ``compiler_version`` purely because this PR's own stamping fix
    corrected which binary's identity gets recorded, not because the
    installation changed. That is the same "an abicheck upgrade must not
    by itself make an unchanged baseline ``NOT_COMPARABLE``" problem this
    whole carve-out exists to solve, just on the corroboration axis rather
    than the ``language_standard`` axis it corroborates -- and it
    compounds: a real, waivable ``language_standard`` transition on such a
    baseline now also fails this function's own compiler-identity check,
    so the corroboration this carve-out depends on is unavailable exactly
    when the legacy stamping bug applies. Deliberately not addressed with
    a further carve-out: there is no sound way, from either snapshot's
    persisted content alone, to distinguish "this compiler_family/version
    difference is purely the stamping fix correcting a mis-recorded legacy
    baseline" from "the installation's compiler genuinely changed between
    the two dumps" -- the old baseline recorded only the (wrong) binary it
    picked, never what the corrected resolution *would* have produced, so
    there is no fact to corroborate against. Affected users should
    re-``dump`` their baseline once after upgrading past this fix rather
    than rely on the carve-out to bridge it, the same guidance any
    ``ProfileMismatchError`` from an unrelated real toolchain change
    already implies.
    """
    old_std = old_fields.get("language_standard", "")
    new_std = new_fields.get("language_standard", "")
    forward = _newly_resolved_standard_remainder(old_std, new_std)
    tag, remainder = (
        (old_std, forward)
        if forward is not None
        else (new_std, _newly_resolved_standard_remainder(new_std, old_std))
    )
    if remainder is None:
        return False
    is_newly_resolved = (
        remainder == _FORCED_C_STANDARD or _PROBED_STANDARD_PREFIX in remainder
    )
    if not is_newly_resolved:
        return False
    # Codex review, fresh evidence: an explicit "c" tag does not pin the
    # mode unconditionally the way this function's docstring above assumes
    # -- both header-AST backends self-heal an explicit --lang c request
    # into C++ when the header turns out to need a C++ stdlib header
    # (dumper.py's C->C++ retry applies regardless of whether C mode was
    # auto-detected or explicitly requested). A self-healed parse's probed
    # value always names __cplusplus (never __STDC_VERSION__, and never the
    # bare _FORCED_C_STANDARD literal, which _resolve_standard_provenance
    # only ever returns for a genuinely-C final mode) -- so a "c"-tagged
    # remainder containing it is real evidence the *new* side's actual
    # parse mode diverged from its own explicit tag, not merely newly
    # discovered upgrade evidence, and must not be waived.
    if tag == "c" and "__cplusplus" in remainder:
        return False
    for key in ("compiler_family", "compiler_version"):
        old_v = old_fields.get(key, "")
        new_v = new_fields.get(key, "")
        if not old_v or not new_v or old_v != new_v:
            return False
    old_sha = _tc(old.ast_toolchain, "compiler_sha256")
    new_sha = _tc(new.ast_toolchain, "compiler_sha256")
    if old_sha and new_sha and old_sha != new_sha:
        return False
    return True


def _language_standard_is_lang_pinned(std: str) -> bool:
    """Whether *std* reflects an explicit ``--lang`` given by the caller,
    as opposed to pure content-based auto-detection
    (:func:`dumper_toolchain._resolve_force_cpp`).

    ``language_standard_field`` only ever prefixes the resolved standard
    with a lang tag (``"c:"``/``"c++:"``/``"cpp:"``) -- or the bare tag,
    pre-probe -- when ``lang`` was actually passed; a pure auto-detected
    value (the bare :data:`_FORCED_C_STANDARD` literal, a ``"probed:..."``
    value, or ``force_cpp20``'s ``"gnu++20"``) never carries one."""
    return std in _UNRESOLVED_STANDARD_SPELLINGS or std.startswith(
        tuple(f"{tag}:" for tag in _UNRESOLVED_STANDARD_SPELLINGS)
    )


def _language_standard_is_known_auto_resolved_form(std: str, mode: str) -> bool:
    """Whether *std* (an un-lang-pinned ``language_standard`` value) is a
    form :func:`dumper_toolchain._resolve_standard_provenance` can actually
    produce for an *unpinned* parse resolved to language *mode* (``"c"``/
    ``"c++"``) -- as opposed to a bare literal that merely happens to look
    like one (Codex review, real finding on this PR).

    An explicit ``-std=gnu11``/``-std=gnu++17`` given with no ``--lang`` at
    all is not caught by :func:`_language_standard_is_lang_pinned` (which
    only recognizes an explicit *lang tag*), and
    :func:`dumper_toolchain._extract_explicit_std_value` returns that value
    verbatim with no marker distinguishing it from an auto-resolved one.
    Most explicit standards are harmless here (a literal like ``"c++17"``
    is never something the unpinned path can produce either), but
    ``-std=gnu11``/``-std=gnu++20`` collide exactly with
    :data:`_FORCED_C_STANDARD`/:data:`_FORCED_CPP20_STANDARD` -- an
    explicit, toolchain-config-driven divergence between those two literals
    would otherwise read as "purely content-driven" and be wrongly waived.

    A ``"probed:..."``-prefixed value is always genuine (only
    :func:`dumper_toolchain._probe_default_language_standard` ever produces
    it); a bare literal is only trusted when it exactly matches the one
    form the unpinned path can produce for *this* mode
    (:data:`_KNOWN_AUTO_RESOLVED_BARE_STANDARDS`) -- any other bare literal
    can only have come from an explicit pin.
    """
    if std.startswith(_PROBED_STANDARD_PREFIX):
        return True
    return std == _KNOWN_AUTO_RESOLVED_BARE_STANDARDS.get(mode)


def _compiler_version_sans_driver_name(version: str) -> str:
    """Strip a leading driver-name token from a raw ``--version`` banner
    (real CI failure, Codex review): castxml resolves a language-mode-
    specific host compiler (``dumper_ast_config._resolve_compiler_binary``
    maps C mode to ``gcc``/``cc``, C++ to ``g++``/``c++``), so a GNU
    compiler's banner differs *only* in that leading word between the two
    modes for the identical toolchain: ``"gcc (Ubuntu 13.3.0-...)
    13.3.0..."`` vs. ``"g++ (Ubuntu 13.3.0-...) 13.3.0..."``. A no-op for
    clang (``clang``/``clang++`` report byte-identical banners) and for two
    genuinely different compiler versions, whose remainders still differ.
    """
    parts = version.split(None, 1)
    return parts[1] if len(parts) == 2 else version


def _tc(ast_toolchain: dict[str, str], key: str) -> str:
    """*key* from *ast_toolchain*, falling back to the castxml leg's own
    value for a hybrid-merged snapshot: ``dumper_hybrid._merge_snapshots``
    prefixes every key ``castxml_``/``clang_`` there, leaving no bare key
    at all, so a plain ``.get(key)`` never corroborated a hybrid dump.
    castxml is that module's own documented "base" leg."""
    return ast_toolchain.get(key) or ast_toolchain.get(f"castxml_{key}", "")


def _compiler_binary_path(ast_toolchain: dict[str, str]) -> str:
    """The resolved host compiler's own on-disk path (``compiler_realpath``,
    falling back to ``compiler_selected``)."""
    return _tc(ast_toolchain, "compiler_realpath") or _tc(
        ast_toolchain, "compiler_selected"
    )


def _compiler_install_dir(ast_toolchain: dict[str, str]) -> str:
    """The resolved host compiler's own directory -- proof of one toolchain
    install. ``PureWindowsPath``, not ``Path``: a persisted path may be
    from another OS."""
    path = _compiler_binary_path(ast_toolchain)
    return str(PureWindowsPath(path).parent) if path else ""


def _nonempty_match(old_value: str, new_value: str) -> bool:
    """Both sides carry the identical, non-empty value."""
    return bool(old_value) and bool(new_value) and old_value == new_value


_HYBRID_LEG_PREFIXES = ("castxml_", "clang_")


def _tc_match(old_tc: dict[str, str], new_tc: dict[str, str], key: str) -> bool:
    """Whether *old_tc*/*new_tc* corroborate on *key*. An ordinary
    (bare-keyed) snapshot needs both sides' bare value non-empty and equal.
    A hybrid-merged snapshot has no bare key -- EVERY castxml_/clang_ leg
    present on either side must independently corroborate (Codex review,
    fresh evidence): checking only the castxml leg let a real clang-leg
    identity drift (a changed clang frontend/compiler between snapshots)
    go uncaught whenever castxml's own leg happened to stay unchanged."""
    if key in old_tc or key in new_tc:
        return _nonempty_match(old_tc.get(key, ""), new_tc.get(key, ""))
    legs = {
        p
        for p in _HYBRID_LEG_PREFIXES
        if f"{p}{key}" in old_tc or f"{p}{key}" in new_tc
    }
    return bool(legs) and all(
        _nonempty_match(old_tc.get(f"{p}{key}", ""), new_tc.get(f"{p}{key}", ""))
        for p in legs
    )


def _driver_prefix(word: str, bare: str, suffix: str) -> str | None:
    """*word*'s prefix if it's a ``bare``/``*-suffix`` driver name (``""``
    for the bare form), else ``None``."""
    if word == bare:
        return ""
    return word[: -len(suffix)] if word.endswith(suffix) else None


def _is_gcc_gxx_driver_pair(old_version: str, new_version: str) -> bool:
    """Whether each banner's leading token is genuinely a C vs. C++ driver
    from the *same* toolchain -- not just two words separately matching
    ``gcc``/``g++`` by suffix: ``vendor-a-g++``/``vendor-b-gcc`` both end
    in a recognized suffix but are unrelated builds, so the two sides'
    cross-compile *prefixes* (``""`` for a bare ``gcc``/``g++``) must
    also match."""
    if not old_version or not new_version:
        return False
    # A whitespace-only (truthy) banner's .split() is empty -- guard before
    # indexing [0] (CodeRabbit review). MinGW leads with "gcc.exe"/"g++.exe".
    old_tokens, new_tokens = old_version.split(), new_version.split()
    if not old_tokens or not new_tokens:
        return False
    old_word = old_tokens[0].lower().removesuffix(".exe")
    new_word = new_tokens[0].lower().removesuffix(".exe")
    dp = _driver_prefix
    old_c, old_cxx = dp(old_word, "cc", "gcc"), dp(old_word, "c++", "g++")
    new_c, new_cxx = dp(new_word, "cc", "gcc"), dp(new_word, "c++", "g++")
    return (old_c is not None and old_c == new_cxx) or (
        old_cxx is not None and old_cxx == new_c
    )


def language_standard_content_divergence_corroborated(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_fields: dict[str, str],
    new_fields: dict[str, str],
) -> bool:
    """Whether a differing, purely content-driven ``language_standard`` is
    safe to waive -- distinct from
    :func:`language_standard_probe_upgrade_corroborated`'s narrower
    "an abicheck upgrade added the probe" case (real CI failure:
    examples/case66_language_linkage_changed, case69_trivial_to_nontrivial).
    Losing an ``extern "C"`` wrapper or gaining a C++-only construct with
    no explicit ``--lang`` on either side is a real, ABI-relevant edit, not
    evidence of a different extraction *environment* (ADR-050 D1/D2) under
    an identical, corroborated toolchain -- real signal for the dedicated
    detectors to report.

    **Scoped to a genuine mode switch (``c`` <-> ``c++``), not a differing
    edition within the same mode** (pinned by ``test_dumper_contract_
    wiring.py::test_cpp20_heuristic_forced_standard_flows_into_profile_
    fingerprint``): two header sets both parsed as C++ but resolving to a
    different *edition* only because ``force_cpp20`` fires on one side are
    still genuinely different dialects. Checked via ``resolved_lang_mode``.

    Waived only when: (1) neither side's non-empty ``language_standard``
    reflects an explicit ``--lang`` (:func:`_language_standard_is_lang_pinned`);
    (2) each side's non-empty value is a form the *unpinned* path can
    actually produce for its own mode
    (:func:`_language_standard_is_known_auto_resolved_form` -- an explicit
    ``-std=gnu11``/``-std=gnu++17`` given without ``--lang`` collides with
    the unpinned literal, invisible to check (1)); and (3) corroborated by
    matching ``compiler_family``/``producer``/frontend ``version``, a
    driver-normalized ``compiler_version``, and ``compiler_sha256`` --
    skipped only for a castxml gcc/g++ pair sharing an install dir and
    ``compiler_target_triple`` (see code)."""
    old_std = old_fields.get("language_standard", "")
    new_std = new_fields.get("language_standard", "")
    if old_std == new_std:
        return False
    # A bare-empty side (the probe can reject unrelated to language mode --
    # real Windows CI failure) is deliberately not excluded, unlike the
    # sibling carve-out: `resolved_lang_mode` below already proves the mode
    # switch regardless of probe success.
    if old_std and _language_standard_is_lang_pinned(old_std):
        return False
    if new_std and _language_standard_is_lang_pinned(new_std):
        return False
    old_mode = _tc(old.ast_toolchain, "resolved_lang_mode")
    new_mode = _tc(new.ast_toolchain, "resolved_lang_mode")
    if (
        not old_mode
        or not new_mode
        or old_mode == new_mode
        or old_mode not in ("c", "c++")
        or new_mode not in ("c", "c++")
    ):
        return False
    if old_std and not _language_standard_is_known_auto_resolved_form(
        old_std, old_mode
    ):
        # A bare literal not in the unpinned-producible set can only be an
        # explicit -std=/--std=/std: pin given without --lang -- invisible
        # to the lang-pinned check above, so this is a separate guard.
        return False
    if new_std and not _language_standard_is_known_auto_resolved_form(
        new_std, new_mode
    ):
        return False
    # The check above still can't tell apart the exact-collision pair -- an
    # explicit -std=gnu11/-std=gnu++20 given without --lang produces the
    # identical literal :data:`_KNOWN_AUTO_RESOLVED_BARE_STANDARDS` uses.
    # Needs real provenance: AbiSnapshot.ast_toolchain[
    # "language_standard_explicit"] (dumper_toolchain._stamp_ast_parser)
    # records whether the caller gave an explicit pin. Missing on either
    # side (a legacy pre-provenance snapshot) fails closed.
    if (
        _tc(old.ast_toolchain, "language_standard_explicit") != "0"
        or _tc(new.ast_toolchain, "language_standard_explicit") != "0"
    ):
        return False
    old_family = old_fields.get("compiler_family", "")
    new_family = new_fields.get("compiler_family", "")
    if not _nonempty_match(old_family, new_family):
        return False
    old_producer = _tc(old.ast_toolchain, "producer")
    if not _tc_match(old.ast_toolchain, new.ast_toolchain, "producer"):
        return False
    # The frontend's OWN version AND content hash must match, unconditionally
    # -- confirms the same build parsed both sides. Unlike the host
    # compiler's sha256 (skipped for a real gcc/g++ split), the frontend is
    # the *same* binary invoked twice, so a vendor-patched rebuild reporting
    # an unchanged --version string is still caught here. `_tc_match`
    # (not `_tc`) validates every hybrid leg independently -- a lone-castxml
    # check would miss a clang-leg-only identity drift.
    if not _tc_match(old.ast_toolchain, new.ast_toolchain, "version") or not _tc_match(
        old.ast_toolchain, new.ast_toolchain, "sha256"
    ):
        return False
    old_host_version = _tc(old.ast_toolchain, "compiler_version")
    new_host_version = _tc(new.ast_toolchain, "compiler_version")
    if not old_host_version or not new_host_version:
        return False
    banners_differ = old_host_version != new_host_version
    if banners_differ and _compiler_version_sans_driver_name(
        old_host_version
    ) != _compiler_version_sans_driver_name(new_host_version):
        # castxml resolves a separate host-compiler binary per side
        # ("gcc"/"g++"), differing only in that leading word under one
        # unchanged toolchain; a real cross-version skew still differs.
        return False
    # sha256 skipped only for: castxml, gnu, a real gcc/g++ pair
    # (_is_gcc_gxx_driver_pair), same install dir, matching resolved-binary
    # driver prefix, AND compiler_target_triple -- dir/banner alone still
    # accept two cross-compilers side by side with a shared banner but
    # different architectures. Missing either side (a best-effort probe)
    # fails closed, not "unknown = same".
    #
    # The resolved-binary check (Codex review, fresh evidence) is a
    # DIFFERENT signal from the banner-driver-pair check above, not a
    # duplicate of it: two distinct wrapper scripts in one directory
    # (`/opt/bin/vendor-a-g++`, `/opt/bin/vendor-b-gcc`) can each
    # faithfully delegate `--version` to the *same* real, bare "gcc"/"g++"
    # underneath -- passing the banner-pair check, the shared directory,
    # and a shared target triple -- while still being genuinely different
    # tools (different injected flags) with genuinely different
    # `compiler_sha256`. `_is_gcc_gxx_driver_pair` reused directly against
    # the two full resolved paths (not just their basenames): since the
    # directory is already required to match, the leading path segment is
    # identical on both sides, leaving exactly the vendor-specific
    # basename prefix (`vendor-a-`/`vendor-b-`) as what must also agree --
    # which correctly rejects this pair even though every other signal
    # above already passed.
    old_dir = _compiler_install_dir(old.ast_toolchain)
    new_dir = _compiler_install_dir(new.ast_toolchain)
    old_bin = _compiler_binary_path(old.ast_toolchain)
    new_bin = _compiler_binary_path(new.ast_toolchain)
    old_triple = _tc(old.ast_toolchain, "compiler_target_triple")
    new_triple = _tc(new.ast_toolchain, "compiler_target_triple")
    if not (
        banners_differ
        and old_producer == "castxml"
        and old_family == "gnu"
        and _is_gcc_gxx_driver_pair(old_host_version, new_host_version)
        and _is_gcc_gxx_driver_pair(old_bin, new_bin)
        and old_triple
        and old_triple == new_triple
        and old_dir
        and old_dir == new_dir
    ):
        old_sha = _tc(old.ast_toolchain, "compiler_sha256")
        new_sha = _tc(new.ast_toolchain, "compiler_sha256")
        if old_sha and new_sha and old_sha != new_sha:
            return False
    return True

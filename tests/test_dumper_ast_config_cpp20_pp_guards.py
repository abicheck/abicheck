# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Regressions for ``_strip_inactive_if_zero_blocks``, the preprocessor-
guard masker that feeds the C++20 detector's shadow/requirements scan.

Split out of ``test_dumper_ast_config_cpp20.py`` (which crossed the
AI-readiness 2000-line hard cap) -- this file covers every "Codex review"
round of that masker specifically: type-name shadowing via a disabled
``#if 0`` stub, ``#elif``/``#else`` arm reachability, unconditionally-true
openings (``#if 1``/``#if true``), ``__cplusplus``/feature-test-macro
guard circularity, and nested ``#if`` chains inside a reachable arm. See
``test_dumper_ast_config_cpp20.py`` for the detector's other regressions
(literal/comment/string handling, abbreviated-auto-parameter detection,
etc.) and AGENTS.md task "P0: fix false-positive C++20 auto-detection".
"""

from __future__ import annotations

from pathlib import Path

from abicheck.dumper_ast_config import _detect_cpp20_headers, _find_cpp20_requirements


def _write(tmp_path: Path, name: str, content: str) -> list[Path]:
    p = tmp_path / name
    p.write_text(content)
    return [p]


def test_cpp20_detector_ignores_consteval_as_shadowed_type_name(tmp_path):
    """Regression (Codex review, third round): the "followed by an
    identifier-starting token" lookahead alone can't distinguish a genuine
    specifier from a pre-C++20 header that instead declares a *type*
    literally named "consteval" (``struct consteval {};``) and later
    references it followed by another decl-specifier or cv-qualifier
    (``consteval const *p;`` — legal pre-C++20: decl-specifier order is
    flexible, so this means the same as ``const consteval *p;``) — the
    textual shape is identical to a genuine ``consteval <type> <name>``
    declaration. Once the header is confirmed to declare "consteval" as a
    type anywhere, every bare occurrence must be treated as ambiguous,
    mirroring the existing "concept" type-shadow check."""
    headers = _write(tmp_path, "a.h", "struct consteval {};\nconsteval const *p;\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_constinit_as_shadowed_type_name(tmp_path):
    """Companion: ``constinit`` shadowed as a type name via ``using``."""
    headers = _write(tmp_path, "a.h", "using constinit = int;\nconstinit long x;\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_concept_shadowed_via_union(tmp_path):
    """Regression (Codex review, sixth round): the shadowed-type-name
    patterns only recognized ``struct``/``class`` class-keys, so a
    pre-C++20 header declaring "concept" as a ``union`` (or ``enum``) —
    just as legal pre-C++20 as the class-key form — was invisible to the
    shadow check."""
    headers = _write(
        tmp_path,
        "a.h",
        "union concept { int x; };\ntemplate<class T> concept C = {0};\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_consteval_shadowed_via_enum(tmp_path):
    """Companion: ``consteval`` shadowed via ``enum`` instead of ``union``."""
    headers = _write(tmp_path, "a.h", "enum consteval { A, B };\nconsteval const *p;\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_requires_as_shadowed_type_name(tmp_path):
    """Regression (Codex review, sixth round): "requires" only became a
    reserved keyword in C++20 — a pre-C++20 header can legally declare a
    type literally named "requires" and use it as a variable template's
    type (``template<class T> requires value = {};``). The template
    header's closing ``>`` directly precedes "requires" either way, so
    the preceding-template-header positive signal alone can't distinguish
    this from a genuine requires-clause; mirrors the existing
    consteval/constinit/concept type-shadow checks."""
    headers = _write(
        tmp_path,
        "a.h",
        "struct requires {};\ntemplate<class T> requires value = {};\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_still_detects_genuine_requires_clause(tmp_path):
    """Companion: an actual requires-clause (no shadowed type name
    anywhere in the header) must still be detected."""
    headers = _write(
        tmp_path,
        "a.h",
        "template<class T> requires std::integral<T> void f(T);\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "requires-clause" for r in reqs)


def test_cpp20_detector_ignores_consteval_as_shadowed_typedef_type_name(tmp_path):
    """Companion: shadowed via ``typedef`` rather than ``struct``/``using``."""
    headers = _write(tmp_path, "a.h", "typedef int consteval;\nconsteval unsigned x;\n")
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_commented_out_consteval_type_shadow(tmp_path):
    """Companion: a *commented-out* ``struct consteval {};`` must not
    suppress a genuine ``consteval`` declaration elsewhere in the same
    header — mirrors the "concept" shadow check's identical comment
    exclusion."""
    headers = _write(
        tmp_path,
        "a.h",
        "// struct consteval {};\nconsteval int f() { return 1; }\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_inactive_if_zero_consteval_type_shadow(tmp_path):
    """Regression (Codex review): a compatibility stub disabled via
    ``#if 0``/``#endif`` must not suppress a genuine ``consteval``
    declaration elsewhere in the header — mirrors the commented-out-shadow
    exclusion above, but for a preprocessor-inactive region rather than a
    ``//`` comment."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nstruct consteval {};\n#endif\nconsteval int f() { return 1; }\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_inactive_if_zero_consteval_type_shadow_crlf(tmp_path):
    """Regression (Windows CI): the same disabled ``#if 0`` stub, but with
    CRLF line endings (as produced by a CRLF source file, or by a
    text-mode write on Windows). The stripped-region matching must not
    depend on a bare "\\n" terminator — a trailing "\\r" left over from
    splitting on "\\n" alone previously defeated ``_PP_IF_ZERO_PATTERN``'s
    end-anchor, so the stub was never recognized as inactive."""
    p = tmp_path / "a.h"
    p.write_bytes(
        b"#if 0\r\nstruct consteval {};\r\n#endif\r\nconsteval int f() { return 1; }\r\n"
    )
    headers = [p]
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_consteval_declared_only_in_if_zero_block(tmp_path):
    """Regression (Codex review): a genuine ``consteval`` *declaration*
    written only inside a disabled ``#if 0`` block must not itself mark the
    header as needing C++20 — it's never actually compiled. The active code
    outside the block uses "consteval" the pre-C++20 way (an ordinary
    identifier), so the header is really C/pre-C++20 throughout."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nconsteval int f();\n#endif\nint consteval;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_still_detects_consteval_alongside_if_zero_block(tmp_path):
    """Companion: a genuine *active* ``consteval`` declaration elsewhere in
    the same header must still be detected even when an unrelated ``#if 0``
    block is also present, confirming the stripping doesn't over-suppress."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nconsteval int f();\n#endif\nconsteval int g();\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" and r.line == 4 for r in reqs)


def test_cpp20_detector_still_shadows_active_if_zero_consteval_type(tmp_path):
    """Companion: an *active* (non-``#if 0``) ``struct consteval {};`` must
    still shadow, confirming the ``#if 0`` stripping doesn't over-strip."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 1\nstruct consteval {};\n#endif\nconsteval const *p;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_construct_in_active_else_arm_of_if_zero(tmp_path):
    """Regression (Codex review): ``#if 0``'s guard is unconditionally
    false, so its ``#else`` arm is unconditionally reachable — a genuine
    C++20 construct written only there must still be detected. Masking the
    whole ``#if 0``...``#endif`` span (including the active ``#else`` arm)
    previously hid it."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint dummy;\n#else\ntemplate<class T> concept C = true;\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" and r.line == 4 for r in reqs)


def test_cpp20_detector_detects_construct_in_else_after_elif(tmp_path):
    """Companion: the same reachable-``#else`` reasoning holds when a
    permanently-false ``#elif 0`` sits between the ``#if 0`` and the
    ``#else`` — the construct in ``#else`` must still be found."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif 0\nint b;\n#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_still_ignores_truly_inactive_if_zero_without_else(tmp_path):
    """Companion: an ``#if 0`` block with *no* ``#else`` stays fully masked
    — nothing in it is reachable, so it must not itself trigger detection."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\ntemplate<class T> concept C = true;\n#endif\nint x;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_construct_in_permanently_false_elif(tmp_path):
    """Regression (Codex review, second round): a ``#elif 0``/``#elif
    false`` arm is itself permanently unreachable, exactly like the
    ``#if 0`` guard before it — a construct written only there must stay
    masked, not treated as reachable the way a genuinely unevaluable
    ``#elif <macro>`` condition is. Only the trailing pre-C++20 ``int
    consteval;`` outside the conditional is active."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif 0\nconsteval int f();\n#endif\nint consteval;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_construct_in_elif_after_permanently_false_elif(
    tmp_path,
):
    """Companion: a genuinely unevaluable ``#elif`` after a permanently-false
    ``#elif 0`` must still stop masking and be scanned — the "keep masking
    for elif-0" rule must not swallow a later, truly unknown arm."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif 0\nint b;\n#elif SOME_MACRO\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_construct_in_dead_arm_after_elif_true(tmp_path):
    """Regression (Codex review, third round): once an ``#elif 1``/``#elif
    true`` arm fires, it is unconditionally reachable in *every* build
    configuration, which makes every later sibling arm in the same chain
    unconditionally *unreachable* — masking must resume for them, unlike
    after a merely-unevaluated ``#elif <macro>`` condition. A construct
    written only in the dead ``#else`` here must not be detected."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif 1\nint consteval;\n#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_construct_in_dead_arm_after_elif_true_word(tmp_path):
    """Companion: same as above but with the ``true`` spelling."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif true\nint consteval;\n#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_construct_in_elif_true_arm_itself(tmp_path):
    """Companion: the ``#elif 1`` arm's own content is definitely reachable
    and must still be scanned normally."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif 1\nconsteval int f();\n#else\nint consteval;\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_still_detects_construct_after_unevaluable_elif_then_else(
    tmp_path,
):
    """Companion: a merely-*unevaluable* ``#elif <macro>`` (as opposed to a
    provably-true ``#elif 1``) must NOT settle the chain — a construct in
    the ``#else`` that follows it is still possibly reachable (the macro
    might be false) and must stay detected."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif SOME_MACRO\nint b;\n#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_nested_if_zero_constinit_type_shadow(tmp_path):
    """Companion: a nested ``#ifdef`` inside a disabled ``#if 0`` region
    must not confuse the matching-``#endif`` depth tracking and re-activate
    the stub early."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\n#ifdef FOO\nstruct constinit {};\n#endif\n#endif\n"
        "constinit int x = 1;\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "constinit-declaration" for r in reqs)


def test_cpp20_detector_recurses_into_nested_dead_stub_inside_live_if_true(tmp_path):
    """Regression (Codex review, eleventh round): a nested ``#if 0``
    compatibility stub inside a *live* ``#if 1`` arm was never evaluated
    on its own terms -- the old flat "opaque nested" depth counter just
    copied every nested line verbatim whenever the outer arm was
    unmasked. A dead nested ``struct concept {};`` then shadowed a
    genuine, live ``concept`` declaration two lines later and made
    ``_detect_cpp20_headers()`` wrongly return ``False``, so the header
    would be parsed without ``-std=gnu++20`` and fail on the real
    concept."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 1\n#if 0\nstruct concept {};\n#endif\n"
        "template<class T> concept C = true;\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)


def test_cpp20_detector_recurses_into_nested_dead_stub_inside_live_else(tmp_path):
    """Companion: the same nested-dead-stub-inside-a-live-arm bug, but
    with the live arm being an ``#else`` rather than an ``#if 1``."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#else\n#if 0\nstruct consteval {};\n#endif\n"
        "consteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_masks_live_looking_construct_inside_dead_ancestor(tmp_path):
    """Symmetric companion: a nested ``#if 1`` (unconditionally live on
    its own terms) sitting inside a dead ``#if 0`` ancestor must stay
    masked -- an ancestor's unreachability always wins over a nested
    arm's own condition."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\n#if 1\nconsteval int dead_nested();\n#endif\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_recurses_through_multiple_nesting_levels(tmp_path):
    """Companion: the recursive masking must hold past more than one
    level of live nesting, not just a single nested #if."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 1\n#if 1\n#if 0\nstruct concept {};\n#endif\n"
        "template<class T> concept C = true;\n#endif\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)


def test_cpp20_detector_detects_consteval_split_across_lines(tmp_path):
    """Regression (Codex review, second round): a bare ``consteval``
    trailing at the end of a line, with its declarator on the following
    physical line (``consteval\\nint f();``), was never joined into the
    same lookahead the way a trailing ``requires``/``concept`` already is
    — the per-line scan only ever saw the two halves separately, neither
    of which alone satisfies the "followed by an identifier" check."""
    headers = _write(tmp_path, "a.h", "consteval\nint f() { return 1; }\n")
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_detects_constinit_split_across_lines(tmp_path):
    """Companion: ``constinit`` split across lines."""
    headers = _write(tmp_path, "a.h", "constinit\nextern int x;\n")
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "constinit-declaration" for r in reqs)


def test_cpp20_detector_detects_abbreviated_unconstrained_function_template(tmp_path):
    """Regression (Codex review): a bare (unconstrained) ``auto`` used
    directly as an ordinary function's parameter type (``void f(auto
    x);`` — the C++20 abbreviated function template form) has no
    ``concept``/``requires``/constrained-parameter syntax at all, so none
    of the existing checks caught it."""
    headers = _write(tmp_path, "a.h", "void f(auto x);\n")
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "abbreviated-function-template-parameter" for r in reqs)


def test_cpp20_detector_carries_concept_shadow_across_header_set(tmp_path):
    """Regression (Codex review, sixth round): *header_paths* is the whole
    aggregate header set castxml/clang parse together as one translation
    unit, so a type literally named "concept" declared in one file
    (``compat.hpp``) shadows a bare "concept" use in a *different* file
    of the same set (``api.hpp``) just as much as it would within a
    single file — a per-file-only shadow check misses this split-file
    case entirely and wrongly forces C++20 mode on the whole aggregate."""
    compat = tmp_path / "compat.hpp"
    compat.write_bytes(b"struct concept {};\n")
    api = tmp_path / "api.hpp"
    api.write_bytes(b"template<class T> concept C = 1;\n")
    assert _detect_cpp20_headers([compat, api]) is False
    # Order in the header set must not matter.
    assert _detect_cpp20_headers([api, compat]) is False


def test_cpp20_detector_still_detects_genuine_concept_across_header_set(tmp_path):
    """Companion: a genuine concept declaration in one file of a
    multi-file header set, with no shadowing type anywhere in the set,
    must still be detected."""
    other = tmp_path / "other.hpp"
    other.write_bytes(b"struct Widget {};\n")
    api = tmp_path / "api.hpp"
    api.write_bytes(b"template<class T> concept D = true;\n")
    assert _detect_cpp20_headers([other, api]) is True
    reqs = _find_cpp20_requirements([other, api])
    assert any(r.reason == "concept-declaration" for r in reqs)


def test_cpp20_detector_ignores_construct_guarded_by_cplusplus_version_check(
    tmp_path,
):
    """Regression (Codex review, sixth round): content behind a
    ``#if __cplusplus >= 202002L`` guard must not itself drive the
    -std= decision this heuristic makes — the guard's own condition is
    what makes that content reachable, and that condition is decided by
    the very -std= this heuristic would otherwise be choosing. Forcing
    C++20 mode here would also turn the *unguarded*, active
    ``int consteval;`` elsewhere in the header into a reserved-word
    parse error."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if __cplusplus >= 202002L\n"
        "template<class T> concept C = true;\n"
        "#endif\n"
        "int consteval;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_construct_guarded_by_feature_test_macro(tmp_path):
    """Companion: the same reasoning applies to a standard feature-test
    macro guard (``__cpp_concepts``), not just a raw ``__cplusplus``
    comparison."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if __cpp_concepts >= 201907L\n"
        "template<class T> concept C = true;\n"
        "#endif\n"
        "int consteval;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_scans_else_fallback_of_cplusplus_guard_normally(tmp_path):
    """Companion: the ``#else`` fallback of a ``__cplusplus`` guard is the
    code path actually compiled whenever this heuristic doesn't force
    C++20, so it must be scanned like ordinary reachable code — this test
    only pins that the guarded arm itself doesn't leak a false positive
    (the fallback here has no C++20 syntax of its own)."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if __cplusplus >= 202002L\n"
        "template<class T> concept C = true;\n"
        "#else\n"
        "int legacy;\n"
        "#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_still_detects_unguarded_construct_alongside_cplusplus_guard(
    tmp_path,
):
    """Companion: a genuine, unguarded C++20 construct elsewhere in the
    same header must still force detection even when an unrelated
    ``__cplusplus``-guarded block is also present."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if __cplusplus >= 202002L\nint legacy;\n#endif\n"
        "template<class T> concept D = true;\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)


def test_cpp20_detector_ignores_construct_behind_ifdef_feature_test_macro(tmp_path):
    """Regression (Codex review, seventh round): the common shorthand
    ``#ifdef __cpp_consteval`` is exactly as circular as an explicit
    ``#if defined(__cpp_consteval)`` comparison — the macro is only
    defined once C++20 (or that specific feature) is already enabled, so
    content behind it must not itself force that same enablement. Forcing
    C++20 mode here would also turn the *unguarded*, active
    ``int consteval;`` into a reserved-word parse error."""
    headers = _write(
        tmp_path,
        "a.h",
        "#ifdef __cpp_consteval\nconsteval int f();\n#endif\nint consteval;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_construct_behind_ifndef_feature_test_macro(tmp_path):
    """Regression (Codex review, fourteenth round): ``#ifndef __cpp_x``
    is the *negated* mirror of ``#ifdef __cpp_x``, not "equally circular
    and masked the same way" as previously assumed here -- it needs the
    opposite polarity. The ``#ifndef``-guarded arm is the feature-*absent*
    content, exactly like the ``#else`` fallback of the positive form,
    and must be trusted as live; only an ``#else`` here (the
    feature-*present*, genuinely circular content) would be masked. A
    genuine C++20 construct written directly in the ``#ifndef``-guarded
    arm (unusual in practice, but not masked away) must still be
    detected -- see the companion test below for the realistic case
    where the circular content sits behind the ``#else`` instead, which
    correctly stays masked."""
    headers = _write(
        tmp_path,
        "a.h",
        "#ifndef __cpp_concepts\ntemplate<class T> concept C = true;\n#endif\nint x;\n",
    )
    assert _detect_cpp20_headers(headers) is True


def test_cpp20_detector_ignores_construct_behind_ifndef_feature_test_macro_else(
    tmp_path,
):
    """Companion: the realistic shape -- portable fallback content in the
    ``#ifndef``-guarded arm, with the actual C++20 construct sitting
    behind the ``#else`` (reached only once the feature is already
    available) -- must stay masked, since forcing ``-std=gnu++20`` purely
    because of that circular ``#else`` could break an unrelated,
    genuinely pre-C++20 use of the same word elsewhere in the header."""
    headers = _write(
        tmp_path,
        "a.h",
        "#ifndef __cpp_consteval\nint fallback;\n#else\nconsteval int f();\n#endif\n"
        "int consteval;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_construct_behind_ifndef_cplusplus(tmp_path):
    """Companion: ``#ifndef __cplusplus`` guards a pure-C fallback that
    castxml/clang never reach (they always parse in a C++-ish mode), so
    it must be masked exactly like ``#if 0``."""
    headers = _write(
        tmp_path,
        "a.h",
        "#ifndef __cplusplus\ntemplate<class T> concept C = true;\n#endif\nint x;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_still_detects_construct_behind_ifdef_cplusplus(tmp_path):
    """Companion: ``#ifdef __cplusplus`` — unlike a version/feature-test
    comparison — is unconditionally true for every ``-std=`` this
    heuristic could pick (castxml always defines it for these scans), so
    its content is a genuine, unconditional signal and must be scanned
    normally rather than masked away."""
    headers = _write(
        tmp_path, "a.h", "#ifdef __cplusplus\nconsteval int f();\n#endif\n"
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_still_detects_construct_behind_if_defined_cplusplus(
    tmp_path,
):
    """Regression (Codex review, eighth round): ``#if defined(__cplusplus)``
    is semantically identical to ``#ifdef __cplusplus`` — unconditionally
    true for every ``-std=`` this heuristic could pick — but the general
    __cplusplus-guard pattern doesn't distinguish it from a genuine
    version/feature-test comparison and previously masked it too, hiding
    the only C++20 signal in the header."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if defined(__cplusplus)\ntemplate<class T> concept C = true;\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)


def test_cpp20_detector_still_detects_construct_behind_bare_if_cplusplus(tmp_path):
    """Companion: the bare ``#if __cplusplus`` (no ``defined()`` wrapper,
    no comparison) truthiness-check form is equally always-true."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if __cplusplus\ntemplate<class T> concept C = true;\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)


def test_cpp20_detector_still_masks_combined_cplusplus_defined_and_version_check(
    tmp_path,
):
    """Companion: ``#if defined(__cplusplus) && __cplusplus >= 202002L`` is
    NOT the same as a bare ``defined(__cplusplus)`` — the attached version
    comparison makes it genuinely circular, so it must stay masked exactly
    like a plain version check would."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if defined(__cplusplus) && __cplusplus >= 202002L\n"
        "template<class T> concept C = true;\n"
        "#endif\n"
        "int consteval;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_masks_negated_defined_cplusplus(tmp_path):
    """Companion: ``#if !defined(__cplusplus)`` is the ``#if`` spelling of
    ``#ifndef __cplusplus`` and must be masked the same way — never
    reached in these C++-mode scans."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if !defined(__cplusplus)\ntemplate<class T> concept C = true;\n#endif\nint x;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_construct_behind_less_than_cplusplus_else(tmp_path):
    """Regression (Codex review, fifteenth round): ``#if __cplusplus < N``
    is the same "not yet at this dialect" polarity as ``#ifndef __cpp_x``,
    not the ``__cplusplus >= N`` polarity the general guard assumes. The
    guarded (less-than) arm is the pre-C++20-safe fallback and should be
    trusted as live; the ``#else`` (feature-present, circular) should be
    masked instead. Reusing the general treatment masked the safe
    fallback and trusted the circular ``#else``, so a header written "if
    older than C++20, do X; else do Y" forced ``-std=gnu++20`` purely
    because Y was there -- which could then break an unrelated, genuinely
    pre-C++20 use of the same word in the wrongly-masked-away X arm."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if __cplusplus < 202002L\nint consteval;\n#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_construct_behind_less_than_cplusplus_guard(tmp_path):
    """Companion: a genuine C++20 construct written directly in the
    less-than-guarded (pre-C++20-safe) arm itself must still be
    detected -- only its circular ``#else`` sibling is masked."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if __cplusplus < 202002L\nconsteval int f();\n#else\nint old_style;\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_construct_behind_less_equal_cplusplus_else(tmp_path):
    """Companion: the ``<=`` spelling (``#if __cplusplus <= 201703L``,
    e.g. "at most C++17") gets the same inverted treatment as ``<``."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if __cplusplus <= 201703L\nint consteval;\n#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_construct_behind_elif_less_than_cplusplus_else(
    tmp_path,
):
    """Regression (Codex review, sixteenth round): the same inverted
    ``__cplusplus < N`` polarity applies when the fallback is written as
    a later ``#elif`` arm (after an earlier disabled arm), not just the
    opening ``#if`` -- the general ``__cplusplus``-guard branch would
    otherwise mask this pre-C++20 fallback and trust the circular
    ``#else`` instead, forcing ``-std=gnu++20`` and breaking an unrelated
    pre-C++20 use of the same word in the wrongly-masked-away fallback."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint disabled;\n#elif __cplusplus < 202002L\nint consteval;\n"
        "#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_construct_behind_elif_less_than_cplusplus_guard(
    tmp_path,
):
    """Companion: a genuine C++20 construct written directly in the
    ``#elif``-less-than (trusted) arm itself must still be detected."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint disabled;\n#elif __cplusplus < 202002L\nconsteval int f();\n"
        "#else\nint old_style;\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_settles_on_elif_less_than_cplusplus(tmp_path):
    """Companion: once an ``#elif __cplusplus < N`` arm is reached, it
    settles the chain like ``#elif 1`` does -- a later sibling arm
    (even one that would otherwise be trusted, like ``#elif 1``) stays
    masked."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint disabled;\n#elif __cplusplus < 202002L\nint fallback;\n"
        "#elif 1\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_construct_behind_less_than_older_standard_else(
    tmp_path,
):
    """Regression (Codex review, seventeenth round): the inverted
    less-than polarity only holds when the threshold *is* the C++20
    boundary (``202002L``/``201703L``) — a less-than check against an
    *older* standard's threshold (``__cplusplus < 201103L``, "before
    C++11") isn't about the C++20 decision at all. There the guarded arm
    is the ancient-dialect fallback that's realistically never reached,
    and the ``#else`` is the one that's practically always active — the
    general (mask-guarded/trust-else) treatment is already correct for
    it, and blanket-inverting every less-than comparison regardless of
    threshold wrongly masked this always-active ``#else``, hiding a
    genuine, unconditional C++20 construct there."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if __cplusplus < 201103L\nint ancient_fallback;\n#else\n"
        "template<class T> concept C = true;\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)


def test_cpp20_detector_detects_construct_behind_elif_less_than_older_standard_else(
    tmp_path,
):
    """Companion: the same older-standard-threshold exclusion applies to
    the ``#elif`` spelling."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint disabled;\n#elif __cplusplus < 201402L\nint old;\n#else\n"
        "consteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_settles_on_elif_defined_cplusplus(tmp_path):
    """Companion: an ``#elif defined(__cplusplus)`` arm, once reached, is
    just as definitely-true as ``#elif 1`` — it must settle the chain
    (marking any later sibling arm dead) the same way, not merely stay
    unmasked without settling."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif defined(__cplusplus)\n"
        "template<class T> concept C = true;\n"
        "#else\nconsteval int dead();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)
    assert not any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_parenthesized_if_zero(tmp_path):
    """Regression (Codex review, ninth round): ``#if (0)`` is the same
    permanently-false guard as bare ``#if 0`` — valid C/C++ preprocessor
    syntax, just with redundant grouping parens around the whole
    condition. The parenthesized spelling wasn't recognized, so its
    (inactive) content leaked into the requirements scan."""
    headers = _write(
        tmp_path, "a.h", "#if (0)\nconsteval int f();\n#endif\nint consteval;\n"
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_ignores_parenthesized_elif_zero_with_spaces(tmp_path):
    """Companion: ``#elif ( 0 )`` (with internal spacing) must stay
    masked exactly like bare ``#elif 0``."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif ( 0 )\nconsteval int f();\n#endif\nint consteval;\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_settles_on_parenthesized_elif_true(tmp_path):
    """Companion: ``#elif (1)`` must settle the chain the same way bare
    ``#elif 1`` does, marking a later sibling arm dead."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif (1)\nint consteval;\n#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_still_detects_construct_behind_if_defined_space_cplusplus(
    tmp_path,
):
    """Regression (Codex review, ninth round): ``#if defined __cplusplus``
    (the no-parens spelling of the ``defined`` operator — equally valid
    C/C++ preprocessor syntax) is semantically identical to
    ``#if defined(__cplusplus)``/``#ifdef __cplusplus`` and must stay
    unmasked the same way."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if defined __cplusplus\ntemplate<class T> concept C = true;\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)


def test_cpp20_detector_settles_on_elif_defined_space_cplusplus(tmp_path):
    """Companion: the no-parens ``#elif defined __cplusplus`` form must
    also settle the chain like ``#elif defined(__cplusplus)`` does."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n#elif defined __cplusplus\n"
        "template<class T> concept C = true;\n"
        "#else\nconsteval int dead();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "concept-declaration" for r in reqs)
    assert not any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_dead_else_of_if_true(tmp_path):
    """Regression (Codex review, tenth round): ``#if 1``/``#if true`` was
    never recognized as opening a trackable chain at all, so a dead
    ``#else``/``#elif`` sibling fell through to plain pass-through
    scanning right along with the live arm. A genuine C++20 construct
    sitting only in that dead sibling must not force ``-std=gnu++20``
    onto a header whose live code never needed it -- especially when the
    live arm itself uses the same word as an ordinary identifier, which
    would then hard-fail to parse under the wrongly-forced dialect."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 1\nint consteval_var;\n#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_still_detects_construct_in_live_if_true_arm(tmp_path):
    """Companion: a genuine C++20 construct in the *live* ``#if 1`` arm
    itself must still be detected -- only dead sibling arms are masked."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 1\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_negated_defined_cpp_feature_guard_else(tmp_path):
    """Regression (Codex review, eighteenth round): ``#if
    !defined(__cpp_x)`` is the ``defined``-operator spelling of
    ``#ifndef __cpp_x`` and needs the same inverted polarity -- the
    guarded arm (feature absent) is the pre-C++20-safe fallback and must
    be trusted as live, while the ``#else`` (feature present) is circular
    and must be masked. Without a dedicated check, this spelling still
    matched the general ``__cplusplus``/``__cpp_*`` guard pattern (it
    contains ``__cpp_x`` too) and got the *positive*-form polarity
    instead -- masking the safe fallback and trusting the circular
    ``#else`` -- forcing ``-std=gnu++20`` purely because the ``#else``
    content was there, which could then break the live arm's own use of
    the same word as an ordinary identifier under the wrongly-forced
    dialect."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if !defined(__cpp_consteval)\n"
        "int consteval;\n"
        "#else\n"
        "consteval int f();\n"
        "#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_detects_construct_behind_negated_defined_cpp_feature_guard(
    tmp_path,
):
    """Companion: a genuine C++20 construct in the *live*
    ``#if !defined(__cpp_x)`` arm itself (no ``#else`` at all) must still
    be detected."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if !defined(__cpp_consteval)\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is True
    reqs = _find_cpp20_requirements(headers)
    assert any(r.reason == "consteval-declaration" for r in reqs)


def test_cpp20_detector_ignores_negated_defined_space_cpp_feature_guard_else(
    tmp_path,
):
    """Companion: the no-parens ``#if !defined __cpp_x`` spelling (the
    ``defined`` operator doesn't require parens) needs the same inverted
    polarity as the parenthesized form."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if !defined __cpp_consteval\n"
        "int consteval;\n"
        "#else\n"
        "consteval int f();\n"
        "#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False


def test_cpp20_detector_settles_on_elif_negated_defined_cpp_feature_guard(tmp_path):
    """Companion: the ``#elif !defined(__cpp_x)`` spelling, reached as a
    later arm in a chain that opens on something else, must settle the
    chain the same way -- masking the ``#else`` -- rather than falling
    into the general elif-guard branch's positive-form polarity."""
    headers = _write(
        tmp_path,
        "a.h",
        "#if 0\nint a;\n"
        "#elif !defined(__cpp_consteval)\n"
        "int consteval;\n"
        "#else\nconsteval int f();\n#endif\n",
    )
    assert _detect_cpp20_headers(headers) is False

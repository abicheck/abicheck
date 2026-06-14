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

"""Compiler-free lexical ABI-risk pattern pre-scan (ADR-035 D2, phase 1 / G19.1).

This is the **always-on, compiler-free** half of the ADR-035 PR pre-scan tier:
a stdlib-regex (no new dependency, no compile DB, no compiler) scan over
changed + public source/header files for the ABI-risk constructs called out in
ADR-035 D2 — ``#pragma pack``, ``alignas``, ``__attribute__((packed|
visibility))``, ``__declspec(dllexport|dllimport)``, ``extern "C"``,
calling-convention macros, explicit / ``extern`` template instantiation,
``inline namespace``, public ``virtual`` methods, and ``operator new``/
``delete``.

The scan emits **advisory facts** and **escalation triggers** only — it never
produces a verdict and is never authoritative for a ``BREAKING`` finding (the
ADR-028 D3 / ADR-035 D1 authority rule). Its facts pre-populate the L2/L5
surface and its escalation triggers feed the D7 points-of-interest list that
targets the expensive S5 source-ABI replay.

Everything here is a pure function over text: no binaries are parsed and no
external tools are run, so the whole module is exercised by fast unit tests.
``Tree-sitter`` is a deliberately-deferred optional backend; the stdlib scanner
is the portable baseline (ADR-035 D2).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .model import CoverageStatus, LayerConfidence, LayerCoverage

#: Pattern-scan fact-schema version. Independent of every other buildsource
#: schema version (see ``buildsource/CLAUDE.md`` "Versioning"); bumped on any
#: breaking change to the emitted ``PatternFact``/``PatternScanResult`` layout.
PATTERN_SCAN_VERSION: int = 1

#: File suffixes the lexical scanner treats as C/C++ source or headers. Headers
#: without a suffix (the libstdc++ ``<vector>`` style) are not on disk under a
#: project tree, so an extension allowlist is sufficient and keeps the walk cheap.
SOURCE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".h",
        ".hh",
        ".hpp",
        ".hxx",
        ".h++",
        ".inl",
        ".inc",
        ".ipp",
        ".tpp",
        ".tcc",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".c++",
    }
)


class PatternCategory(str, Enum):
    """The ABI dimension a construct touches — drives the escalation hint."""

    LAYOUT = "layout"  # record size/alignment/packing
    VTABLE = "vtable"  # virtual table shape / dispatch
    TEMPLATE = "template"  # instantiated template surface
    NAMESPACE = "namespace"  # mangling (inline namespace)
    VISIBILITY = "visibility"  # symbol visibility annotations
    LINKAGE = "linkage"  # extern "C" / dllexport linkage
    CALLING_CONVENTION = "calling_convention"  # __cdecl/__stdcall/...
    ALLOCATION = "allocation"  # operator new/delete


class PatternKind(str, Enum):
    """One named ABI-risk construct the lexical scan recognizes (ADR-035 D2)."""

    PRAGMA_PACK = "pragma_pack"
    ALIGNAS = "alignas"
    ATTRIBUTE_PACKED = "attribute_packed"
    ATTRIBUTE_VISIBILITY = "attribute_visibility"
    DECLSPEC_DLLEXPORT = "declspec_dllexport"
    DECLSPEC_DLLIMPORT = "declspec_dllimport"
    EXTERN_C = "extern_c"
    CALLING_CONVENTION = "calling_convention"
    EXPLICIT_TEMPLATE_INSTANTIATION = "explicit_template_instantiation"
    EXTERN_TEMPLATE = "extern_template"
    INLINE_NAMESPACE = "inline_namespace"
    VIRTUAL_METHOD = "virtual_method"
    OPERATOR_NEW_DELETE = "operator_new_delete"


@dataclass(frozen=True)
class _Rule:
    """A single-kind lexical rule: a compiled regex plus its classification."""

    kind: PatternKind
    category: PatternCategory
    regex: re.Pattern[str]
    escalates: bool  # finding it warrants a deeper (S5) scan
    detail: str
    # `extern "C"` is itself a string literal syntactically; this rule must see
    # the literal, so it scans the comment-blanked-but-string-preserved text.
    scan_strings: bool = False


#: Hex-digit set used to tell a C++14 digit separator (`1'000`) from a
#: char-literal opener in the comment/string blanker.
_HEXDIGITS = frozenset("0123456789abcdefABCDEF")

_RAW_STRING_PREFIXES = ("u8R", "uR", "UR", "LR", "R")

#: Matches the body *inside* a ``__attribute__((...))`` list up to (but not
#: across) its closing ``))``: a run of non-paren chars or nested paren groups
#: **up to two levels deep** (e.g. ``(8)`` in ``aligned(8)`` or
#: ``(sizeof(int))`` in ``aligned(sizeof(int))``). Lazy, so it stops at the
#: searched keyword. The alternatives are first-char-disjoint (``[^()]`` vs
#: ``\(``) at every level, so there is no catastrophic backtracking. Because it
#: can never consume an unbalanced ``)`` it cannot leak past the attribute into
#: following code — so ``__attribute__((aligned(8))) int packed;`` does *not*
#: match the packed rule.
_ATTR_INNER = r"(?:[^()]|\((?:[^()]|\([^()]*\))*\))*?"

#: Layout-, vtable-, template-, and mangling-affecting constructs warrant
#: escalation to the expensive semantic scan (S5); pure annotations
#: (visibility/linkage/calling-convention/allocation) are advisory only.
_RULES: tuple[_Rule, ...] = (
    _Rule(
        PatternKind.PRAGMA_PACK,
        PatternCategory.LAYOUT,
        re.compile(r"#\s*pragma\s+pack\b"),
        True,
        "explicit struct packing changes record layout",
    ),
    _Rule(
        PatternKind.PRAGMA_PACK,
        PatternCategory.LAYOUT,
        # Macro-friendly pragma spelling, e.g. `_Pragma("pack(push, 1)")`.
        # Runs on string-preserved text (the payload is a string literal) and
        # requires `pack` to be the payload's *directive token* (first token
        # after the quote), so non-pack pragmas that merely mention the word —
        # `_Pragma("GCC diagnostic ignored \"-Wpragma-pack\"")` — don't match.
        re.compile(r'_Pragma\s*\(\s*"\s*pack\b'),
        True,
        "explicit struct packing changes record layout",
        scan_strings=True,
    ),
    _Rule(
        PatternKind.ALIGNAS,
        PatternCategory.LAYOUT,
        re.compile(r"\b(?:alignas|_Alignas)\s*\("),
        True,
        "explicit alignment changes record layout",
    ),
    _Rule(
        PatternKind.ATTRIBUTE_PACKED,
        PatternCategory.LAYOUT,
        # Match `packed`/`__packed__` anywhere in the attribute list — including
        # after nested args, e.g. `__attribute__((aligned(8), packed))` — but
        # only *within* the attribute parentheses (`_ATTR_INNER`), so a later
        # identifier named `packed` is not mistaken for the attribute. Also
        # covers the C++11 `[[gnu::packed]]` spelling (`[^]]*` stays inside `[[]]`).
        re.compile(
            r"__attribute__\s*\(\s*\(" + _ATTR_INNER + r"\b(?:__)?packed(?:__)?\b"
            r"|\[\[[^]]*\bpacked\b"
        ),
        True,
        "packed attribute changes record layout",
    ),
    _Rule(
        PatternKind.ATTRIBUTE_VISIBILITY,
        PatternCategory.VISIBILITY,
        # Match `visibility` anywhere in the attribute list (including after a
        # nested arg, e.g. `__attribute__((aligned(8), visibility("hidden")))`)
        # but only within the attribute parentheses (`_ATTR_INNER`), so a later
        # identifier named `visibility` is not mistaken for the attribute.
        re.compile(
            r"__attribute__\s*\(\s*\(" + _ATTR_INNER + r"\bvisibility\b"
            # C++ branch: allow other attributes before `gnu::visibility`,
            # e.g. `[[nodiscard, gnu::visibility("default")]]`; `[^]]*` stays
            # within the `[[...]]` brackets.
            r"|\[\[[^]]*gnu::visibility"
        ),
        False,
        "explicit symbol visibility annotation",
    ),
    _Rule(
        PatternKind.DECLSPEC_DLLEXPORT,
        PatternCategory.LINKAGE,
        re.compile(r"__declspec\s*\(\s*dllexport\s*\)"),
        False,
        "PE/COFF export annotation",
    ),
    _Rule(
        PatternKind.DECLSPEC_DLLIMPORT,
        PatternCategory.LINKAGE,
        re.compile(r"__declspec\s*\(\s*dllimport\s*\)"),
        False,
        "PE/COFF import annotation",
    ),
    _Rule(
        PatternKind.EXTERN_C,
        PatternCategory.LINKAGE,
        # `extern "C"` but not `extern "C++"` (no closing quote right after C).
        re.compile(r'\bextern\s*"C"'),
        False,
        "C linkage block suppresses C++ name mangling",
        scan_strings=True,
    ),
    _Rule(
        PatternKind.CALLING_CONVENTION,
        PatternCategory.CALLING_CONVENTION,
        re.compile(
            # MSVC-style keywords and Windows API macros.
            r"\b(?:__cdecl|__stdcall|__fastcall|__thiscall|__vectorcall"
            r"|_cdecl|_stdcall|_fastcall|WINAPI|APIENTRY|CALLBACK"
            r"|STDMETHODCALLTYPE)\b"
            # GNU/Clang attribute spellings (the documented ELF way to change
            # calling convention), e.g. `__attribute__((ms_abi))`,
            # `((sysv_abi))`, `((stdcall))`, `((regparm(3)))`.
            r"|__attribute__\s*\(\s*\("
            + _ATTR_INNER
            + r"\b(?:__)?(?:ms_abi|sysv_abi|stdcall|cdecl|fastcall|thiscall"
            r"|regparm|pcs|aarch64_vector_pcs|preserve_all|preserve_most"
            r"|vectorcall)(?:__)?\b"
        ),
        False,
        "explicit calling convention affects the symbol/ABI",
    ),
    _Rule(
        PatternKind.INLINE_NAMESPACE,
        PatternCategory.NAMESPACE,
        re.compile(r"\binline\s+namespace\b"),
        True,
        "inline namespace participates in name mangling / ABI tagging",
    ),
    _Rule(
        PatternKind.VIRTUAL_METHOD,
        PatternCategory.VTABLE,
        re.compile(r"\bvirtual\b"),
        True,
        "virtual member affects vtable layout and dispatch",
    ),
    _Rule(
        PatternKind.VIRTUAL_METHOD,
        PatternCategory.VTABLE,
        # C++11 override-only declarations are virtual even without the
        # `virtual` keyword. The delimiter guard avoids double-counting the
        # common `virtual void f() override;` spelling and handles one-line class
        # bodies (`struct D { void f() override; };`). The suffix accepts common
        # cv/ref/noexcept/virt-specifier orderings such as `final override`,
        # `& noexcept override`, and `&& final`.
        re.compile(
            r"(?m)(?:^|[;{}])\s*(?![^\n;{}]*\bvirtual\b)"
            r"[^\n;{}()]*\([^;\n{}]*\)\s*"
            r"(?:const\s*)?(?:volatile\s*)?(?:&{1,2}\s*)?"
            r"(?:noexcept(?:\s*\([^)]*\))?\s*)?"
            r"(?:->\s*[^;\n{}]*?\s*)?"
            r"(?:\b(?:override|final)\b\s*){1,2}(?=[=;{])"
        ),
        True,
        "virtual member affects vtable layout and dispatch",
    ),
    _Rule(
        PatternKind.OPERATOR_NEW_DELETE,
        PatternCategory.ALLOCATION,
        re.compile(r"\boperator\s+(?:new|delete)\b"),
        False,
        "custom allocation operator participates in the ABI",
    ),
)

#: Matches an explicit instantiation (``template class Foo<int>;``,
#: ``template void api<int>();``) or a forward ``extern template`` declaration
#: in one pass. The distinguisher from a template *definition* is that the
#: ``template`` keyword is followed by a declaration token, not ``<`` (which
#: starts the parameter list of ``template <...>`` / ``template<...>``). The
#: ``(?!\s*<)`` lookahead is anchored right after ``template`` so it rejects the
#: definition even with arbitrary whitespace/newlines before ``<`` (it is
#: zero-width and cannot be defeated by ``\s+`` backtracking). Dependent-name
#: disambiguators (``x.template f<>()``, ``p->template ...``, ``A::template
#: rebind<>``) are rejected separately in ``scan_text`` by walking back over
#: whitespace to the preceding token — which fixed-width regex lookbehind cannot
#: do when there is whitespace after ``::`` / ``.`` / ``->``. The optional
#: ``extern`` group selects the kind so the two never double-count the same span.
#: Covers both class and function template instantiations (ADR-035 D2).
_TEMPLATE_RE = re.compile(r"(?P<extern>\bextern\s+)?\btemplate\b(?!\s*<)\s+")

#: A ``template`` keyword preceded (ignoring whitespace) by one of these ends a
#: ``.`` / ``->`` / ``::`` access — i.e. a dependent-name disambiguator, not an
#: explicit instantiation.
_TEMPLATE_DISAMBIGUATOR_PREV = frozenset(".:>")


@dataclass(frozen=True)
class PatternFact:
    """One advisory ABI-risk construct located by the lexical scan.

    Carries enough to render a finding (``path``/``line``/``snippet``) and to
    seed the D7 POI list (``kind``/``category``/``escalates``). Never a verdict.
    """

    kind: PatternKind
    category: PatternCategory
    path: str
    line: int
    snippet: str
    escalates: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "category": self.category.value,
            "path": self.path,
            "line": self.line,
            "snippet": self.snippet,
            "escalates": self.escalates,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class EscalationTrigger:
    """A grouped recommendation to run a deeper source-analysis method.

    Aggregates every escalating fact of one ``kind`` into a single advisory so
    a header with twenty ``virtual`` methods produces one trigger, not twenty.
    ``recommended_method`` is the ADR-035 S-axis selector (e.g. ``"s5"``).
    """

    kind: PatternKind
    category: PatternCategory
    recommended_method: str
    count: int
    sample_location: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "category": self.category.value,
            "recommended_method": self.recommended_method,
            "count": self.count,
            "sample_location": self.sample_location,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PatternScanResult:
    """Outcome of a lexical pre-scan over a set of files (ADR-035 D2).

    ``facts`` are the raw advisory hits; ``escalation_triggers`` is the deduped
    per-kind recommendation set that feeds D7 focusing; ``coverage`` is the
    mandatory ADR-033 coverage row stating the scan ran and over how much.
    """

    facts: list[PatternFact] = field(default_factory=list)
    files_scanned: int = 0
    files_skipped: int = 0
    version: int = PATTERN_SCAN_VERSION

    @property
    def escalation_triggers(self) -> list[EscalationTrigger]:
        """Group escalating facts by kind into deterministic, deduped advisories."""
        by_kind: dict[PatternKind, list[PatternFact]] = {}
        for fact in self.facts:
            if fact.escalates:
                by_kind.setdefault(fact.kind, []).append(fact)
        triggers: list[EscalationTrigger] = []
        for kind, hits in by_kind.items():
            first = hits[0]
            triggers.append(
                EscalationTrigger(
                    kind=kind,
                    category=first.category,
                    recommended_method="s5",
                    count=len(hits),
                    sample_location=f"{first.path}:{first.line}"
                    if first.path
                    else str(first.line),
                    reason=first.detail,
                )
            )
        # Stable ordering for reproducible reports/CI diffs.
        triggers.sort(key=lambda t: t.kind.value)
        return triggers

    @property
    def should_escalate(self) -> bool:
        """True if any located construct warrants a deeper source-ABI scan."""
        return any(fact.escalates for fact in self.facts)

    def counts_by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for fact in self.facts:
            counts[fact.kind.value] = counts.get(fact.kind.value, 0) + 1
        return counts

    def coverage(self) -> LayerCoverage:
        """The mandatory ADR-033 D6 coverage row for this always-on tier."""
        status = CoverageStatus.PRESENT
        if self.files_scanned == 0:
            status = CoverageStatus.NOT_COLLECTED
        elif self.files_skipped:
            status = CoverageStatus.PARTIAL
        detail = (
            f"lexical pattern scan (S3), {self.files_scanned} file(s), "
            f"{len(self.facts)} fact(s)"
        )
        if self.files_skipped:
            detail += f", {self.files_skipped} unreadable skipped"
        return LayerCoverage(
            layer="pattern_scan",
            status=status,
            # Lexical, no semantics: facts are advisory, never directly observed ABI.
            confidence=LayerConfidence.REDUCED,
            detail=detail,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "files_scanned": self.files_scanned,
            "files_skipped": self.files_skipped,
            "facts": [f.to_dict() for f in self.facts],
            "escalation_triggers": [t.to_dict() for t in self.escalation_triggers],
            "counts_by_kind": self.counts_by_kind(),
        }


def _is_digit_separator(text: str, i: int) -> bool:
    """True if the ``'`` at ``text[i]`` is a C++14 digit separator, not a literal.

    A digit separator sits between two hex digits *inside a numeric literal*
    (``1'000``, ``0xFF'FF``). A numeric literal always starts with a decimal
    digit, so the maximal preceding identifier-run must begin with one — this
    rejects a prefixed char literal whose prefix happens to end in a hex digit
    (``u8'a'``, where the run is ``u8`` and starts with ``u``).
    """
    prev = text[i - 1] if i > 0 else ""
    nxt = text[i + 1] if i + 1 < len(text) else ""
    if prev not in _HEXDIGITS or nxt not in _HEXDIGITS:
        return False
    k = i - 1
    while k >= 0 and (text[k].isalnum() or text[k] in "_'"):
        k -= 1
    token_start = text[k + 1] if k + 1 < i else ""
    return token_start.isdigit()


def _raw_string_end(text: str, quote_index: int) -> int:
    """Return the closing quote offset for a C++ raw string, or ``-1``."""
    prefix_start = -1
    prefix = ""
    for candidate in _RAW_STRING_PREFIXES:
        start = quote_index - len(candidate)
        if start >= 0 and text[start:quote_index] == candidate:
            prefix_start = start
            prefix = candidate
            break
    if prefix_start < 0:
        return -1
    if prefix_start > 0 and (
        text[prefix_start - 1].isalnum() or text[prefix_start - 1] == "_"
    ):
        return -1
    if prefix != "R" and not prefix.endswith("R"):
        return -1

    open_paren = text.find("(", quote_index + 1, quote_index + 18)
    if open_paren < 0:
        return -1
    delimiter = text[quote_index + 1 : open_paren]
    if any(ch.isspace() or ch in "()\\" for ch in delimiter):
        return -1
    close = ")" + delimiter + '"'
    close_start = text.find(close, open_paren + 1)
    if close_start < 0:
        return -1
    return close_start + len(close) - 1


def _blank_comments_and_strings(text: str, blank_strings: bool = True) -> str:
    """Replace comment (and optionally string/char-literal) *contents* with spaces.

    Preserves every newline and the overall length so byte offsets (and thus
    line numbers) are unchanged — the scan can then match only real code and
    never trips on an ABI keyword mentioned inside a comment or string literal.
    A single forward state machine handles ``//`` / ``/* */`` comments and
    ``"..."`` / ``'...'`` literals with backslash escapes, plus C++ raw string
    literals so embedded quotes do not desynchronize the scanner.

    With ``blank_strings=False`` only comments are blanked and string/char
    literals are preserved verbatim — needed for the ``extern "C"`` rule, whose
    target *is* a string literal.
    """
    out: list[str] = []
    i, n = 0, len(text)
    state = "code"  # code | line_comment | block_comment | string | char
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                out.append("  ")
                i += 2
                state = "line_comment"
            elif ch == "/" and nxt == "*":
                out.append("  ")
                i += 2
                state = "block_comment"
            elif ch == '"':
                raw_end = _raw_string_end(text, i)
                if raw_end >= 0:
                    raw = text[i : raw_end + 1]
                    # Raw-string bodies are never code, even on the
                    # string-preserving path used by `_Pragma`/`extern "C"`.
                    out.append("".join("\n" if c == "\n" else " " for c in raw))
                    i = raw_end + 1
                else:
                    out.append('"')
                    i += 1
                    state = "string"
            elif ch == "'":
                # Distinguish a C++14 digit separator (`1'000`, `0xFF'FF`) from a
                # char-literal opener — misreading a literal as a separator (or
                # vice-versa) would blank the rest of the file.
                out.append("'")
                if not _is_digit_separator(text, i):
                    state = "char"
                i += 1
            else:
                out.append(ch)
                i += 1
        elif state == "line_comment":
            if ch == "\n":
                # A backslash immediately before the newline (optionally across
                # a CRLF `\r`) splices the next physical line into the `//`
                # comment via C/C++ line continuation, so stay in the comment.
                prev = text[i - 1] if i > 0 else ""
                prev2 = text[i - 2] if i > 1 else ""
                spliced = prev == "\\" or (prev == "\r" and prev2 == "\\")
                out.append("\n")
                if not spliced:
                    state = "code"
            else:
                out.append(" ")
            i += 1
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                out.append("  ")
                i += 2
                state = "code"
            else:
                out.append("\n" if ch == "\n" else " ")
                i += 1
        elif state in ("string", "char"):
            quote = '"' if state == "string" else "'"
            if ch == "\\":
                # Keep the escape + escaped char verbatim when preserving strings,
                # else blank both (newlines always survive for line accounting).
                if not blank_strings:
                    out.append(ch + (nxt if nxt else ""))
                else:
                    out.append("  " if nxt != "\n" else " \n")
                i += 2
            elif ch == quote:
                out.append(quote)
                i += 1
                state = "code"
            else:
                if not blank_strings:
                    out.append(ch)
                else:
                    out.append("\n" if ch == "\n" else " ")
                i += 1
    return "".join(out)


def _line_of(text: str, offset: int) -> int:
    """1-based line number of ``offset`` within ``text``."""
    return text.count("\n", 0, offset) + 1


def _snippet(raw_lines: list[str], line: int) -> str:
    """The original (un-blanked) source line for a 1-based ``line`` number."""
    if 1 <= line <= len(raw_lines):
        return raw_lines[line - 1].strip()
    return ""


def scan_text(text: str, path: str = "") -> list[PatternFact]:
    """Lexically scan one source/header's text for ABI-risk constructs.

    Pure and side-effect-free: comments and string literals are blanked first
    (so offsets/line numbers are preserved), then every rule plus the template
    classifier runs over the blanked text. Facts are returned in source order.
    """
    blanked = _blank_comments_and_strings(text)
    # Comments blanked, string/char literals preserved — for rules whose target
    # is itself a string literal (`extern "C"`).
    code_with_strings = _blank_comments_and_strings(text, blank_strings=False)
    raw_lines = text.splitlines()
    facts: list[PatternFact] = []

    for rule in _RULES:
        haystack = code_with_strings if rule.scan_strings else blanked
        for m in rule.regex.finditer(haystack):
            line = _line_of(blanked, m.start())
            facts.append(
                PatternFact(
                    kind=rule.kind,
                    category=rule.category,
                    path=path,
                    line=line,
                    snippet=_snippet(raw_lines, line),
                    escalates=rule.escalates,
                    detail=rule.detail,
                )
            )

    # Template instantiation/declaration: one regex, kind chosen by the optional
    # `extern` group so `extern template class X<int>;` is classified once.
    for m in _TEMPLATE_RE.finditer(blanked):
        is_extern = bool(m.group("extern"))
        # Reject dependent-name disambiguators (`x.template f<>()`,
        # `A::template rebind<>`): walk back over whitespace to the token before
        # `template`; if it ends a `.`/`->`/`::` access it is not an
        # instantiation. `extern template` is never a disambiguator.
        if not is_extern:
            kw_start = m.start()
            j = kw_start - 1
            while j >= 0 and blanked[j].isspace():
                j -= 1
            if j >= 0 and blanked[j] in _TEMPLATE_DISAMBIGUATOR_PREV:
                continue
        line = _line_of(blanked, m.start())
        facts.append(
            PatternFact(
                kind=PatternKind.EXTERN_TEMPLATE
                if is_extern
                else PatternKind.EXPLICIT_TEMPLATE_INSTANTIATION,
                category=PatternCategory.TEMPLATE,
                path=path,
                line=line,
                snippet=_snippet(raw_lines, line),
                escalates=True,
                detail="extern template declaration suppresses local instantiation"
                if is_extern
                else "explicit template instantiation fixes a concrete ABI surface",
            )
        )

    facts.sort(key=lambda f: (f.line, f.kind.value))
    return facts


def _is_scannable(path: Path) -> bool:
    """True if a directory-walked file should be lexically scanned.

    Known C/C++ suffixes plus **extensionless** files: many C++ libraries ship
    extensionless public headers (``include/mylib/Core``), and the D2 scope is
    "changed + public headers", not "files with a C/C++ extension". Files with a
    different, explicit extension (``.md``, ``.txt``, ``.bin``) are skipped.
    """
    suffix = path.suffix.lower()
    return suffix in SOURCE_SUFFIXES or suffix == ""


def iter_source_files(
    roots: Iterable[str | Path],
    changed_paths: Iterable[str] | None = None,
) -> list[Path]:
    """Collect C/C++ source/header files under ``roots`` (files or directories).

    A ``root`` that is a **file** is honored regardless of suffix — the caller
    pointed at it directly. A ``root`` that is a **directory** is walked and
    filtered by :func:`_is_scannable` (known suffixes + extensionless headers).
    When ``changed_paths`` is given, the result is intersected with it (by
    suffix-matching the path tail), implementing the ADR-035 D2 "changed +
    public" scope: callers pass public roots and the PR's changed paths. The
    walk is deterministic (sorted) for reproducible reports.
    """
    changed_suffixes: set[str] | None = None
    if changed_paths is not None:
        changed_suffixes = {str(p).replace("\\", "/") for p in changed_paths}

    collected: set[Path] = set()
    for root in roots:
        rp = Path(root)
        if rp.is_file():
            candidates = [(rp, True)]  # explicit file: honor regardless of suffix
        elif rp.is_dir():
            candidates = [(p, False) for p in rp.rglob("*") if p.is_file()]
        else:
            continue
        for cand, explicit in candidates:
            if not explicit and not _is_scannable(cand):
                continue
            if changed_suffixes is not None and not _path_changed(
                cand, changed_suffixes
            ):
                continue
            collected.add(cand)
    return sorted(collected)


def _path_changed(candidate: Path, changed: set[str]) -> bool:
    """True if ``candidate`` tail-matches any of the changed-path strings.

    The changed list usually holds repo-relative paths (``include/foo.h``)
    while ``candidate`` may be absolute or rooted elsewhere, so a suffix match
    in either direction is the robust join; a bare filename in the changed list
    matches by basename.
    """
    norm = str(candidate).replace("\\", "/")
    for ch in changed:
        c = ch.replace("\\", "/")
        if norm == c or norm.endswith("/" + c) or c.endswith("/" + norm):
            return True
        if "/" not in c and candidate.name == c:
            return True
    return False


def scan_files(
    roots: Iterable[str | Path],
    changed_paths: Iterable[str] | None = None,
) -> PatternScanResult:
    """Run the lexical pre-scan over the in-scope files and aggregate facts.

    Unreadable files are counted as skipped (reported via ``coverage()``), never
    fatal — the pre-scan is best-effort advisory by design (ADR-035 D2/D3).
    """
    files = iter_source_files(roots, changed_paths)
    facts: list[PatternFact] = []
    scanned = 0
    skipped = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        facts.extend(scan_text(text, path=str(f)))
        scanned += 1
    return PatternScanResult(facts=facts, files_scanned=scanned, files_skipped=skipped)

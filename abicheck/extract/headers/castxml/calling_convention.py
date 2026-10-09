"""Calling-convention attributes CastXML leaves out of its ``attributes``.

CastXML 0.7 reports only a few x86-32 conventions in a function's compound
``attributes`` string; a GNU x86-64 selection such as
``__attribute__((ms_abi))`` or ``__attribute__((sysv_abi))`` is dropped
entirely. Without it a header-only comparison of a function that switched to
``ms_abi`` read ``NO_CHANGE`` -- the GCC catalog lane of
``case64_calling_convention_changed`` -- because GCC does not emit
``DW_AT_calling_convention`` either, while the same artifacts compared through
the clang AST backend reported the break.

This module recovers the convention from the declaration CastXML itself
located (``file``/``line``), reading only that declaration's own text: the
specifiers before the function's name and the tail after its parameter list,
never the parameter list itself (a callback parameter's ``ms_abi`` belongs to
the parameter's type, not to the function). A convention spelled through a
macro is expanded with the compiler-resolved macro table the castxml run
recorded (:mod:`.macro_table`) before the text is read, so ``CALL int f()``
reads as whatever ``CALL`` really expanded to in that translation unit.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree.ElementTree import Element

from ....model.cc_attributes import CC_ATTRIBUTE_BASES
from .context import CastxmlParserContext

#: How far above the reported line a declaration's specifiers may start.
_LOOKBACK_LINES = 8
#: How far below it the parameter list and trailing attributes may run.
_LOOKAHEAD_LINES = 12

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
#: Comments and string/character literals, in one pass so a quote inside a
#: comment (or ``//`` inside a string) is read the way the compiler reads it.
_NON_CODE_RE = re.compile(
    r"/\*.*?\*/|//[^\n]*|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", re.DOTALL
)


def _blank_non_code(text: str) -> str:
    """*text* with comments and string/char literals replaced by spaces of the
    same length (newlines kept), so offsets and line numbers still line up
    and nothing inside them can be mistaken for the declaration."""
    return _NON_CODE_RE.sub(
        lambda m: "".join("\n" if c == "\n" else " " for c in m.group(0)), text
    )


_GNU_ATTRIBUTE_RE = re.compile(r"__attribute__\s*\(")
_STD_ATTRIBUTE_RE = re.compile(r"\[\[(?P<body>.*?)\]\]", re.DOTALL)
#: MSVC-style keywords (also accepted by GCC/Clang on x86).
_KEYWORD_RE = re.compile(r"\b__(cdecl|stdcall|fastcall|thiscall|vectorcall)\b")


def _attribute_bases(body: str) -> set[str]:
    found: set[str] = set()
    for raw in body.split(","):
        token = raw.strip()
        for prefix in ("gnu::", "gnu:"):
            if token.startswith(prefix):
                token = token[len(prefix) :]
        base = token.split("(", 1)[0].strip().strip("_")
        if base in CC_ATTRIBUTE_BASES:
            found.add(token.strip("_") if "(" not in token else token)
    return found


def calling_conventions_in(text: str) -> set[str]:
    """Calling-convention tokens spelled in *text* (comments ignored)."""
    text = _LINE_COMMENT_RE.sub(" ", _BLOCK_COMMENT_RE.sub(" ", text))
    found: set[str] = set()
    for m in _GNU_ATTRIBUTE_RE.finditer(text):
        close = _matching_paren(text, m.end() - 1)
        if close != -1:
            # ``__attribute__((a, b(1)))``: the outer pair wraps one more pair.
            inner = text[m.end() : close].strip()
            if inner.startswith("(") and inner.endswith(")"):
                inner = inner[1:-1]
            found |= _attribute_bases(inner)
    for m in _STD_ATTRIBUTE_RE.finditer(text):
        found |= _attribute_bases(m.group("body"))
    found |= {m.group(1) for m in _KEYWORD_RE.finditer(text)}
    return found


def _matching_paren(text: str, open_at: int) -> int:
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _declaration_outside_params(window: str, name: str, name_at: int) -> str:
    """The declaration's text around its parameter list, without it."""
    prefix = window[:name_at]
    boundary = max(prefix.rfind(";"), prefix.rfind("{"), prefix.rfind("}"))
    head = prefix[boundary + 1 :]
    rest = window[name_at + len(name) :]
    open_at = rest.find("(")
    if open_at == -1:
        return head
    close_at = _matching_paren(rest, open_at)
    if close_at == -1:
        return head
    tail = rest[close_at + 1 :]
    stop = min((i for i in (tail.find(";"), tail.find("{")) if i != -1), default=-1)
    return head + " " + (tail[:stop] if stop != -1 else tail)


def _expand_cc_macros_on_lines(window: str, macros: dict[str, str]) -> str:
    """*window* with each calling-convention macro expanded in place, line
    by line so the reported line keeps its index."""
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, macros)) + r")\b")
    return "\n".join(
        pattern.sub(lambda m: macros[m.group(1)], line) for line in window.split("\n")
    )


def source_calling_conventions(
    ctx: CastxmlParserContext, el: Element, name: str
) -> set[str]:
    """Calling conventions the declaration *el* spells in its own text.

    Empty when the location is unknown, the file is unreadable or the name
    cannot be found at the reported line.
    """
    file_el = ctx.id_map.get(el.get("file", ""))
    line_raw = el.get("line", "")
    if file_el is None or not line_raw.isdigit() or not name:
        return set()
    fname = file_el.get("name", "")
    if not fname:
        return set()
    lines = ctx.source_lines_cache.get(fname)
    if lines is None:
        try:
            lines = Path(fname).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return set()
        ctx.source_lines_cache[fname] = lines
    line_no = int(line_raw)
    if not 1 <= line_no <= len(lines):
        return set()
    start = max(0, line_no - 1 - _LOOKBACK_LINES)
    before = "\n".join(lines[start : line_no - 1])
    here_and_after = "\n".join(lines[line_no - 1 : line_no - 1 + _LOOKAHEAD_LINES])
    window = _blank_non_code(before + "\n" + here_and_after)
    table = ctx.cc_macro_table
    if table is not None and table.macros:
        window = _expand_cc_macros_on_lines(window, table.macros)
    # Located by line index, not by ``len(before)``: expansion changes lengths.
    target = before.count("\n") + 1
    line_start = sum(len(ln) + 1 for ln in window.split("\n")[:target])
    line_end = window.find("\n", line_start)
    line_end = len(window) if line_end == -1 else line_end
    # Every spelling of ``name(`` on the reported line, not the first: the
    # line may also carry an expression or another declaration naming it,
    # and a first-match search would let that shadow the real declaration
    # (and its attribute). A tail stops at the next ``;``/``{``, so another
    # declaration's attributes are never read as this one's.
    found: set[str] = set()
    pattern = re.compile(rf"(?<![\w:]){re.escape(name)}\s*\(")
    for match in pattern.finditer(window, line_start, line_end):
        found |= calling_conventions_in(
            _declaration_outside_params(window, name, match.start())
        )
    if table is not None and table.default_cc:
        # Spelling the target's default convention changes nothing.
        found.discard(table.default_cc)
    return found

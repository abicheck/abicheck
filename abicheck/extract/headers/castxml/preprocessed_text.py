"""The preprocessed text of declarations that spell a calling convention.

CastXML drops GNU x86-64 conventions (``ms_abi``/``sysv_abi``) from its
``attributes`` string, so :mod:`.calling_convention` recovers them from the
declaration's own text. Real APIs rarely spell the attribute literally; they
write ``CALL int f(void)`` or ``int CALL(ms_abi) f(void)`` with ``CALL``
defined in some configuration header, often under ``#ifdef``.

What a macro meant *for that declaration* is the preprocessor state at the
declaration, not at the end of the translation unit: an ``#undef CALL`` after
the declaration, or a later ``#define CALL`` with another convention, leaves
the declaration's convention unchanged (2026-10-11 re-audit; a final ``-dM``
macro table read both wrong and skipped function-like macros entirely). So
the declaration text is taken from the compiler's own preprocessed output:
the same castxml command is re-run with ``-E -dD`` (preprocess only, keep
``#define``/``#undef`` lines in place), whose line markers map every output
line back to its source ``file:line``. Each macro -- object-like or
function-like -- is then already expanded exactly as the real parse expanded
it at that point.

Only files whose preprocessed code spells a convention matter, and in them
only the lines around a declaration CastXML located; those lines are stored
in the CastXML document itself (one ``<AbicheckPreprocessedLine>`` each) so a
cached AST carries them too. A file without stored lines spells no
convention anywhere. When the preprocess run fails, the document carries no
table and :mod:`.calling_convention` keeps its literal-text behaviour.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement

log = logging.getLogger(__name__)

#: Marks a document whose preprocessed text was recorded (possibly with no
#: lines), so "no convention anywhere" is distinguishable from "no table".
PREPROCESSED_TAG = "AbicheckPreprocessed"
#: Tag of the element carrying one preprocessed source line.
LINE_TAG = "AbicheckPreprocessedLine"

#: How far above the reported line a declaration's specifiers may start.
LOOKBACK_LINES = 8
#: How far below it the parameter list and trailing attributes may run.
LOOKAHEAD_LINES = 12

_LINEMARKER_RE = re.compile(
    r'^#\s*(?:line\s+)?(?P<line>\d+)\s+"(?P<file>(?:\\.|[^"\\])*)"'
)
_DEFINE_RE = re.compile(r"^#define\s+(?P<name>[A-Za-z_]\w*)")
_XML_UNSAFE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def preprocess_command(castxml_cmd: list[str], out_path: Path) -> list[str] | None:
    """*castxml_cmd* turned into a ``-E -dD`` run writing *out_path*.

    ``None`` when the command does not have the shape
    ``dumper_ast_config._build_castxml_command`` produces.
    """
    if "--castxml-output=1" not in castxml_cmd or "-o" not in castxml_cmd:
        return None
    cmd = [tok for tok in castxml_cmd if tok != "--castxml-output=1"]
    o_at = len(cmd) - 1 - cmd[::-1].index("-o")
    if o_at + 1 >= len(cmd):
        return None
    cmd[o_at + 1] = str(out_path)
    # Before the trailing input file, so the tool reads the flags first.
    return [*cmd[:-1], "-E", "-dD", cmd[-1]]


def file_key(name: str) -> str:
    """The key a source file is stored under (CastXML ``File/@name`` and a
    line marker's spelling of the same path compare equal)."""
    return os.path.normpath(name)


def parse_preprocessed(text: str) -> tuple[dict[str, dict[int, str]], set[str]]:
    """``({file: {line: text}}, defined macro names)`` of ``-E -dD`` output.

    Line markers (``# 12 "a.h" 2``) set the file and line of the next output
    line; every other line -- code, or a ``#define``/``#undef``/``#pragma``
    that stands where its directive did -- is the next source line. Pseudo
    files (``<built-in>``, ``<command line>``) carry no declarations.
    """
    files: dict[str, dict[int, str]] = {}
    defined: set[str] = set()
    current: dict[int, str] | None = None
    line_no = 0
    for raw in text.splitlines():
        marker = _LINEMARKER_RE.match(raw)
        if marker:
            name = re.sub(r"\\(.)", r"\1", marker.group("file"))
            line_no = int(marker.group("line"))
            current = (
                None if name.startswith("<") else files.setdefault(file_key(name), {})
            )
            continue
        define = _DEFINE_RE.match(raw)
        if define:
            defined.add(define.group("name"))
        if current is not None and raw.strip():
            current[line_no] = raw
        line_no += 1
    return files, defined


def target_default_convention(defined: set[str] | Mapping[str, str]) -> str:
    """The convention a plain declaration already has on the target the
    predefined macros describe: on x86-64 an explicit ``sysv_abi`` (SysV
    targets) or ``ms_abi`` (Windows) is the default and changes nothing,
    which is how the clang backend reads it too. Empty elsewhere."""
    if "__x86_64__" not in defined:
        return ""
    return "ms_abi" if "_WIN32" in defined else "sysv_abi"


class PreprocessedText:
    """Preprocessed declaration lines plus the target's default convention.

    *lines* maps a :func:`file_key` to ``{line number: preprocessed text}``;
    only files spelling a convention appear.
    """

    def __init__(
        self, lines: Mapping[str, Mapping[int, str]], default_cc: str = ""
    ) -> None:
        self.lines = {f: dict(by_line) for f, by_line in lines.items()}
        self.default_cc = default_cc

    def source_lines(self, file_name: str) -> list[str] | None:
        """*file_name*'s preprocessed lines (unrecorded lines empty), or
        ``None`` when the file spells no convention."""
        by_line = self.lines.get(file_key(file_name))
        if not by_line:
            return None
        return [by_line.get(i, "") for i in range(1, max(by_line) + 1)]


def _is_code(line: str) -> bool:
    return not line.lstrip().startswith("#")


def declaration_windows(
    root: Element,
    files: Mapping[str, Mapping[int, str]],
    has_cc: Callable[[str], bool],
) -> dict[str, dict[int, str]]:
    """The lines of *files* :mod:`.calling_convention` may read for a
    declaration located in *root*, kept only for files whose preprocessed
    code spells a convention."""
    cc_files = {
        key
        for key, by_line in files.items()
        if has_cc("\n".join(t for t in by_line.values() if _is_code(t)))
    }
    if not cc_files:
        return {}
    names = {
        el.get("id", ""): file_key(el.get("name", ""))
        for el in root.iter("File")
        if el.get("name")
    }
    out: dict[str, dict[int, str]] = {}
    for el in root:
        key = names.get(el.get("file", ""))
        line_raw = el.get("line", "")
        if key not in cc_files or not line_raw.isdigit():
            continue
        assert key is not None
        line = int(line_raw)
        src, dst = files[key], out.setdefault(key, {})
        for i in range(line - LOOKBACK_LINES, line + LOOKAHEAD_LINES):
            if i in src:
                dst[i] = src[i]
    return out


def attach_preprocessed_text(root: Element, table: PreprocessedText) -> None:
    """Record *table* in the CastXML *root* (see the module docstring)."""
    SubElement(root, PREPROCESSED_TAG, default_cc=table.default_cc)
    for key in sorted(table.lines):
        for line, text in sorted(table.lines[key].items()):
            SubElement(
                root,
                LINE_TAG,
                file=key,
                line=str(line),
                text=_XML_UNSAFE_RE.sub(" ", text),
            )


def read_preprocessed_text(root: Element) -> PreprocessedText | None:
    """The table :func:`attach_preprocessed_text` stored in *root*, or
    ``None`` when the document has none."""
    marker = root.find(PREPROCESSED_TAG)
    if marker is None:
        return None
    lines: dict[str, dict[int, str]] = {}
    for el in root.iter(LINE_TAG):
        line_raw = el.get("line", "")
        if el.get("file") and line_raw.isdigit():
            lines.setdefault(el.get("file", ""), {})[int(line_raw)] = el.get("text", "")
    return PreprocessedText(lines, marker.get("default_cc", ""))


def resolve_preprocessed_text(
    castxml_cmd: list[str],
    root: Element,
    work_dir: Path,
    run: Callable[..., subprocess.CompletedProcess[str]],
    has_cc: Callable[[str], bool],
) -> PreprocessedText | None:
    """Run the ``-E -dD`` sibling of *castxml_cmd* and keep the declaration
    windows of *root* that matter; ``None`` on any failure."""
    out_path = work_dir / "preprocessed.txt"
    cmd = preprocess_command(castxml_cmd, out_path)
    if cmd is None:
        return None
    try:
        result = run(cmd, timeout=120)
        if result.returncode != 0 or not out_path.exists():
            return None
        text = out_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("castxml preprocess run failed: %s", exc)
        return None
    files, defined = parse_preprocessed(text)
    return PreprocessedText(
        declaration_windows(root, files, has_cc), target_default_convention(defined)
    )

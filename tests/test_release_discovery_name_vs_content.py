"""A release directory's members are decided by content, with the file
name only a hint.

Bug class: ``classify.BinaryExtensionClassifier`` accepted every name
containing ``.so.``, so a source or backup file next to a library
(``libreal.so.c``, ``libfoo.so.bak``) became a release member. It then
failed to load, the whole directory comparison read ``ERROR``/exit 4, and
because it shared the real library's match key it displaced
``libreal.so`` from the comparison entirely.

The oracle below is written from the rule, not from the classifier's
regex: a file is a member when its content is a binary, or when its name
is exactly ``<stem>.so`` or ``<stem>.so.<digits>[.<digits>...]`` (kept for
text linker-script stubs such as ``libc.so``).
"""

from __future__ import annotations

import itertools
import shutil
import sys
from pathlib import Path

from abicheck.classify import is_supported_compare_input
from abicheck.workflows.release_inputs import collect_release_inputs

_NAMES = [
    "libreal.so",
    "libreal.so.1",
    "libreal.so.1.2.3",
    "libreal.so.0d",
    "libreal.so.c",
    "libreal.so.bak",
    "libreal.so.debug",
    "libreal.so.1.debug",
    "libreal.so.txt",
    "libreal.dll",
    "libreal.dylib",
    "notes.txt",
]

_C_SOURCE = b"int add(int a, int b) { return a + b; }\n"
_LINKER_SCRIPT = b"/* GNU ld script */\nGROUP ( /lib/libreal.so.1 )\n"


def _binary_bytes() -> bytes:
    # The running interpreter is a real ELF/PE/Mach-O binary on every
    # platform the suite runs on, so no compiler is needed.
    return Path(sys.executable).resolve().read_bytes()[: 1 << 16]


_CONTENTS = {
    "binary": _binary_bytes,
    "c_source": lambda: _C_SOURCE,
    "linker_script": lambda: _LINKER_SCRIPT,
    "empty": lambda: b"",
}


def _name_alone_qualifies(name: str) -> bool:
    lower = name.lower()
    if lower.endswith((".dll", ".dylib", ".pyd")):
        return True
    head, sep, tail = lower.rpartition(".so")
    if not sep or not head:
        return False
    if tail == "":
        return True
    parts = tail.split(".")
    return parts[0] == "" and all(p.isdigit() for p in parts[1:])


def _is_debug_companion(path: Path) -> bool:
    # Separate debug info: a ``*.debug`` file or anything inside a macOS
    # ``*.dSYM`` bundle. Never a library, even though it is a real binary
    # carrying the library's own name.
    if path.name.lower().endswith(".debug"):
        return True
    return any(part.lower().endswith(".dsym") for part in path.parent.parts)


def _expected(name: str, content: str, parent: Path = Path("lib")) -> bool:
    if _is_debug_companion(parent / name):
        return False
    return content == "binary" or _name_alone_qualifies(name)


def test_membership_matches_the_rule_over_every_name_and_content(
    tmp_path: Path,
) -> None:
    payloads = {k: make() for k, make in _CONTENTS.items()}
    wrong = []
    for name, content in itertools.product(_NAMES, payloads):
        d = tmp_path / content
        d.mkdir(exist_ok=True)
        f = d / name
        f.write_bytes(payloads[content])
        got = is_supported_compare_input(f)
        if got != _expected(name, content):
            wrong.append((name, content, got))
    assert wrong == []


def test_oracle_is_not_constant() -> None:
    verdicts = {_expected(n, c) for n, c in itertools.product(_NAMES, _CONTENTS)}
    assert verdicts == {True, False}
    # The cases the defect got wrong are on the "not a member" side.
    assert not _expected("libreal.so.c", "c_source")
    assert not _expected("libreal.so.bak", "empty")
    assert _expected("libreal.so.0d", "binary")
    assert not _expected("libreal.so.debug", "binary")
    assert not _expected("libreal.so", "binary", Path("x.so.dSYM/Contents"))


def test_a_source_file_beside_a_library_is_not_a_release_member(tmp_path: Path) -> None:
    lib = tmp_path / "libreal.so"
    shutil.copyfile(Path(sys.executable).resolve(), lib)
    for stray in ("libreal.so.c", "libreal.so.bak", "libreal.so.txt"):
        (tmp_path / stray).write_bytes(_C_SOURCE)
    assert collect_release_inputs(tmp_path) == [lib]


def test_debug_companions_are_never_members(tmp_path: Path) -> None:
    """The macOS ``gcc -g`` layout: a dylib, its dSYM DWARF copy (a real
    Mach-O with the same name) and dsymutil's relocation map. Only the
    library itself is a member, so the directory has one ``libx.so``, not an
    ambiguous pair."""
    binary = _binary_bytes()
    lib = tmp_path / "libx.so"
    lib.write_bytes(binary)
    contents = tmp_path / "libx.so.dSYM" / "Contents"
    for rel, data in (
        ("Resources/DWARF/libx.so", binary),
        ("Resources/Relocations/aarch64/libx.so.yml", b"---\ntriple: arm64\n"),
        ("Info.plist", b"<plist/>"),
    ):
        f = contents / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    (tmp_path / "libx.so.debug").write_bytes(binary)
    assert collect_release_inputs(tmp_path) == [lib]

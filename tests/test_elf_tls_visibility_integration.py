"""A TLS variable that becomes protected reads symbol_elf_visibility_changed.

``symbol_elf_visibility_changed`` covers the exported-visibility transitions
no specific detector owns: a function's DEFAULT->PROTECTED change is
``func_visibility_protected_changed`` and a data object's
``protected_visibility_changed``, but a thread-local (``STT_TLS``) symbol has
neither. Built with the real compiler and read from the real ``.dynsym``, so
the transition is the one a linker writes, not a hand-built fixture.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump
from abicheck.model.change_catalog.kinds import ChangeKind

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux") or shutil.which("gcc") is None,
        reason="needs gcc producing ELF (Linux)",
    ),
]

_VISIBILITY_KINDS = frozenset(
    {
        ChangeKind.SYMBOL_ELF_VISIBILITY_CHANGED,
        ChangeKind.PROTECTED_VISIBILITY_CHANGED,
        ChangeKind.FUNC_VISIBILITY_PROTECTED_CHANGED,
    }
)
_SOURCE = "{attr} __thread int tcount = 1;\nint get_tcount(void) {{ return tcount; }}\n"


def _build(tmp: Path, name: str, attr: str) -> Path:
    src = tmp / f"{name}.c"
    src.write_text(_SOURCE.format(attr=attr), encoding="utf-8")
    out = tmp / f"lib{name}.so"
    cmd = ["gcc", "-shared", "-fPIC", "-o", str(out), str(src)]
    run = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # A configured compiler that ran and failed is a failure, never a skip.
    assert run.returncode == 0, f"{' '.join(cmd)}\n{run.stderr}"
    return out


def test_tls_default_to_protected_is_the_generic_visibility_kind(
    tmp_path: Path,
) -> None:
    old = dump(_build(tmp_path, "old", ""), [], version="1")
    new = dump(
        _build(tmp_path, "new", '__attribute__((visibility("protected")))'),
        [],
        version="2",
    )
    kinds = {c.kind for c in compare(old, new).changes}
    # Exactly one visibility finding, and it is the generic one: the specific
    # kinds own functions and data objects only.
    assert kinds & _VISIBILITY_KINDS == {ChangeKind.SYMBOL_ELF_VISIBILITY_CHANGED}

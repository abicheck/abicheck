# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Demangling answers the same whatever the host's locale encoding is.

``c++filt`` reads and writes bytes; symbol names are UTF-8 (GCC and Clang
mangle a non-ASCII identifier as its raw UTF-8 bytes). Text-mode subprocess
I/O without an explicit encoding uses the *locale* encoding instead -- cp1252
on Windows before Python 3.15 -- which cannot encode such a name, so one
non-ASCII symbol made the whole ``demangle_batch`` call raise
``UnicodeEncodeError``. On ``windows-latest``/3.14 that turned every
Hypothesis example of ``test_live_stored_operand_property.py`` with a
non-ASCII ``_Z`` name into a failure whose shrinking (one ``c++filt`` spawn
per step) outlasted the 600s per-test timeout, so the xdist worker was killed.

The oracle is a differential across host encodings, not abicheck's own
helper: the same names are demangled in a child interpreter whose locale
encoding is ASCII (C locale, UTF-8 mode and locale coercion off; plain
``PYTHONUTF8=0`` on Windows, i.e. cp1252) and in one forced into UTF-8 mode,
and the two answers must be identical and complete. The in-process
``cxxfilt`` package is hidden in both, so the ``c++filt`` subprocess -- the
path whose encoding is in question -- is what answers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("c++filt") is None, reason="needs the c++filt binary"
)

# Independently chosen name shapes: plain ASCII (must demangle), Latin-1
# range (encodable in cp1252, not ASCII), BMP and astral code points (in no
# single-byte code page), a lone surrogate (what an undecodable ELF byte
# becomes under surrogateescape), and a valid name sharing a batch with them.
_NAMES = [
    "_Z3foov",
    "_ZN2ns3barEi",
    "_Z1é",
    "_Zété3",
    "_Zꘚ260",
    "_Z\U00014509440",
    "_Z3f\udcffv",
    "_ZN1a1bEš",
]

_CHILD = r"""
import json, sys
sys.modules["cxxfilt"] = None  # force the c++filt subprocess path
import abicheck.demangle as d
names = json.loads(sys.stdin.read())
batch = d.demangle_batch(names)
d._reset_demangle_batch_cache()
d.demangle.cache_clear()
single = {n: d.demangle(n) for n in names}
sys.stdout.write(json.dumps({"batch": batch, "single": single}, ensure_ascii=True))
"""


def _child_env(*, utf8: bool) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env["PYTHONUTF8"] = "1" if utf8 else "0"
    if os.name != "nt" and not utf8:
        # The C locale's codeset is ASCII; without these two, CPython would
        # coerce it to UTF-8 (PEP 538) or enter UTF-8 mode (PEP 540).
        env["LC_ALL"] = "C"
        env["PYTHONCOERCECLOCALE"] = "0"
    return env


def _demangle_in_child(*, utf8: bool) -> dict[str, dict[str, str | None]]:
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD],
        input=json.dumps(_NAMES, ensure_ascii=True).encode("ascii"),
        capture_output=True,
        env=_child_env(utf8=utf8),
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return json.loads(proc.stdout.decode("ascii"))


def test_locale_encoding_of_the_child_is_really_narrow() -> None:
    """Non-vacuity: the narrow child cannot encode the names under test."""
    probe = "import locale, sys; enc = locale.getencoding(); sys.stdout.write(enc)"
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        env=_child_env(utf8=False),
        timeout=60,
    )
    encoding = proc.stdout.decode("ascii")
    with pytest.raises(UnicodeEncodeError):
        "\n".join(_NAMES).encode(encoding)


def test_demangling_does_not_depend_on_the_host_locale_encoding() -> None:
    narrow = _demangle_in_child(utf8=False)
    wide = _demangle_in_child(utf8=True)
    assert narrow == wide
    # And the answer is real, not two equally empty ones: the ASCII names
    # demangle in both the batch and the single-symbol path.
    for result in (narrow["batch"], narrow["single"]):
        assert result["_Z3foov"] == "foo()"
        assert result["_ZN2ns3barEi"] == "ns::bar(int)"

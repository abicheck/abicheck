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

"""Parse a Windows binary's headers for the C++ ABI the binary was built with.

A Windows clang defaults to ``*-windows-msvc``; a MinGW clang to
``*-windows-gnu``. The two mangle C++ differently (``?f@@YAHH@Z`` vs
``_Z1fi``) and lay some records out differently, so a header parsed for the
other ABI yields declarations that match none of the binary's exports. castxml
does not have the problem: it parses through the GNU driver it was handed. The
clang frontend took clang's own default instead, so on a Windows runner every
MinGW-built DLL lost its whole declared surface under ``--ast-frontend clang``.

:func:`binary_abi_target_token` reads the ABI off the binary's own exports and
returns the ``--target`` that matches it, or ``None`` to leave the parse
untouched: no Windows default, an explicit target already given, no mangled
C++ export (a C-only DLL), exports of both ABIs, or a default that already
matches. Only the environment (``-gnu``/``-msvc``) changes; the architecture
stays the default's.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...._compiler_options import explicit_target_triple
from ....model.execution_cache import memoized
from ..toolchain import _configured_target_triple, _resolved_tool, _tool_target_triple

__all__ = ["binary_abi_target_token", "exported_cxx_abi"]

ITANIUM = "itanium"
MSVC = "msvc"


def exported_cxx_abi(exported: Iterable[str]) -> str | None:
    """The C++ ABI the exported names are mangled for, or ``None`` when they
    state none (no mangled C++ name) or both."""
    itanium = msvc = False
    for name in exported:
        # 32-bit MinGW prefixes a C symbol with "_", so "__Z" is Itanium too.
        if name.startswith(("_Z", "__Z")):
            itanium = True
        elif name.startswith("?"):
            msvc = True
        if itanium and msvc:
            return None
    return ITANIUM if itanium else MSVC if msvc else None


def _windows_abi(triple: str) -> str | None:
    if "-windows-msvc" in triple:
        return MSVC
    if "-windows-gnu" in triple or "-mingw" in triple:
        return ITANIUM
    return None


@memoized(maxsize=32)
def _default_triple(selected: str, digest: str) -> str | None:
    del digest  # part of the memo key: a replaced binary is re-probed
    return _configured_target_triple(None, (), selected)


def _probe_gnu_triple(probe_cc: str | None) -> str | None:
    if probe_cc is None:
        return None
    try:
        selected, _real, _stat, digest = _resolved_tool(probe_cc)
    except OSError:
        return None
    return _tool_target_triple(selected, digest)


def binary_abi_target_token(
    *,
    clang_bin: str,
    exported: Iterable[str],
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    probe_cc: str | None,
) -> str | None:
    """``--target=<triple>`` for the binary's C++ ABI, or ``None``.

    For an Itanium-ABI binary the triple is *probe_cc*'s own ``-dumpmachine``
    when that GNU driver targets the same architecture's MinGW (its system
    headers are then the right ones), else ``<arch>-w64-windows-gnu``.
    """
    if explicit_target_triple(gcc_options, gcc_option_tokens):
        return None
    abi = exported_cxx_abi(exported)
    if abi is None:
        return None
    try:
        selected, _real, _stat, digest = _resolved_tool(clang_bin)
    except OSError:
        return None
    default = _default_triple(selected, digest)
    if default is None or _windows_abi(default) in (None, abi):
        return None
    arch = default.split("-", 1)[0]
    if abi == MSVC:
        return f"--target={arch}-pc-windows-msvc"
    probed = _probe_gnu_triple(probe_cc)
    if probed and probed.split("-", 1)[0] == arch and _windows_abi(probed) == ITANIUM:
        return f"--target={probed}"
    return f"--target={arch}-w64-windows-gnu"

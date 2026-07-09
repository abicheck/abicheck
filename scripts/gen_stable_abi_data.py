#!/usr/bin/env python3
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

"""Regenerate ``abicheck/stable_abi_data.py`` from CPython's ``stable_abi.toml``.

The vendored Stable-ABI membership set (:mod:`abicheck.stable_abi_data`) is the
authoritative source for whether an imported CPython symbol is part of the
Limited API. It must be refreshed when a new CPython minor ships so extensions
targeting that interpreter (``--abi3 3.NN``) are not flagged for using symbols
that entered the Stable ABI in that release.

Usage::

    # from a local checkout of CPython's Misc/stable_abi.toml
    python scripts/gen_stable_abi_data.py path/to/stable_abi.toml

    # or straight from a CPython branch/tag (needs network)
    python scripts/gen_stable_abi_data.py \\
        --url https://raw.githubusercontent.com/python/cpython/3.15/Misc/stable_abi.toml \\
        --version 3.15

The extraction is deterministic: every ``[function.*]`` and ``[data.*]`` entry
(these are the linkable symbols — macros/consts/typedefs/structs are not) is
mapped to the ``(major, minor)`` release in its ``added`` field, INCLUDING the
``abi_only`` ``_Py*`` symbols the Limited-API headers route public macros to.
Membership — not a name prefix — is what decides stability downstream.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

import tomllib

_OUT = Path(__file__).resolve().parent.parent / "abicheck" / "stable_abi_data.py"

_LICENSE = """\
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
"""


def _parse_version(added: str) -> tuple[int, int]:
    """Parse an ``added`` field like ``"3.14"`` into ``(3, 14)``."""
    major_s, _, minor_s = added.partition(".")
    return (int(major_s), int(minor_s or "0"))


def extract(toml_bytes: bytes) -> dict[str, tuple[int, int]]:
    """Extract ``symbol -> (major, minor)`` for every linkable Stable-ABI entry."""
    data = tomllib.loads(toml_bytes.decode("utf-8"))
    symbols: dict[str, tuple[int, int]] = {}
    for section in ("function", "data"):
        for name, meta in data.get(section, {}).items():
            added = meta.get("added")
            if added is not None:
                symbols[name] = _parse_version(str(added))
    return symbols


def render(symbols: dict[str, tuple[int, int]], version: str) -> str:
    """Render the full ``stable_abi_data.py`` module text."""
    rows = "\n".join(
        f'    "{name}": ({v[0]}, {v[1]}),'
        for name, v in sorted(symbols.items())
    )
    return (
        _LICENSE
        + '\n"""Vendored CPython Stable-ABI (Limited API) symbol floors — '
        "GENERATED DATA.\n\n"
        "Maps every linkable Stable-ABI symbol (``[function.*]`` and "
        "``[data.*]`` entries)\nto the ``(major, minor)`` CPython release that "
        "added it to the Limited API. This\nis the authoritative membership "
        "set: it INCLUDES the ``abi_only`` ``_Py*``\nsymbols that the "
        "Limited-API headers route public macros to (e.g. ``Py_DECREF`` →\n"
        "``_Py_Dealloc``, ``PyObject_GC_New`` → ``_PyObject_GC_New``, the\n"
        "``_PyArg_*_SizeT`` parsers, the ``_Py_NoneStruct`` singleton), so a "
        "symbol's\npresence here — not a name prefix — decides whether it is "
        "stable.\n\n"
        f"Source: CPython ``Misc/stable_abi.toml`` @ branch {version} "
        f"({len(symbols)} symbols).\n"
        "This is GENERATED data — do not hand-edit individual rows; the table "
        "grows by\na few dozen symbols each CPython release. If the source branch "
        "above is a\nnot-yet-released (in-development) CPython, its newest "
        "``added`` versions may\nstill change before release — re-run the "
        "generator once it stabilises.\n"
        "Refresh: run ``scripts/gen_stable_abi_data.py`` over a newer "
        "``stable_abi.toml``\n(functions + data sections, ``added`` → floor).\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "#: Stable-ABI symbol -> (major, minor) release it entered the "
        "Limited API.\n"
        "STABLE_ABI_SYMBOLS: dict[str, tuple[int, int]] = {\n"
        f"{rows}\n"
        "}\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "toml", nargs="?", help="path to a local Misc/stable_abi.toml"
    )
    parser.add_argument(
        "--url", help="fetch stable_abi.toml from this URL instead of a local path"
    )
    parser.add_argument(
        "--version",
        required=True,
        help="CPython branch/tag the data is from (e.g. 3.15) — recorded in the header",
    )
    args = parser.parse_args(argv)

    if args.url:
        with urllib.request.urlopen(args.url) as resp:  # noqa: S310 (trusted URL)
            toml_bytes = resp.read()
    elif args.toml:
        toml_bytes = Path(args.toml).read_bytes()
    else:
        parser.error("provide a TOML path or --url")

    symbols = extract(toml_bytes)
    _OUT.write_text(render(symbols, args.version), encoding="utf-8")
    print(f"wrote {_OUT} ({len(symbols)} symbols) from CPython {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

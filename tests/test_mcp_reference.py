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

"""The generated MCP tools reference stays in sync with abicheck/mcp_server.py
-- mirrors scripts/gen_action_reference.py's own test_action_reference.py
pattern. Skipped when the optional `mcp` extra isn't installed, the same
guard the generator itself needs."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_DIR = Path(__file__).resolve().parent.parent


def _mcp_extra_installed() -> bool:
    try:
        importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


if not _mcp_extra_installed():
    pytest.skip("mcp extra not installed", allow_module_level=True)

_TAINTED_MODULE_NAMES = ("mcp", "mcp.server", "mcp.server.fastmcp", "abicheck.mcp_server")


@pytest.fixture(autouse=True)
def _real_abicheck_mcp_server():
    """tests/test_mcp_server_unit.py and tests/test_mcp_server_coverage.py
    install a MagicMock under sys.modules["mcp"] via setdefault and never
    restore it (unlike test_mcp_server_coverage_gaps.py/test_cov95_misc.py,
    which carefully save/restore sys.modules around the same trick). If
    either module already ran in this pytest process, `abicheck.mcp_server`
    is cached with a mocked FastMCP instance whose tool registry is empty --
    silently breaking this file's assumption that it exercises the real,
    live MCP server (reproduced with `pytest tests/test_mcp_server_unit.py
    tests/test_mcp_reference.py`).

    This must run at test-execution time, not import/collection time: pytest
    collects every file before running any test, so a module-level purge
    here would fire during our own collection -- before test_mcp_server_unit.py's
    tests actually run -- and rip the mock out from under them instead.
    """
    mocked = isinstance(sys.modules.get("mcp"), MagicMock)
    saved = {name: sys.modules.get(name) for name in _TAINTED_MODULE_NAMES}
    abicheck_pkg = sys.modules.get("abicheck")
    had_attr = abicheck_pkg is not None and hasattr(abicheck_pkg, "mcp_server")
    saved_attr = getattr(abicheck_pkg, "mcp_server", None)
    if mocked:
        for name in _TAINTED_MODULE_NAMES:
            sys.modules.pop(name, None)
        # Popping "abicheck.mcp_server" from sys.modules isn't enough on its
        # own: Python's `from abicheck import mcp_server` first tries
        # `getattr(abicheck, "mcp_server")`, and the earlier (mocked) import
        # already left that attribute set on the cached `abicheck` package
        # object -- so without this, `from abicheck import mcp_server` below
        # (including inside gen_mcp_reference.py's own render()) would keep
        # resolving to the stale mocked module instead of a fresh real one.
        if had_attr:
            delattr(abicheck_pkg, "mcp_server")
    try:
        yield
    finally:
        if mocked:
            for name, mod in saved.items():
                if mod is not None:
                    sys.modules[name] = mod
                else:
                    sys.modules.pop(name, None)
            if had_attr:
                abicheck_pkg.mcp_server = saved_attr


def _load_gen():
    path = REPO_DIR / "scripts" / "gen_mcp_reference.py"
    spec = importlib.util.spec_from_file_location("gen_mcp_reference", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generated_reference_is_in_sync_with_mcp_server():
    gen = _load_gen()
    expected = gen.render()
    actual = gen.OUT_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/reference/mcp-tools-reference.md is stale -- regenerate with "
        "`python scripts/gen_mcp_reference.py`"
    )


def test_every_registered_tool_appears_in_generated_reference():
    # _registered_tools reads the live FastMCP tool manager rather than a
    # hand-maintained name list, so a newly added/renamed @mcp.tool() can't
    # silently go missing from the "exhaustive" reference.
    from abicheck import mcp_server

    gen = _load_gen()
    content = gen.OUT_PATH.read_text(encoding="utf-8")
    names = [name for name, _fn in gen._registered_tools(mcp_server)]
    assert names, "no tools found on the live MCP server -- something is wrong with the test setup"
    for name in names:
        assert f"`{name}`" in content, f"{name!r} missing from generated MCP reference"


def test_default_values_appear_in_generated_reference():
    gen = _load_gen()
    content = gen.OUT_PATH.read_text(encoding="utf-8")
    # abi_dump's version parameter has a real default ("unknown") that must
    # show up in the Default column, not just an implicit "no" in Required.
    assert "| `version` | `str` | no | `unknown` |" in content


def test_parse_args_section_folds_wrapped_continuation_lines():
    gen = _load_gen()
    doc = """Summary line.

    Args:
        foo: Short description.
        bar: A description that
            wraps onto a second line.
    """
    parsed = gen._parse_args_section(doc)
    assert parsed == {
        "foo": "Short description.",
        "bar": "A description that wraps onto a second line.",
    }


def test_generated_reference_has_marker_comment():
    gen = _load_gen()
    text = gen.OUT_PATH.read_text(encoding="utf-8")
    assert "generated by scripts/gen_mcp_reference.py" in text.lower()

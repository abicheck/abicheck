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

_TAINTED_MODULE_NAMES = (
    "mcp",
    "mcp.server",
    "mcp.server.fastmcp",
    "abicheck.mcp_shared",
    "abicheck.mcp_server",
    "abicheck.mcp_server_project",
)


def _mcp_server_is_tainted() -> bool:
    # Two distinct ways a prior test module can leave this poisoned:
    #  (1) sys.modules["mcp"] is *currently* a MagicMock (test_mcp_server_unit.py/
    #      test_mcp_server_coverage.py leave this behind permanently) -- any
    #      fresh import right now would pick up the mock.
    #  (2) sys.modules["mcp"] has since been restored to the real package (or
    #      removed), but "abicheck.mcp_server" is already cached from when it
    #      *was* mocked (test_mcp_server_coverage_gaps.py restores sys.modules
    #      for the mcp.* chain but never touches the cached abicheck.mcp_server
    #      module or the abicheck package's `mcp_server` attribute) -- reusing
    #      that cache would silently check a mocked FastMCP instance even
    #      though the real mcp package is otherwise available.
    if isinstance(sys.modules.get("mcp"), MagicMock):
        return True
    cached = sys.modules.get("abicheck.mcp_server")
    return cached is not None and isinstance(getattr(cached, "mcp", None), MagicMock)


@pytest.fixture(autouse=True)
def _real_abicheck_mcp_server():
    """tests/test_mcp_server_unit.py and tests/test_mcp_server_coverage.py
    install a MagicMock under sys.modules["mcp"] via setdefault and never
    restore it; tests/test_mcp_server_coverage_gaps.py/test_cov95_misc.py do
    restore sys.modules for the mcp.* chain, but neither clears the
    `abicheck.mcp_server` module (or the `abicheck` package's `mcp_server`
    attribute) that was cached while mcp was mocked. Either way,
    `abicheck.mcp_server` ends up cached with a mocked FastMCP instance whose
    tool registry is empty -- silently breaking this file's assumption that
    it exercises the real, live MCP server (reproduced with `pytest
    tests/test_mcp_server_unit.py tests/test_mcp_reference.py` and with
    `pytest tests/test_mcp_server_coverage_gaps.py tests/test_mcp_reference.py`).

    This must run at test-execution time, not import/collection time: pytest
    collects every file before running any test, so a module-level purge
    here would fire during our own collection -- before test_mcp_server_unit.py's
    tests actually run -- and rip the mock out from under them instead.
    """
    tainted = _mcp_server_is_tainted()
    saved = {name: sys.modules.get(name) for name in _TAINTED_MODULE_NAMES}
    abicheck_pkg = sys.modules.get("abicheck")
    # Every "abicheck.X" submodule name (not just "abicheck.mcp_server") gets
    # its own attribute set on the cached `abicheck` package object the
    # moment it's first imported; popping sys.modules alone doesn't clear
    # that attribute. All three of mcp_shared/mcp_server/mcp_server_project
    # need the same treatment, or a later test's `from abicheck import
    # mcp_shared` (etc.) would keep resolving to a stale mocked module via
    # the attribute even after sys.modules has been restored (CodeRabbit
    # review: the original fix only handled mcp_server).
    _pkg_attrs = tuple(
        name.split(".", 1)[1] for name in _TAINTED_MODULE_NAMES if name.startswith("abicheck.")
    )
    had_attr = {
        attr: abicheck_pkg is not None and hasattr(abicheck_pkg, attr)
        for attr in _pkg_attrs
    }
    saved_attr = {attr: getattr(abicheck_pkg, attr, None) for attr in _pkg_attrs}
    if tainted:
        for name in _TAINTED_MODULE_NAMES:
            sys.modules.pop(name, None)
        # Popping "abicheck.mcp_server" (etc.) from sys.modules isn't enough
        # on its own: Python's `from abicheck import mcp_server` first tries
        # `getattr(abicheck, "mcp_server")`, and the earlier (mocked) import
        # already left that attribute set on the cached `abicheck` package
        # object -- so without this, `from abicheck import mcp_server` below
        # (including inside gen_mcp_reference.py's own render()) would keep
        # resolving to the stale mocked module instead of a fresh real one.
        for attr in _pkg_attrs:
            if had_attr[attr]:
                delattr(abicheck_pkg, attr)
    try:
        yield
    finally:
        if tainted:
            for name, mod in saved.items():
                if mod is not None:
                    sys.modules[name] = mod
                else:
                    sys.modules.pop(name, None)
            for attr in _pkg_attrs:
                if had_attr[attr]:
                    setattr(abicheck_pkg, attr, saved_attr[attr])
                elif abicheck_pkg is not None and hasattr(abicheck_pkg, attr):
                    # had_attr[attr] was False, so the fresh real import our
                    # own test triggered is what created this attribute --
                    # leaving it in place would let a later test's `from
                    # abicheck import X` bypass the mock we just restored in
                    # sys.modules.
                    delattr(abicheck_pkg, attr)


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


def test_fixture_teardown_does_not_leak_a_fresh_import_when_attr_was_absent():
    # Regression: when sys.modules["mcp"] is mocked but the `abicheck`
    # package has no `mcp_server` attribute cached yet (had_attr False), the
    # real import a test triggers inside the fixture's `yield` sets that
    # attribute as an ordinary side effect of `from abicheck import
    # mcp_server`. The teardown must delete it again -- merely skipping the
    # restore (as an earlier version of this fixture did) leaves the real
    # module cached on the package, so a later test's `from abicheck import
    # mcp_server` would bypass the mock this fixture just put back into
    # sys.modules.
    import abicheck

    saved = {name: sys.modules.get(name) for name in _TAINTED_MODULE_NAMES}
    had_attr_before = hasattr(abicheck, "mcp_server")
    saved_attr_before = getattr(abicheck, "mcp_server", None)
    for name in _TAINTED_MODULE_NAMES:
        sys.modules.pop(name, None)
    if had_attr_before:
        delattr(abicheck, "mcp_server")

    try:
        sys.modules["mcp"] = MagicMock()
        assert not hasattr(abicheck, "mcp_server")

        gen = _real_abicheck_mcp_server.__wrapped__()
        next(gen)  # run the fixture's setup half

        # Simulate what a test body does: a real import, now possible
        # because setup cleared the mock out of sys.modules.
        from abicheck import mcp_server  # noqa: F401

        assert hasattr(abicheck, "mcp_server")

        with pytest.raises(StopIteration):
            next(gen)  # run the fixture's teardown half

        assert not hasattr(abicheck, "mcp_server"), (
            "teardown left a real abicheck.mcp_server cached on the package "
            "even though it wasn't there before this fixture ran"
        )
        assert isinstance(sys.modules.get("mcp"), MagicMock)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
        if had_attr_before:
            abicheck.mcp_server = saved_attr_before
        elif hasattr(abicheck, "mcp_server"):
            delattr(abicheck, "mcp_server")

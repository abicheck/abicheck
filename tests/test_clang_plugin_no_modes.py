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

"""Regression guard for ADR-038 C.8 / recommendation P0 #1: the Clang facts
plugin must have exactly one canonical, always-complete collection profile —
no user-selectable fact-family mode.

Pure text scan of the plugin source/build files; needs no compiler toolchain,
so it runs in the default fast lane rather than the `clang-plugin` workflow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PLUGIN_DIR = (
    Path(__file__).resolve().parent.parent / "contrib" / "abicheck-clang-plugin"
)
_PLUGIN_CPP = _PLUGIN_DIR / "AbicheckFactsPlugin.cpp"
_CMAKE = _PLUGIN_DIR / "CMakeLists.txt"

#: Fact-family-narrowing flags the design explicitly forbids (recommendation
#: P0 #1). Collection must always be complete; only *reporting*/coverage may
#: describe what happened, never a request to collect less.
_BANNED_MODE_TOKENS = (
    "minimal",
    "types-only",
    "no-macros",
    "skip-inline-bodies",
    "skip-templates",
    "no-source-edges",
    "fast-hashes",
)


def test_plugin_source_present() -> None:
    """Sanity: the file this guard scans actually exists (else the scan below
    would trivially pass for the wrong reason)."""
    assert _PLUGIN_CPP.is_file()


@pytest.mark.parametrize("token", _BANNED_MODE_TOKENS)
def test_no_fact_family_mode_flags_in_plugin_source(token: str) -> None:
    text = _PLUGIN_CPP.read_text(encoding="utf-8")
    assert token not in text, (
        f"AbicheckFactsPlugin.cpp mentions {token!r} — ADR-038 C.8 / recommendation "
        "P0 #1 forbids user-selectable fact-family collection modes. Coverage "
        "reporting (complete/empty-confirmed/partial/unsupported/failed) is "
        "allowed; a flag that narrows what is collected is not."
    )


@pytest.mark.parametrize("token", _BANNED_MODE_TOKENS)
def test_no_fact_family_mode_flags_in_cmake(token: str) -> None:
    text = _CMAKE.read_text(encoding="utf-8")
    assert token not in text


def test_only_one_fact_set_version_constant() -> None:
    """The plugin declares exactly one ``kFactSetVersion`` — one canonical
    fact-set contract per build, not a selectable set of versions."""
    text = _PLUGIN_CPP.read_text(encoding="utf-8")
    assert text.count("kFactSetVersion =") == 1


def test_fact_set_name_matches_python_source_of_truth() -> None:
    """The plugin's ``kFactSetName`` literal must match
    ``source_abi.SOURCE_ABI_FACT_SET_NAME`` — two independent constants kept in
    sync by convention (documented at both definition sites), not shared code
    (the plugin is C++, the schema is Python). This guard is the regression
    check for that convention drifting apart.
    """
    from abicheck.buildsource.source_abi import SOURCE_ABI_FACT_SET_NAME

    text = _PLUGIN_CPP.read_text(encoding="utf-8")
    assert f'"{SOURCE_ABI_FACT_SET_NAME}"' in text

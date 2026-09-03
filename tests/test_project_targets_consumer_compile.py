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

"""Tests for ``profiles.<id>.consumer_compile`` (G34 Phase 0), split out of
``test_project_targets.py`` to keep that file under the AI-readiness
file-size hard cap.

Covers the schema side only: shape validation and to_dict/from_dict
round-tripping of the separate producer/consumer compiler-profile overlay.
The projection into ``run-plan.json``'s ``consumer_compile_gcc_path``/
``consumer_compile_gcc_options`` is covered by ``test_run_plan.py``'s
``TestConsumerCompileOverlayProjection``.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.project_targets import ProfileSpec, ProjectTargetsConfig


def test_profile_consumer_compile_overlay_parses_full_shape() -> None:
    """G34 Phase 0: ``consumer_compile:`` accepts the identical
    ProfileCompileSpec shape as ``compile:``, coexisting with it as a
    separate, independent overlay on the same profile."""
    config = ProjectTargetsConfig.from_dict(
        {
            "profiles": {
                "linux-gcc14-build-client-clang20": {
                    "os": "linux",
                    "arch": "x86_64",
                    "compile": {
                        "compiler_family": "gcc",
                        "binding": "gcc14",
                        "standard": "gnu++17",
                    },
                    "consumer_compile": {
                        "compiler_family": "clang",
                        "compiler_version": ">=20,<21",
                        "target": "x86_64-linux-gnu",
                        "standard": "gnu++20",
                        "stdlib": "libc++",
                        "binding": "clang20",
                        "abi_macros": {"_LIBCPP_ABI_VERSION": "2"},
                        "args": ["-fno-rtti"],
                    },
                }
            }
        }
    )
    profile = config.profiles["linux-gcc14-build-client-clang20"]
    assert profile.compile is not None
    assert profile.compile.compiler_family == "gcc"
    assert profile.consumer_compile is not None
    assert profile.consumer_compile.compiler_family == "clang"
    assert profile.consumer_compile.compiler_version == ">=20,<21"
    assert profile.consumer_compile.target == "x86_64-linux-gnu"
    assert profile.consumer_compile.standard == "gnu++20"
    assert profile.consumer_compile.stdlib == "libc++"
    assert profile.consumer_compile.binding == "clang20"
    assert profile.consumer_compile.abi_macros == {"_LIBCPP_ABI_VERSION": "2"}
    assert profile.consumer_compile.args == ["-fno-rtti"]
    # Round-trips through to_dict/from_dict.
    round_tripped = ProfileSpec.from_dict(
        "linux-gcc14-build-client-clang20", profile.to_dict()
    )
    assert round_tripped.to_dict() == profile.to_dict()


def test_profile_without_consumer_compile_overlay_is_none() -> None:
    """A profile with no ``consumer_compile:`` behaves exactly as today --
    absence is the default, not a breaking change for existing configs."""
    config = ProjectTargetsConfig.from_dict(
        {
            "profiles": {
                "linux": {
                    "os": "linux",
                    "compile": {"compiler_family": "gcc"},
                }
            }
        }
    )
    profile = config.profiles["linux"]
    assert profile.compile is not None
    assert profile.consumer_compile is None
    assert "consumer_compile" not in profile.to_dict()


def test_profile_consumer_compile_overlay_unknown_key_raises() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        ProjectTargetsConfig.from_dict(
            {"profiles": {"linux": {"consumer_compile": {"bogus": "x"}}}}
        )


def test_profile_consumer_compile_overlay_not_a_mapping_raises() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        ProjectTargetsConfig.from_dict(
            {"profiles": {"linux": {"consumer_compile": "not-a-mapping"}}}
        )


def test_profile_consumer_compile_overlay_explicit_null_raises() -> None:
    """A bare ``consumer_compile:`` key with nothing under it (YAML parses
    this as ``None``, a real, plausible typo when a user means to leave the
    block for later) is REJECTED at the public boundary -- the same
    ``not-a-mapping`` guard above, exercised for the one shape that isn't a
    string. Per bug-class-regression-testing.md Phase 6's own invariant
    ("reaches every consumer with identical semantics, or is rejected at
    the boundary -- no third state"): silently treating ``None`` as
    "absent" here would create exactly that third state, since ``None`` is
    not the same value ``ProfileSpec.from_dict`` uses to mean "no overlay"
    (a missing dict key)."""
    with pytest.raises(ValueError, match="must be a mapping"):
        ProjectTargetsConfig.from_dict(
            {"profiles": {"linux": {"consumer_compile": None}}}
        )


def test_profile_consumer_compile_overlay_explicit_empty_mapping_is_indistinguishable_from_absent() -> (
    None
):
    """``consumer_compile: {}`` (present, explicitly empty) is a distinct
    *input* shape from an omitted key, but per ``ProfileCompileSpec``'s own
    ``is_empty``-based design (see its docstring) the two must resolve to
    identical downstream behavior -- both `to_dict()`-omitted and both
    treated as "no overlay" for run-plan projection. Genuinely different
    from ``test_profile_without_consumer_compile_overlay_is_none`` at the
    Python-object level (``consumer_compile`` is a real, present
    ``ProfileCompileSpec()`` here, not ``None``) even though the two must
    behave the same everywhere a consumer reads them."""
    omitted = ProjectTargetsConfig.from_dict(
        {"profiles": {"linux": {"compile": {"compiler_family": "gcc"}}}}
    ).profiles["linux"]
    explicit_empty = ProjectTargetsConfig.from_dict(
        {
            "profiles": {
                "linux": {"compile": {"compiler_family": "gcc"}, "consumer_compile": {}}
            }
        }
    ).profiles["linux"]

    assert omitted.consumer_compile is None
    assert explicit_empty.consumer_compile is not None
    assert explicit_empty.consumer_compile.is_empty

    # Both project identically downstream, per to_dict() -- the shape this
    # module's own consumer (run_plan.py) and the "at least equal" bar
    # AGENTS.md sets for a should-be-inert distinction.
    assert "consumer_compile" not in omitted.to_dict()
    assert "consumer_compile" not in explicit_empty.to_dict()


def test_profile_unknown_top_level_key_still_rejects_consumer_compile_typo() -> None:
    """A typo'd key at the profile level (not inside consumer_compile:
    itself) is still caught by the existing unknown-key guard, now that
    consumer_compile is a recognized sibling of compile."""
    with pytest.raises(ValueError, match="unknown key"):
        ProjectTargetsConfig.from_dict(
            {"profiles": {"linux": {"consumer_compiles": {}}}}
        )

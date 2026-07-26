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

"""Tests for ``buildsource.link_attribution`` (ADR-053 D1).

Covers both attribution channels independently (target-graph, link-unit-
graph), their combination (union, not override), transitive static-library
folding on each, the deliberate SHARED_LIBRARY dependency hard-stop, and the
"unknown source" contract (absent from the result, not present-with-empty).
"""

from __future__ import annotations

from abicheck.buildsource.build_evidence import (
    BuildEvidence,
    CompileUnit,
    LinkUnit,
    Target,
    TargetKind,
)
from abicheck.buildsource.link_attribution import (
    attribute_sources_to_targets,
    normalize_source_path,
)


def test_normalize_source_path_strips_leading_dot_slash_and_backslashes():
    assert normalize_source_path("./src/a.cpp") == "src/a.cpp"
    assert normalize_source_path("src\\a.cpp") == "src/a.cpp"
    assert normalize_source_path("src/a.cpp") == "src/a.cpp"


def test_unknown_source_is_absent_not_empty():
    ev = BuildEvidence(
        targets=[Target(id="target://libfoo", source_files=["src/foo.cpp"])]
    )
    attr = attribute_sources_to_targets(ev)
    assert "src/unrelated.cpp" not in attr
    assert attr.get("src/unrelated.cpp", frozenset()) == frozenset()


def test_target_with_no_id_is_skipped():
    ev = BuildEvidence(
        targets=[
            Target(id="", source_files=["src/anon.cpp"]),
            Target(id="target://libfoo", source_files=["src/foo.cpp"]),
        ]
    )
    attr = attribute_sources_to_targets(ev)
    assert "src/anon.cpp" not in attr
    assert attr["src/foo.cpp"] == frozenset({"target://libfoo"})


def test_target_with_blank_source_entry_is_skipped():
    ev = BuildEvidence(
        targets=[Target(id="target://libfoo", source_files=["", "src/foo.cpp"])]
    )
    attr = attribute_sources_to_targets(ev)
    assert attr["src/foo.cpp"] == frozenset({"target://libfoo"})
    assert "" not in attr


class TestTargetGraphChannel:
    def test_direct_source_attributes_to_its_own_target(self):
        ev = BuildEvidence(
            targets=[Target(id="target://libfoo", source_files=["src/foo.cpp"])]
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/foo.cpp"] == frozenset({"target://libfoo"})

    def test_static_library_dependency_folds_into_dependent(self):
        ev = BuildEvidence(
            targets=[
                Target(
                    id="target://libfoo",
                    kind=TargetKind.SHARED_LIBRARY,
                    source_files=["src/foo.cpp"],
                    dependencies=["target://common"],
                ),
                Target(
                    id="target://common",
                    kind=TargetKind.STATIC_LIBRARY,
                    source_files=["src/common.cpp"],
                ),
            ]
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/common.cpp"] == frozenset({"target://common", "target://libfoo"})

    def test_object_library_folds_transitively_through_static_library(self):
        ev = BuildEvidence(
            targets=[
                Target(
                    id="target://libfoo",
                    kind=TargetKind.SHARED_LIBRARY,
                    dependencies=["target://common"],
                ),
                Target(
                    id="target://common",
                    kind=TargetKind.STATIC_LIBRARY,
                    dependencies=["target://base"],
                ),
                Target(
                    id="target://base",
                    kind=TargetKind.OBJECT_LIBRARY,
                    source_files=["src/base.cpp"],
                ),
            ]
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/base.cpp"] == frozenset(
            {"target://base", "target://common", "target://libfoo"}
        )

    def test_shared_library_dependency_is_a_hard_stop(self):
        """`app` depends on `libfoo` (a real .so) -- app links against
        libfoo's public interface, never absorbs its object code."""
        ev = BuildEvidence(
            targets=[
                Target(
                    id="target://app",
                    kind=TargetKind.EXECUTABLE,
                    source_files=["src/main.cpp"],
                    dependencies=["target://libfoo"],
                ),
                Target(
                    id="target://libfoo",
                    kind=TargetKind.SHARED_LIBRARY,
                    source_files=["src/foo.cpp"],
                ),
            ]
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/main.cpp"] == frozenset({"target://app"})
        assert attr["src/foo.cpp"] == frozenset({"target://libfoo"})

    def test_source_shared_between_two_targets_attributes_to_both(self):
        ev = BuildEvidence(
            targets=[
                Target(
                    id="target://libfoo",
                    kind=TargetKind.SHARED_LIBRARY,
                    dependencies=["target://common"],
                ),
                Target(
                    id="target://libbar",
                    kind=TargetKind.SHARED_LIBRARY,
                    dependencies=["target://common"],
                ),
                Target(
                    id="target://common",
                    kind=TargetKind.STATIC_LIBRARY,
                    source_files=["src/common.cpp"],
                ),
            ]
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/common.cpp"] == frozenset(
            {"target://common", "target://libfoo", "target://libbar"}
        )

    def test_interface_dependency_is_a_hard_stop_not_looked_past(self):
        """Pins current behavior (code review, PR #632): folding is
        per-direct-dependent, not fully transitive through a non-absorbed
        kind. `iface` directly depends on the OBJECT_LIBRARY `obj`, so
        `obj`'s sources fold into `iface` (an OBJECT_LIBRARY folds into ANY
        direct dependent, regardless of the dependent's own kind) -- but
        `iface` itself is INTERFACE, not in _ABSORBED_KINDS, so when `app`
        walks its own dependency on `iface`, that's a hard stop: `app`'s walk
        neither folds `iface`'s (inherited) sources in nor continues past it
        to `obj`. A real OBJECT_LIBRARY sitting *behind* an INTERFACE
        dependency is therefore NOT folded into a target two hops away, even
        though CMake usage-requirement propagation through an INTERFACE
        library can, in practice, still cause that object library's objects
        to end up linked into whatever ultimately depends on the interface
        target. Documented as current, deliberately-scoped behavior -- not
        asserted here to be the only correct design, just the one this code
        implements today."""
        ev = BuildEvidence(
            targets=[
                Target(
                    id="target://app",
                    kind=TargetKind.EXECUTABLE,
                    source_files=["src/main.cpp"],
                    dependencies=["target://iface"],
                ),
                Target(
                    id="target://iface",
                    kind=TargetKind.INTERFACE,
                    dependencies=["target://obj"],
                ),
                Target(
                    id="target://obj",
                    kind=TargetKind.OBJECT_LIBRARY,
                    source_files=["src/obj.cpp"],
                ),
            ]
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/main.cpp"] == frozenset({"target://app"})
        # Attributed to obj itself and to iface (obj's direct dependent) --
        # but NOT to app, two hops away through the INTERFACE hard stop.
        assert attr["src/obj.cpp"] == frozenset({"target://obj", "target://iface"})

    def test_dependency_cycle_does_not_infinite_loop(self):
        # Pathological/malformed input (a real build graph is a DAG) -- the
        # visited-set guard must still terminate and produce a sane result.
        ev = BuildEvidence(
            targets=[
                Target(
                    id="target://a",
                    kind=TargetKind.STATIC_LIBRARY,
                    source_files=["src/a.cpp"],
                    dependencies=["target://b"],
                ),
                Target(
                    id="target://b",
                    kind=TargetKind.STATIC_LIBRARY,
                    source_files=["src/b.cpp"],
                    dependencies=["target://a"],
                ),
            ]
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/a.cpp"] == frozenset({"target://a", "target://b"})
        assert attr["src/b.cpp"] == frozenset({"target://a", "target://b"})


class TestLinkUnitGraphChannel:
    def test_direct_object_input_attributes_via_target_id(self):
        ev = BuildEvidence(
            compile_units=[CompileUnit(id="cu://a", source="src/a.cpp", output="build/a.o")],
            link_units=[
                LinkUnit(
                    id="link://build/libfoo.so",
                    target_id="target://libfoo",
                    output="build/libfoo.so",
                    kind="shared_library",
                    inputs=["build/a.o"],
                )
            ],
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/a.cpp"] == frozenset({"target://libfoo"})

    def test_no_target_id_falls_back_to_output_basename_identity(self):
        ev = BuildEvidence(
            compile_units=[CompileUnit(id="cu://a", source="src/a.cpp", output="build/a.o")],
            link_units=[
                LinkUnit(
                    id="link://build/out/libfoo.so",
                    output="build/out/libfoo.so",
                    kind="shared_library",
                    inputs=["build/a.o"],
                )
            ],
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/a.cpp"] == frozenset({"output://libfoo.so"})

    def test_transitive_static_library_link_unit_resolves(self):
        ev = BuildEvidence(
            compile_units=[
                CompileUnit(id="cu://a", source="src/a.cpp", output="build/a.o"),
            ],
            link_units=[
                LinkUnit(
                    id="link://build/libstatic.a",
                    output="build/libstatic.a", kind="static_library", inputs=["build/a.o"]
                ),
                LinkUnit(
                    id="link://build/app",
                    output="build/app",
                    kind="executable",
                    inputs=["build/libstatic.a"],
                ),
            ],
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/a.cpp"] == frozenset({"output://app"})

    def test_multi_hop_static_library_link_unit_chain_resolves(self):
        # Symmetric with the target-graph channel's own two-hop
        # object_library -> static_library -> shared_library test
        # (test_object_library_folds_transitively_through_static_library) --
        # the link-unit channel's own transitive resolution must go more
        # than one static-library hop deep too: libbase.a is itself an
        # input to libmid.a, which is an input to the terminal executable.
        ev = BuildEvidence(
            compile_units=[
                CompileUnit(id="cu://a", source="src/a.cpp", output="build/a.o"),
            ],
            link_units=[
                LinkUnit(
                    id="link://build/libbase.a",
                    output="build/libbase.a",
                    kind="static_library",
                    inputs=["build/a.o"],
                ),
                LinkUnit(
                    id="link://build/libmid.a",
                    output="build/libmid.a",
                    kind="static_library",
                    inputs=["build/libbase.a"],
                ),
                LinkUnit(
                    id="link://build/app",
                    output="build/app",
                    kind="executable",
                    inputs=["build/libmid.a"],
                ),
            ],
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/a.cpp"] == frozenset({"output://app"})

    def test_terminal_link_unit_named_as_another_link_units_input_is_a_hard_stop(self):
        # A dependent naming another DSO's *output path* directly (rather
        # than -lfoo) must not fold that DSO's own objects into itself --
        # mirrors the target-graph channel's SHARED_LIBRARY hard stop.
        ev = BuildEvidence(
            compile_units=[
                CompileUnit(id="cu://a", source="src/a.cpp", output="build/liba_impl.o"),
                CompileUnit(id="cu://b", source="src/b.cpp", output="build/b.o"),
            ],
            link_units=[
                LinkUnit(
                    id="link://build/liba.so",
                    output="build/liba.so",
                    kind="shared_library",
                    inputs=["build/liba_impl.o"],
                ),
                LinkUnit(
                    id="link://build/app",
                    output="build/app",
                    kind="executable",
                    # Explicit .so path, not -lfoo -- liba.so's own objects
                    # must NOT be folded into app's attribution.
                    inputs=["build/b.o", "build/liba.so"],
                ),
            ],
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/a.cpp"] == frozenset({"output://liba.so"})
        assert attr["src/b.cpp"] == frozenset({"output://app"})

    def test_static_library_link_unit_itself_is_never_a_terminal_identity(self):
        # Only shared_library/executable link units are attribution targets
        # -- a bare static-library link unit with nothing consuming it must
        # not appear as an identity anywhere in the result.
        ev = BuildEvidence(
            compile_units=[CompileUnit(id="cu://a", source="src/a.cpp", output="build/a.o")],
            link_units=[
                LinkUnit(
                    id="link://build/libstatic.a",
                    output="build/libstatic.a", kind="static_library", inputs=["build/a.o"],
                )
            ],
        )
        attr = attribute_sources_to_targets(ev)
        assert "src/a.cpp" not in attr

    def test_link_unit_cycle_does_not_infinite_loop(self):
        ev = BuildEvidence(
            link_units=[
                LinkUnit(id="link://a.so", output="a.so", kind="shared_library", inputs=["b.so"]),
                LinkUnit(id="link://b.so", output="b.so", kind="shared_library", inputs=["a.so"]),
            ]
        )
        # Must simply terminate with no attribution (neither resolves to a
        # real compile-unit object), not hang or raise.
        attr = attribute_sources_to_targets(ev)
        assert attr == {}


class TestCombinedChannels:
    def test_both_channels_agreeing_union_to_one_identity_set(self):
        ev = BuildEvidence(
            targets=[Target(id="target://libfoo", source_files=["src/foo.cpp"])],
            compile_units=[CompileUnit(id="cu://a", source="src/foo.cpp", output="build/a.o")],
            link_units=[
                LinkUnit(
                    id="link://build/libfoo.so",
                    target_id="target://libfoo",
                    output="build/libfoo.so",
                    kind="shared_library",
                    inputs=["build/a.o"],
                )
            ],
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/foo.cpp"] == frozenset({"target://libfoo"})

    def test_channels_disagreeing_union_both_identities(self):
        # A pathological/inconsistent evidence set (real builds shouldn't
        # produce this) -- the module unions rather than picks a winner, so
        # a disagreement is visible (more identities), never silently
        # resolved to a possibly-wrong single answer.
        ev = BuildEvidence(
            targets=[Target(id="target://libfoo", source_files=["src/foo.cpp"])],
            compile_units=[CompileUnit(id="cu://a", source="src/foo.cpp", output="build/a.o")],
            link_units=[
                LinkUnit(
                    id="link://build/libbar.so",
                    target_id="target://libbar",
                    output="build/libbar.so",
                    kind="shared_library",
                    inputs=["build/a.o"],
                )
            ],
        )
        attr = attribute_sources_to_targets(ev)
        assert attr["src/foo.cpp"] == frozenset({"target://libfoo", "target://libbar"})

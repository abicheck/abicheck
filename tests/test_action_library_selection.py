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

"""Declarative component selection (``actions/baseline``'s ``library-spec``).

These tests state the contract of the *primitive* -- "one artifact per
component, resolved through its symlink aliases", "a header pattern that
matches nothing is a refusal", "an include root is context, never an export
obligation" -- rather than only replaying the one integration it was extracted
from, per AGENTS.md's primitive-level property-test guidance.

The ELF fixtures are synthesized byte by byte rather than compiled: the two
fields selection actually reads (``e_type``, ``e_machine``) live at fixed
offsets in the file header, and building them directly is what lets the
machine-disagreement and not-a-shared-object cases be exercised on any host,
including one with no cross compiler. A separate, `slow`-free real-binary
lane is deliberately NOT added here -- `tests/test_action_baseline.py` already
drives a real dump through this resolution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.frontends.action.library_selection import (
    SelectionError,
    libraries_payload,
    read_elf_identity,
    resolve_library_set,
)

_ET_DYN = 3
_ET_EXEC = 2
_ET_REL = 1


def _elf(path: Path, *, e_type: int = _ET_DYN, machine: int = 62) -> Path:
    """Write a minimal little-endian ELF64 header at *path*."""
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4] = 2  # ELFCLASS64
    header[5] = 1  # ELFDATA2LSB
    header[6] = 1  # EV_CURRENT
    header[16:18] = e_type.to_bytes(2, "little")
    header[18:20] = machine.to_bytes(2, "little")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(header))
    return path


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A two-component installation: shared headers, disjoint ownership."""
    root = tmp_path / "inst"
    for name in ("core.h", "util.h", "hooks.h"):
        (root / "include" / "demo").mkdir(parents=True, exist_ok=True)
        (root / "include" / "demo" / name).write_text(
            "#pragma once\n", encoding="utf-8"
        )
    _elf(root / "lib" / "libdemo.so.1.2.3")
    _elf(root / "lib" / "libhooks.so.1.2.3")
    # The SONAME alias chain a real install carries.
    (root / "lib" / "libdemo.so.1").symlink_to("libdemo.so.1.2.3")
    (root / "lib" / "libdemo.so").symlink_to("libdemo.so.1")
    (root / "lib" / "libhooks.so").symlink_to("libhooks.so.1.2.3")
    return root


_SPEC = [
    {
        "name": "libdemo",
        "artifact": "lib/libdemo.so*",
        "header": ["include/demo/*.h"],
        "header_exclude": ["include/demo/hooks.h"],
        "include": ["include"],
    },
    {
        "name": "libhooks",
        "artifact": "lib/libhooks.so*",
        "header": ["include/demo/hooks.h"],
        "include": ["include"],
    },
]


class TestOwnership:
    def test_two_components_split_one_installed_header_tree(self, tree: Path) -> None:
        resolved = resolve_library_set(_SPEC, root=tree)
        by_name = {library.name: library for library in resolved}
        assert [p.name for p in by_name["libdemo"].headers] == ["core.h", "util.h"]
        assert [p.name for p in by_name["libhooks"].headers] == ["hooks.h"]

    def test_include_root_is_context_not_an_export_obligation(self, tree: Path) -> None:
        """The whole point of the split above.

        ``libhooks`` names the same include root that holds ``core.h`` and
        ``util.h`` -- and still owns exactly one header. A resolver that let an
        include root widen ownership would make both components own everything,
        and the sibling's every change would show up as this one's.
        """
        resolved = {lib.name: lib for lib in resolve_library_set(_SPEC, root=tree)}
        hooks = resolved["libhooks"]
        assert tree / "include" in hooks.includes
        assert len(hooks.headers) == 1

    def test_exclusion_removes_only_what_it_names(self, tree: Path) -> None:
        spec = [dict(_SPEC[0], header_exclude=["include/demo/util.h"])]
        (resolved,) = resolve_library_set(spec, root=tree)
        assert [p.name for p in resolved.headers] == ["core.h", "hooks.h"]


class TestArtifactResolution:
    def test_symlink_aliases_collapse_to_one_real_artifact(self, tree: Path) -> None:
        """Three matches, one file -- an alias chain is not ambiguity.

        Banning symlinks outright would also be wrong: a pattern that matched
        only ``libdemo.so`` (the unversioned alias, which is what a consumer
        actually links against) must still resolve.
        """
        for pattern in ("lib/libdemo.so*", "lib/libdemo.so", "lib/libdemo.so.1"):
            spec = [dict(_SPEC[0], artifact=pattern)]
            (resolved,) = resolve_library_set(spec, root=tree)
            assert resolved.artifact == (tree / "lib" / "libdemo.so.1.2.3").resolve()

    def test_two_genuinely_distinct_files_are_ambiguous(self, tree: Path) -> None:
        spec = [dict(_SPEC[0], artifact="lib/lib*.so.1.2.3")]
        with pytest.raises(SelectionError, match="ambiguous"):
            resolve_library_set(spec, root=tree)

    def test_a_link_out_of_the_declared_root_is_refused(
        self, tmp_path: Path, tree: Path
    ) -> None:
        outside = _elf(tmp_path / "elsewhere" / "libsneak.so.1")
        (tree / "lib" / "libsneak.so").symlink_to(outside)
        spec = [dict(_SPEC[0], artifact="lib/libsneak.so*")]
        with pytest.raises(SelectionError, match="outside the declared root"):
            resolve_library_set(spec, root=tree)

    def test_no_match_is_refused_rather_than_an_empty_component(
        self, tree: Path
    ) -> None:
        spec = [dict(_SPEC[0], artifact="lib/libabsent.so*")]
        with pytest.raises(SelectionError, match="matched no regular file"):
            resolve_library_set(spec, root=tree)

    @pytest.mark.parametrize("e_type", [_ET_EXEC, _ET_REL])
    def test_only_a_shared_object_can_own_a_library_abi(
        self, tree: Path, e_type: int
    ) -> None:
        _elf(tree / "lib" / "libstatic.so.1", e_type=e_type)
        spec = [dict(_SPEC[0], artifact="lib/libstatic.so.1")]
        with pytest.raises(SelectionError, match="not a shared object"):
            resolve_library_set(spec, root=tree)

    def test_a_non_elf_artifact_is_refused_unless_opted_out(self, tree: Path) -> None:
        (tree / "lib" / "libtext.so.1").write_text("not an ELF file", encoding="utf-8")
        spec = [dict(_SPEC[0], artifact="lib/libtext.so.1")]
        with pytest.raises(SelectionError, match="not an ELF object"):
            resolve_library_set(spec, root=tree)
        # ...and the opt-out really is an opt-out, not merely a softer message.
        (resolved,) = resolve_library_set(spec, root=tree, require_elf=False)
        assert resolved.machine == ""


class TestRefusals:
    def test_a_header_pattern_matching_nothing_is_refused(self, tree: Path) -> None:
        spec = [dict(_SPEC[0], header=["include/demo/absent.h"])]
        with pytest.raises(SelectionError, match="matched no file"):
            resolve_library_set(spec, root=tree)

    def test_a_stale_exclusion_is_refused(self, tree: Path) -> None:
        """An exclusion that excludes nothing is how a component silently
        re-acquires a header that has since moved to a sibling."""
        spec = [dict(_SPEC[0], header_exclude=["include/demo/gone.h"])]
        with pytest.raises(SelectionError, match="excludes nothing|matched no file"):
            resolve_library_set(spec, root=tree)

    def test_a_missing_include_root_is_refused(self, tree: Path) -> None:
        spec = [dict(_SPEC[0], include=["include", "no/such/dir"])]
        with pytest.raises(SelectionError, match="matched no directory"):
            resolve_library_set(spec, root=tree)

    def test_a_duplicate_component_name_is_refused(self, tree: Path) -> None:
        with pytest.raises(SelectionError, match="declared twice"):
            resolve_library_set([_SPEC[0], dict(_SPEC[0])], root=tree)

    def test_two_components_sharing_one_artifact_are_refused(self, tree: Path) -> None:
        spec = [_SPEC[0], dict(_SPEC[1], artifact="lib/libdemo.so")]
        with pytest.raises(SelectionError, match="same artifact"):
            resolve_library_set(spec, root=tree)

    def test_a_component_name_that_is_a_path_is_refused(self, tree: Path) -> None:
        spec = [dict(_SPEC[0], name="../escape")]
        with pytest.raises(SelectionError, match="path separator"):
            resolve_library_set(spec, root=tree)

    def test_a_misspelled_field_is_refused_not_ignored(self, tree: Path) -> None:
        """A silently-ignored ``header-exclude`` (hyphen) would widen ownership."""
        spec = [dict(_SPEC[0])]
        spec[0].pop("header_exclude")
        spec[0]["header-exclude"] = ["include/demo/hooks.h"]
        with pytest.raises(SelectionError, match="unrecognized field"):
            resolve_library_set(spec, root=tree)

    def test_a_path_the_libraries_contract_cannot_represent_is_refused(
        self, tmp_path: Path
    ) -> None:
        """``libraries`` serializes headers space-separated and word-splits them.

        A path containing a space cannot survive that round trip, so it is
        refused by name here rather than silently becoming two wrong paths in
        the dump command -- which is what the hand-rolled shell this replaces
        did.
        """
        root = tmp_path / "inst"
        (root / "inc lude").mkdir(parents=True)
        (root / "inc lude" / "a.h").write_text("", encoding="utf-8")
        _elf(root / "lib" / "libx.so.1")
        spec = [
            {"name": "libx", "artifact": "lib/libx.so.1", "header": ["inc lude/*.h"]}
        ]
        with pytest.raises(SelectionError, match="cannot\n?\\s*represent|contains"):
            resolve_library_set(spec, root=root)

    def test_excluding_every_header_is_refused(self, tree: Path) -> None:
        spec = [
            dict(
                _SPEC[0],
                header=["include/demo/hooks.h"],
                header_exclude=["include/demo/hooks.h"],
            )
        ]
        with pytest.raises(SelectionError, match="owning no declarations"):
            resolve_library_set(spec, root=tree)


class TestMachineAgreement:
    def test_components_built_for_different_machines_are_refused(
        self, tree: Path
    ) -> None:
        _elf(tree / "lib" / "libhooks.so.1.2.3", machine=183)  # EM_AARCH64
        with pytest.raises(SelectionError, match="same machine"):
            resolve_library_set(_SPEC, root=tree)

    def test_agreement_holds_for_the_ordinary_case(self, tree: Path) -> None:
        resolved = resolve_library_set(_SPEC, root=tree)
        assert {library.machine for library in resolved} == {"EM_X86_64"}

    def test_an_unnamed_machine_still_compares(self, tree: Path) -> None:
        """Agreement must not depend on this module knowing the machine's name.

        An unlisted ``e_machine`` is spelled numerically; two components
        carrying the same unlisted value still agree, and two different ones
        still disagree. A resolver that only compared *named* machines would
        silently accept a mixed set on any architecture it had not heard of.
        """
        _elf(tree / "lib" / "libdemo.so.1.2.3", machine=4242)
        _elf(tree / "lib" / "libhooks.so.1.2.3", machine=4242)
        assert {lib.machine for lib in resolve_library_set(_SPEC, root=tree)} == {
            "EM_4242"
        }
        _elf(tree / "lib" / "libhooks.so.1.2.3", machine=4243)
        with pytest.raises(SelectionError, match="same machine"):
            resolve_library_set(_SPEC, root=tree)


class TestElfIdentity:
    def test_big_endian_headers_are_read_correctly(self, tmp_path: Path) -> None:
        """``e_type``/``e_machine`` are byte-order dependent.

        A little-endian-only reader reports ET_DYN (3) as 768 on a big-endian
        object and refuses every shared library on that architecture.
        """
        header = bytearray(64)
        header[0:4] = b"\x7fELF"
        header[4], header[5] = 2, 2  # ELFCLASS64, ELFDATA2MSB
        header[16:18] = (_ET_DYN).to_bytes(2, "big")
        header[18:20] = (21).to_bytes(2, "big")  # EM_PPC64
        path = tmp_path / "libbe.so"
        path.write_bytes(bytes(header))
        identity = read_elf_identity(path)
        assert identity is not None
        assert (identity.e_type, identity.machine) == (_ET_DYN, "EM_PPC64")

    @pytest.mark.parametrize(
        "content", [b"", b"\x7fELF", b"MZ\x90\x00" + b"\0" * 60, b"not elf at all"]
    )
    def test_non_elf_and_truncated_input_reads_as_none(
        self, tmp_path: Path, content: bytes
    ) -> None:
        path = tmp_path / "thing"
        path.write_bytes(content)
        assert read_elf_identity(path) is None


class TestPayload:
    def test_the_payload_is_the_libraries_array_the_action_consumes(
        self, tree: Path
    ) -> None:
        payload = libraries_payload(resolve_library_set(_SPEC, root=tree))
        assert json.loads(json.dumps(payload)) == payload
        entry = next(row for row in payload if row["name"] == "libdemo")
        assert set(entry) == {"name", "artifact", "header", "include"}
        # Every path absolute: the dump runs from an unspecified directory.
        assert Path(entry["artifact"]).is_absolute()
        assert all(Path(part).is_absolute() for part in entry["header"].split())

    def test_stage_binary_survives_resolution(self, tree: Path) -> None:
        spec = [dict(_SPEC[0], stage_binary=True)]
        (entry,) = libraries_payload(resolve_library_set(spec, root=tree))
        assert entry["stage_binary"] is True

    def test_resolution_is_deterministic(self, tree: Path) -> None:
        """Two runs over one tree must agree, header order included.

        ``build_manifest``'s content digest folds the dumped surface, so an
        order that varied between runs would make an unchanged library look
        changed.
        """
        first = libraries_payload(resolve_library_set(_SPEC, root=tree))
        second = libraries_payload(resolve_library_set(_SPEC, root=tree))
        assert first == second


class TestBaselineActionWiring:
    """``actions/baseline``'s ``library-spec`` surface, as published.

    The resolution itself is covered above; what this pins is the composite
    wiring around it, where a mistake is invisible to every test that calls the
    resolver directly.
    """

    @staticmethod
    def _action() -> dict:
        import yaml

        path = (
            Path(__file__).resolve().parents[1] / "actions" / "baseline" / "action.yml"
        )
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_the_spec_inputs_are_declared(self) -> None:
        inputs = self._action()["inputs"]
        for name in ("library-spec", "library-root", "library-spec-require-elf"):
            assert name in inputs, name
            assert inputs[name]["description"].strip(), f"{name} has no help text"

    def test_libraries_became_optional(self) -> None:
        """Relaxed, never removed: every existing caller passes `libraries`."""
        libraries = self._action()["inputs"]["libraries"]
        assert libraries.get("required") is False
        assert libraries.get("default") == ""

    def test_resolution_runs_before_the_dump(self) -> None:
        """A resolve step ordered after the dump would resolve nothing in time,
        and the dump would fail on an empty `libraries` instead."""
        names = [step.get("name", "") for step in self._action()["runs"]["steps"]]
        assert names.index("Resolve the declarative library spec") < names.index(
            "Dump baseline-set"
        )

    def test_the_dump_prefers_the_resolved_libraries(self) -> None:
        dump = next(
            step
            for step in self._action()["runs"]["steps"]
            if step.get("id") == "run-baseline"
        )
        value = dump["env"]["INPUT_LIBRARIES"]
        assert "steps.libraries.outputs.libraries" in value
        # ...and still falls back to the input, so a caller that never sets
        # library-spec is bit-for-bit unchanged.
        assert "inputs.libraries" in value

    def test_declaring_both_is_refused(self) -> None:
        """Two declarations of one component set is the drift this removes."""
        resolve = next(
            step
            for step in self._action()["runs"]["steps"]
            if step.get("id") == "libraries"
        )
        assert 'if [[ -n "$INPUT_LIBRARIES" ]]' in resolve["run"]
        assert "not both" in resolve["run"]

    def test_every_spec_input_reaches_the_resolve_step(self) -> None:
        resolve = next(
            step
            for step in self._action()["runs"]["steps"]
            if step.get("id") == "libraries"
        )
        for name in ("library-spec", "library-root", "library-spec-require-elf"):
            env_name = f"INPUT_{name.upper().replace('-', '_')}"
            assert env_name in resolve["env"], name
            assert resolve["env"][env_name] == f"${{{{ inputs.{name} }}}}"

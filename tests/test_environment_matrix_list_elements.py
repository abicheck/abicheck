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

"""``EnvironmentMatrix.from_dict``'s list-typed fields must reject a
non-``str`` element, not just a non-``list`` container.

Codex review, P2 (PR #1221, round-8): ``.abicheck.yml``'s ``deployment:
compilers: [{name: gcc}]`` (a list of dicts rather than strings) reached
``EnvironmentMatrix.from_dict`` unrejected because only the *outer* ``list``
type was checked -- the nested dict element inside it was not. Since
``EnvironmentMatrix`` is a genuinely frozen, hashable dataclass (round-7
fix), hashing a ``CompareRequest`` carrying that matrix then raised
``TypeError: unhashable type: 'dict'`` at hash time, defeating the
structural-hash guarantee for a config ``from_dict`` had already accepted as
valid.

This is a general "outer-list-checked, elements-unchecked" gap, not a
``compilers``-only defect: ``sycl.backends``/``cuda.gpu_architectures`` had
the identical shape (an ``isinstance(..., list)`` check on the container,
followed by blind ``str(x)`` coercion of each element with no element-level
type check at all) -- silently stringifying a wrong-shaped element into
nonsense (e.g. ``sycl.backends: [{driver: x}]`` becoming the single backend
string ``"{'driver': 'x'}"``) instead of raising the clear config error
``strict=True`` promises, the same failure mode
``tests/test_environment_matrix_floor_types.py`` already covers for
``runtime_floors``' non-numeric-typed keys. This module states and checks
the general invariant -- every element of ``compilers``/``sycl.backends``/
``cuda.gpu_architectures`` must be a ``str``, checked in both lenient and
``strict=True`` mode -- across all three fields, plus one strict-mode
end-to-end ``.abicheck.yml`` case for the originally reported field, plus a
positive case proving a real ``EnvironmentMatrix`` built from an
all-``str`` ``compilers`` list is genuinely hashable end to end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.environment_matrix import EnvironmentMatrix

# A representative, deliberately varied set of non-str element shapes: a
# dict (the exact reported repro), a list, an int, a float, and a bool --
# covering the same "any type but the right one" spread
# test_environment_matrix_floor_types.py already exercises for
# runtime_floors' non-numeric-typed keys.
_BAD_ELEMENTS = [
    {"name": "gcc"},
    ["gcc-13"],
    13,
    13.0,
    True,
]
_BAD_ELEMENT_IDS = ["dict", "list", "int", "float", "bool"]


class TestCompilersRejectsNonStringElements:
    @pytest.mark.parametrize("bad_element", _BAD_ELEMENTS, ids=_BAD_ELEMENT_IDS)
    def test_lenient(self, bad_element: object) -> None:
        with pytest.raises(ValueError, match="'compilers' entries must be strings"):
            EnvironmentMatrix.from_dict({"compilers": ["gcc-13", bad_element]})

    @pytest.mark.parametrize("bad_element", _BAD_ELEMENTS, ids=_BAD_ELEMENT_IDS)
    def test_strict(self, bad_element: object) -> None:
        # The mode .abicheck.yml's `deployment:` block actually loads with.
        with pytest.raises(ValueError, match="'compilers' entries must be strings"):
            EnvironmentMatrix.from_dict(
                {"compilers": ["gcc-13", bad_element]}, strict=True
            )

    def test_all_string_compilers_still_accepted(self) -> None:
        matrix = EnvironmentMatrix.from_dict(
            {"compilers": ["gcc-13", "clang-17"]}, strict=True
        )
        assert matrix.compilers == ("gcc-13", "clang-17")


class TestSyclBackendsRejectsNonStringElements:
    @pytest.mark.parametrize("bad_element", _BAD_ELEMENTS, ids=_BAD_ELEMENT_IDS)
    def test_lenient(self, bad_element: object) -> None:
        with pytest.raises(ValueError, match="'sycl.backends' entries must be strings"):
            EnvironmentMatrix.from_dict(
                {"sycl": {"backends": ["level_zero", bad_element]}}
            )

    @pytest.mark.parametrize("bad_element", _BAD_ELEMENTS, ids=_BAD_ELEMENT_IDS)
    def test_strict(self, bad_element: object) -> None:
        with pytest.raises(ValueError, match="'sycl.backends' entries must be strings"):
            EnvironmentMatrix.from_dict(
                {"sycl": {"backends": ["level_zero", bad_element]}}, strict=True
            )

    def test_all_string_backends_still_accepted(self) -> None:
        matrix = EnvironmentMatrix.from_dict(
            {"sycl": {"backends": ["level_zero", "opencl"]}}, strict=True
        )
        assert matrix.sycl.backends == ("level_zero", "opencl")


class TestCudaGpuArchitecturesRejectsNonStringElements:
    @pytest.mark.parametrize("bad_element", _BAD_ELEMENTS, ids=_BAD_ELEMENT_IDS)
    def test_lenient(self, bad_element: object) -> None:
        with pytest.raises(
            ValueError, match="'cuda.gpu_architectures' entries must be strings"
        ):
            EnvironmentMatrix.from_dict(
                {"cuda": {"gpu_architectures": ["sm_80", bad_element]}}
            )

    @pytest.mark.parametrize("bad_element", _BAD_ELEMENTS, ids=_BAD_ELEMENT_IDS)
    def test_strict(self, bad_element: object) -> None:
        with pytest.raises(
            ValueError, match="'cuda.gpu_architectures' entries must be strings"
        ):
            EnvironmentMatrix.from_dict(
                {"cuda": {"gpu_architectures": ["sm_80", bad_element]}}, strict=True
            )

    def test_all_string_gpu_architectures_still_accepted(self) -> None:
        matrix = EnvironmentMatrix.from_dict(
            {"cuda": {"gpu_architectures": ["sm_80", "sm_90"]}}, strict=True
        )
        assert matrix.cuda.gpu_architectures == ("sm_80", "sm_90")


class TestDeploymentCompilersDictElementEndToEndConfigError:
    def test_deployment_compilers_dict_element_is_hard_config_error(
        self, tmp_path: Path
    ) -> None:
        """`deployment: {compilers: [{name: gcc}]}` in `.abicheck.yml` must be
        a loud exit-64 error at parse time -- not a silently-accepted
        `EnvironmentMatrix` that only blows up much later (`TypeError:
        unhashable type: 'dict'`) the first time something hashes the
        `CompareRequest`/`EnvironmentMatrix` carrying it."""
        from click.testing import CliRunner

        from abicheck.cli import main

        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(
            "deployment:\n  compilers:\n    - name: gcc\n",
            encoding="utf-8",
        )
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        result = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--config", str(cfg)],
        )
        assert result.exit_code == 64, result.output
        assert "compilers" in result.output.lower()


class TestHashableEndToEnd:
    def test_matrix_with_all_string_lists_is_hashable(self) -> None:
        """The positive companion to the dict-element tests above: a real
        `EnvironmentMatrix` built by `from_dict` from well-typed
        `compilers`/`sycl.backends`/`cuda.gpu_architectures` lists must
        actually hash cleanly end to end (the structural-hash guarantee
        the round-7 fix introduced, and this fix must not regress)."""
        matrix = EnvironmentMatrix.from_dict(
            {
                "compilers": ["gcc-13", "clang-17"],
                "sycl": {"backends": ["level_zero", "opencl"]},
                "cuda": {"gpu_architectures": ["sm_80", "sm_90"]},
            },
            strict=True,
        )
        assert hash(matrix) == hash(matrix)
        assert {matrix} == {matrix}

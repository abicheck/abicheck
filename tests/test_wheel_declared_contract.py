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

"""A compared wheel is checked against its own declared contract (G26/G27).

``compare old.whl new.whl`` derives ``runtime_floors`` from the NEW wheel's
platform tag and ``METADATA`` (``extract.wheel_tags.
wheel_declared_runtime_floors``) unless the project declared its own, and the
checks that consume those floors -- the glibc platform baseline and the NumPy
C-API metadata contract -- run over each member.

The oracles here are written from the meaning of the claims, not from the
helpers under test: the glibc oracle compares version tuples, and the NumPy
oracle asks :class:`packaging.specifiers.SpecifierSet` which real NumPy
releases the declaration admits.
"""

from __future__ import annotations

import itertools
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from abicheck.checker import compare
from abicheck.cli import main
from abicheck.environment_matrix import EnvironmentMatrix
from abicheck.extract.wheel_tags import wheel_declared_runtime_floors
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.elf_facts import ElfMetadata
from abicheck.model.python_facts import NumPyCapiSurface
from abicheck.serialization import snapshot_to_json
from abicheck.workflows.release_inputs import wheel_release_env_matrix

UNDERSTATES = ChangeKind.NUMPY_METADATA_UNDERSTATES_REQUIRED_VERSION
MAJOR = ChangeKind.NUMPY_ABI_MAJOR_INCOMPATIBLE
FLOOR = ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED

#: Real NumPy release lines an installer could pick (the oracle's universe).
_NUMPY_RELEASES = [
    Version(v)
    for v in (
        "1.20.0", "1.21.0", "1.22.0", "1.23.0", "1.23.5", "1.24.0", "1.25.0",
        "1.26.0", "1.26.4", "2.0.0", "2.0.2", "2.1.0", "2.2.0", "2.3.0",
    )
]  # fmt: skip


def _snapshot(
    *, extra: bool, glibc: str = "2.2.5", numpy_target: str | None = None
) -> AbiSnapshot:
    funcs = [
        Function(
            name="foo", mangled="foo", return_type="int", visibility=Visibility.PUBLIC
        )
    ]
    if extra:
        funcs.append(
            Function(
                name="bar",
                mangled="bar",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        )
    return AbiSnapshot(
        library="libfoo.so",
        version="1",
        functions=funcs,
        from_headers=True,
        elf=ElfMetadata(
            versions_required={"libc.so.6": ["GLIBC_2.2.5", f"GLIBC_{glibc}"]}
        ),
        numpy_capi=(
            NumPyCapiSurface(
                consumes_array_api=True,
                consumes_ufunc_api=False,
                capi_target_version=numpy_target,
            )
            if numpy_target
            else None
        ),
    )


def _wheel(path: Path, snap: AbiSnapshot, metadata: str | None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("pkg/libfoo.so.json", snapshot_to_json(snap))
        if metadata is not None:
            z.writestr("pkg-1.0.dist-info/METADATA", metadata)
        z.writestr("pkg-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
    return path


def _metadata(requires: str | None) -> str:
    head = "Metadata-Version: 2.1\nName: pkg\nVersion: 1.0\n"
    return head + (f"Requires-Dist: {requires}\n" if requires else "")


# ── the derivation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("manylinux_2_17_x86_64", {"GLIBC": "2.17", "WHEEL_ARCH": "x86_64"}),
        (
            "manylinux_2_17_x86_64.manylinux2014_x86_64",
            {"GLIBC": "2.17", "WHEEL_ARCH": "x86_64"},
        ),
        ("musllinux_1_2_aarch64", {"MUSLLINUX": "1", "WHEEL_ARCH": "aarch64"}),
        (
            "macosx_11_0_arm64",
            {"MACOS_DEPLOYMENT_TARGET": "11.0", "WHEEL_ARCH": "arm64"},
        ),
        ("win_amd64", {}),
    ],
)
def test_floors_follow_the_platform_tag(
    tmp_path: Path, tag: str, expected: dict[str, str]
) -> None:
    wheel = _wheel(
        tmp_path / f"pkg-1.0-cp312-cp312-{tag}.whl",
        _snapshot(extra=False),
        _metadata(None),
    )
    floors = wheel_declared_runtime_floors(wheel)
    assert floors == {"WHEEL_CONTEXT": "1", "NUMPY_REQUIREMENT": "", **expected}


def test_unreadable_metadata_leaves_the_numpy_requirement_unknown(
    tmp_path: Path,
) -> None:
    """No METADATA means "not known", not "declares no numpy": the key is
    absent, so the NumPy check cannot read silence as an empty declaration."""
    wheel = _wheel(
        tmp_path / "pkg-1.0-cp312-cp312-manylinux_2_17_x86_64.whl",
        _snapshot(extra=False),
        None,
    )
    assert "NUMPY_REQUIREMENT" not in wheel_declared_runtime_floors(wheel)


def test_declared_floors_win_and_non_wheels_are_untouched(tmp_path: Path) -> None:
    wheel = _wheel(
        tmp_path / "pkg-1.0-cp312-cp312-manylinux_2_5_x86_64.whl",
        _snapshot(extra=False),
        _metadata("numpy>=1"),
    )
    declared = EnvironmentMatrix(runtime_floors={"GLIBC": "2.39"})
    assert wheel_release_env_matrix(declared, wheel) is declared
    assert wheel_release_env_matrix(None, tmp_path) is None
    derived = wheel_release_env_matrix(EnvironmentMatrix(abi_version="18"), wheel)
    assert derived is not None and derived.abi_version == "18"
    assert derived.runtime_floors["GLIBC"] == "2.5"


# ── the checks, against independent oracles ─────────────────────────────────


_GLIBC_TAG_FLOORS = ["2.5", "2.17", "2.28", "2.34", "2.39"]
_GLIBC_REQUIRED = ["2.17", "2.28", "2.34", "2.38"]


def _vt(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def test_glibc_baseline_matches_the_tuple_oracle() -> None:
    failures = []
    for floor, required in itertools.product(_GLIBC_TAG_FLOORS, _GLIBC_REQUIRED):
        result = compare(
            _snapshot(extra=False),
            _snapshot(extra=True, glibc=required),
            env_matrix=EnvironmentMatrix(
                runtime_floors={"WHEEL_CONTEXT": "1", "GLIBC": floor}
            ),
        )
        fired = any(c.kind is FLOOR for c in result.changes)
        if fired != (_vt(required) > _vt(floor)):
            failures.append((floor, required, fired))
    assert not failures, failures


_DECLARED = ["", ">=1.23", ">=1.26,<2", ">=2.0", ">2.0", ">=1.23,!=1.*", ">=2.1", "<3"]
_TARGETS = ["1.26", "2.0", "2.1"]


def _numpy_oracle(declared: str, target: str) -> tuple[bool, bool]:
    """(understates, major_incompatible) from what the declaration admits.

    *understates*: some admitted NumPy release is older than the binary's
    target. *major*: the target needs NumPy 2 and a 1.x release is admitted.
    """
    allowed = [
        v
        for v in _NUMPY_RELEASES
        if SpecifierSet(declared).contains(v, prereleases=True)
    ]
    t = Version(target)
    t_line = Version(f"{t.major}.{t.minor}")
    understates = any(Version(f"{v.major}.{v.minor}") < t_line for v in allowed)
    major = t.major >= 2 and any(v.major < 2 for v in allowed)
    return understates, major


def test_numpy_contract_matches_the_specifier_oracle() -> None:
    failures = []
    for declared, target in itertools.product(_DECLARED, _TARGETS):
        result = compare(
            _snapshot(extra=False, numpy_target=target),
            _snapshot(extra=True, numpy_target=target),
            env_matrix=EnvironmentMatrix(
                runtime_floors={"WHEEL_CONTEXT": "1", "NUMPY_REQUIREMENT": declared}
            ),
        )
        kinds = {c.kind for c in result.changes}
        got = (UNDERSTATES in kinds, MAJOR in kinds)
        if got != _numpy_oracle(declared, target):
            failures.append((declared, target, got, _numpy_oracle(declared, target)))
    assert not failures, failures


def test_numpy_check_needs_both_the_wheel_gate_and_a_known_requirement() -> None:
    """Negative controls: a non-wheel matrix, or a wheel matrix that does not
    say what the wheel declares, never produce a NumPy metadata finding."""
    for floors in (
        {"NUMPY_REQUIREMENT": ""},
        {"WHEEL_CONTEXT": "1"},
        {"GLIBC": "2.17"},
    ):
        result = compare(
            _snapshot(extra=False, numpy_target="2.0"),
            _snapshot(extra=True, numpy_target="2.0"),
            env_matrix=EnvironmentMatrix(runtime_floors=floors),
        )
        assert not {c.kind for c in result.changes} & {UNDERSTATES, MAJOR}, floors


def test_hand_declared_numpy_requirement_is_validated() -> None:
    with pytest.raises(ValueError, match="NUMPY_REQUIREMENT"):
        EnvironmentMatrix.from_dict(
            {"runtime_floors": {"NUMPY_REQUIREMENT": "not a spec!"}}
        )
    m = EnvironmentMatrix.from_dict(
        {"runtime_floors": {"NUMPY_REQUIREMENT": ">=2.0", "WHEEL_CONTEXT": True}}
    )
    assert m.runtime_floors["NUMPY_REQUIREMENT"] == ">=2.0"


# ── the public workflow: compare old.whl new.whl ────────────────────────────


def _cli_kinds(
    tmp_path: Path, tag: str, requires: str | None, glibc: str
) -> tuple[int, set[str]]:
    import json

    name = f"pkg-1.0-cp312-cp312-{tag}.whl"
    old = _wheel(
        tmp_path / "old" / name,
        _snapshot(extra=False, glibc=glibc, numpy_target="2.0"),
        _metadata(requires),
    )
    new = _wheel(
        tmp_path / "new" / name,
        _snapshot(extra=True, glibc=glibc, numpy_target="2.0"),
        _metadata(requires),
    )
    report = tmp_path / "r.json"
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", f"json={report}"]
    )
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        result.output
    )

    def walk(o: object):
        if isinstance(o, dict):
            if isinstance(o.get("kind"), str):
                yield o["kind"]
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)

    return result.exit_code, set(walk(json.loads(report.read_text())))


def test_cli_wheel_compare_checks_the_new_wheels_own_claims(tmp_path: Path) -> None:
    code, kinds = _cli_kinds(
        tmp_path, "manylinux_2_17_x86_64", "numpy>=1.23", glibc="2.34"
    )
    assert {FLOOR.value, UNDERSTATES.value, MAJOR.value} <= kinds
    assert code == 4


def test_cli_wheel_that_keeps_its_claims_is_clean(tmp_path: Path) -> None:
    code, kinds = _cli_kinds(
        tmp_path, "manylinux_2_39_x86_64", "numpy>=2.0", glibc="2.34"
    )
    assert not kinds & {FLOOR.value, UNDERSTATES.value, MAJOR.value}
    assert code == 0

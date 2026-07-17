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

"""Wheel deployment-claim vs. binary-evidence checks (G27): the macOS
deployment-target floor check (the Mach-O counterpart of G10's manylinux
glibc-floor check) and the wheel-tag architecture-mismatch check (a wheel
tag's claimed single architecture vs. the binary's own ELF e_machine /
Mach-O cpu_type). All tests use synthetic ``ElfMetadata``/``MachoMetadata``/
``AbiSnapshot`` — no real binaries required.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.diff_wheel_deployment import (
    check_macos_deployment_target_floor,
    check_wheel_closure_dependency_violation,
    check_wheel_rpath_not_portable,
    check_wheel_tag_architecture_mismatch,
)
from abicheck.elf_metadata import ElfMetadata
from abicheck.environment_matrix import EnvironmentMatrix
from abicheck.macho_metadata import MachoMetadata
from abicheck.model import AbiSnapshot


def _macho(**kwargs) -> MachoMetadata:
    kwargs.setdefault("install_name", "@rpath/libtest.dylib")
    return MachoMetadata(**kwargs)


def _elf(**kwargs) -> ElfMetadata:
    kwargs.setdefault("soname", "libtest.so.1")
    return ElfMetadata(**kwargs)


def _snap(macho: MachoMetadata, **kwargs) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.dylib", version="1.0", macho=macho, platform="macho", **kwargs
    )


def _elf_snap(elf: ElfMetadata, **kwargs) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1", version="1.0", elf=elf, platform="elf", **kwargs
    )


def _kinds(changes) -> set[ChangeKind]:
    return {c.kind for c in changes}


class TestMacosDeploymentTargetFloorUnit:
    def test_no_declared_floor_no_finding(self) -> None:
        macho = _macho(min_os_version="12.3")
        assert check_macos_deployment_target_floor(macho, None) == []
        assert check_macos_deployment_target_floor(macho, {}) == []

    def test_no_macho_no_finding(self) -> None:
        assert (
            check_macos_deployment_target_floor(
                None, {"MACOS_DEPLOYMENT_TARGET": "10.14"}
            )
            == []
        )

    def test_within_floor_no_finding(self) -> None:
        macho = _macho(min_os_version="10.9")
        assert (
            check_macos_deployment_target_floor(
                macho, {"MACOS_DEPLOYMENT_TARGET": "10.14"}
            )
            == []
        )

    def test_at_floor_exactly_no_finding(self) -> None:
        macho = _macho(min_os_version="10.14")
        assert (
            check_macos_deployment_target_floor(
                macho, {"MACOS_DEPLOYMENT_TARGET": "10.14"}
            )
            == []
        )

    def test_padded_equal_versions_not_flagged(self) -> None:
        # Codex review #583: a bare "11" floor and a "11.0" load-command
        # minimum name the same version — raw tuple comparison would treat
        # (11, 0) as exceeding (11,) and falsely flag this.
        macho = _macho(min_os_version="11.0")
        assert (
            check_macos_deployment_target_floor(
                macho, {"MACOS_DEPLOYMENT_TARGET": "11"}
            )
            == []
        )

    def test_above_floor_flagged(self) -> None:
        macho = _macho(min_os_version="12.3", install_name="@rpath/libopenblas.dylib")
        changes = check_macos_deployment_target_floor(
            macho, {"MACOS_DEPLOYMENT_TARGET": "10.14"}
        )
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED
        assert changes[0].old_value == "10.14"
        assert changes[0].new_value == "12.3"
        assert "libopenblas" in changes[0].description

    def test_malformed_declared_floor_no_finding_not_crash(self) -> None:
        macho = _macho(min_os_version="12.3")
        changes = check_macos_deployment_target_floor(
            macho, {"MACOS_DEPLOYMENT_TARGET": "not-a-version"}
        )
        assert changes == []

    def test_missing_min_os_version_no_finding(self) -> None:
        macho = _macho(min_os_version="")
        assert (
            check_macos_deployment_target_floor(
                macho, {"MACOS_DEPLOYMENT_TARGET": "10.14"}
            )
            == []
        )

    def test_lowercase_key_still_matches(self) -> None:
        macho = _macho(min_os_version="12.3")
        changes = check_macos_deployment_target_floor(
            macho, {"macos_deployment_target": "10.14"}
        )
        assert len(changes) == 1

    def test_unrelated_floor_key_ignored(self) -> None:
        macho = _macho(min_os_version="12.3")
        assert check_macos_deployment_target_floor(macho, {"GLIBC": "2.28"}) == []

    def test_fat_universal_binary_skipped(self) -> None:
        # Codex review #583: min_os_version is only captured for the ONE
        # slice parse_macho_metadata selected for the host running abicheck
        # — a universal binary's arm64 slice commonly has a genuinely higher
        # real minimum than its x86_64 slice, so the single captured value
        # can't be reliably attributed to whichever slice the wheel tag
        # claims. Skip rather than risk a false positive.
        macho = _macho(min_os_version="12.3", cpu_types=["X86_64", "ARM64"])
        assert (
            check_macos_deployment_target_floor(
                macho, {"MACOS_DEPLOYMENT_TARGET": "10.14"}
            )
            == []
        )

    def test_fat_binary_still_checked_when_selected_slice_matches_wheel_arch(
        self,
    ) -> None:
        # Codex review #583, follow-up: when WHEEL_ARCH is also declared and
        # matches the selected slice, min_os_version is no longer a guess —
        # it unambiguously belongs to the exact slice the wheel tag claims,
        # so a real violation must still be flagged rather than skipped.
        macho = _macho(
            cpu_type="X86_64",
            cpu_types=["X86_64", "ARM64"],
            min_os_version="11.0",
        )
        changes = check_macos_deployment_target_floor(
            macho,
            {"MACOS_DEPLOYMENT_TARGET": "10.9", "WHEEL_ARCH": "x86_64"},
        )
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED

    def test_fat_binary_skipped_when_selected_slice_does_not_match_wheel_arch(
        self,
    ) -> None:
        # The selected slice (arm64, host-preferred) does not match the
        # claimed x86_64 arch — its min_os_version can't be attributed to
        # the x86_64 slice the wheel tag actually promises, so still skip.
        macho = _macho(
            cpu_type="ARM64",
            cpu_types=["X86_64", "ARM64"],
            min_os_version="11.0",
        )
        assert (
            check_macos_deployment_target_floor(
                macho,
                {"MACOS_DEPLOYMENT_TARGET": "10.9", "WHEEL_ARCH": "x86_64"},
            )
            == []
        )

    def test_fat_binary_skipped_when_no_wheel_arch_declared(self) -> None:
        macho = _macho(
            cpu_type="X86_64",
            cpu_types=["X86_64", "ARM64"],
            min_os_version="11.0",
        )
        assert (
            check_macos_deployment_target_floor(
                macho, {"MACOS_DEPLOYMENT_TARGET": "10.9"}
            )
            == []
        )

    def test_single_slice_binary_still_checked(self) -> None:
        macho = _macho(min_os_version="12.3", cpu_types=["X86_64"])
        changes = check_macos_deployment_target_floor(
            macho, {"MACOS_DEPLOYMENT_TARGET": "10.14"}
        )
        assert len(changes) == 1

    def test_legacy_snapshot_without_cpu_types_still_checked(self) -> None:
        macho = _macho(min_os_version="12.3", cpu_types=[])
        changes = check_macos_deployment_target_floor(
            macho, {"MACOS_DEPLOYMENT_TARGET": "10.14"}
        )
        assert len(changes) == 1


class TestMacosDeploymentTargetFloorCliEndToEnd:
    """The check reaches exit code / JSON through the real ``compare`` CLI
    via ``--env-matrix``'s existing ``runtime_floors`` mechanism (no
    dedicated flag — same declared-constraint contract G10/G27's GLIBC
    checks already use)."""

    def test_raised_floor_surfaces_as_risk(self) -> None:
        old = _snap(_macho(min_os_version="12.3"))
        new = _snap(_macho(min_os_version="12.3"))
        result = compare(
            old,
            new,
            env_matrix=EnvironmentMatrix(
                runtime_floors={"MACOS_DEPLOYMENT_TARGET": "10.14"}
            ),
        )
        assert ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED in _kinds(result.changes)
        assert result.verdict is Verdict.COMPATIBLE_WITH_RISK

    def test_raised_floor_reaches_compare_via_from_dict(self) -> None:
        # End-to-end through the documented --env-matrix/from_dict path,
        # not just the direct-constructor path the test above uses — the
        # same reachability gap WHEEL_ARCH previously had (Codex review
        # #583) before EnvironmentMatrix._parse_runtime_floors was fixed to
        # accept it at all.
        old = _snap(_macho(min_os_version="12.3"))
        new = _snap(_macho(min_os_version="12.3"))
        matrix = EnvironmentMatrix.from_dict(
            {"runtime_floors": {"MACOS_DEPLOYMENT_TARGET": "10.14"}}
        )
        result = compare(old, new, env_matrix=matrix)
        assert ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED in _kinds(result.changes)

    def test_within_floor_clean(self) -> None:
        old = _snap(_macho(min_os_version="10.9"))
        new = _snap(_macho(min_os_version="10.9"))
        result = compare(
            old,
            new,
            env_matrix=EnvironmentMatrix(
                runtime_floors={"MACOS_DEPLOYMENT_TARGET": "10.14"}
            ),
        )
        assert ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED not in _kinds(result.changes)

    def test_no_env_matrix_no_finding(self) -> None:
        old = _snap(_macho(min_os_version="12.3"))
        new = _snap(_macho(min_os_version="12.3"))
        result = compare(old, new)
        assert ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED not in _kinds(result.changes)


class TestWheelTagArchitectureMismatchUnit:
    def test_no_declared_claim_no_finding(self) -> None:
        elf = _elf(machine="EM_AARCH64")
        assert check_wheel_tag_architecture_mismatch(elf, None, None) == []
        assert check_wheel_tag_architecture_mismatch(elf, None, {}) == []

    def test_matching_elf_machine_no_finding(self) -> None:
        elf = _elf(machine="EM_X86_64")
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "x86_64"}
            )
            == []
        )

    def test_ppc64le_claim_with_big_endian_binary_flagged(self) -> None:
        # Codex review #583: ppc64/ppc64le share one e_machine value
        # (EM_PPC64) — the tag distinction is byte order (EI_DATA). A
        # ppc64le-tagged wheel containing a big-endian ppc64 binary must
        # not pass just because e_machine matches.
        elf = _elf(machine="EM_PPC64", ei_data="MSB", soname="libfoo.so.1")
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "ppc64le"}
        )
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH
        assert "MSB" in changes[0].new_value

    def test_ppc64_claim_with_little_endian_binary_flagged(self) -> None:
        elf = _elf(machine="EM_PPC64", ei_data="LSB")
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "ppc64"}
        )
        assert len(changes) == 1

    def test_ppc64le_claim_with_little_endian_binary_clean(self) -> None:
        elf = _elf(machine="EM_PPC64", ei_data="LSB")
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "ppc64le"}
            )
            == []
        )

    def test_ppc64_claim_with_big_endian_binary_clean(self) -> None:
        elf = _elf(machine="EM_PPC64", ei_data="MSB")
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "ppc64"}
            )
            == []
        )

    def test_ppc64_claim_with_missing_ei_data_degrades_safely(self) -> None:
        # A legacy snapshot without ei_data captured must not false-positive
        # purely from the endianness check having no evidence to compare.
        elf = _elf(machine="EM_PPC64", ei_data="")
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "ppc64le"}
            )
            == []
        )

    def test_x86_64_claim_with_wrong_endianness_flagged(self) -> None:
        # Codex review #583, follow-up: e_machine alone doesn't prove
        # endianness for *any* claim, not just the ppc64 pair — x86_64 is
        # always little-endian, so a captured "MSB" is impossible evidence
        # (a corrupted/misidentified snapshot) and must be flagged.
        elf = _elf(machine="EM_X86_64", ei_data="MSB", soname="libfoo.so.1")
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "x86_64"}
        )
        assert len(changes) == 1
        assert "MSB" in changes[0].new_value

    def test_x86_64_claim_with_correct_endianness_clean(self) -> None:
        elf = _elf(machine="EM_X86_64", ei_data="LSB")
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "x86_64"}
            )
            == []
        )

    def test_s390x_claim_with_correct_big_endian_clean(self) -> None:
        elf = _elf(machine="EM_S390", ei_data="MSB")
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "s390x"}
            )
            == []
        )

    def test_s390x_claim_with_wrong_endianness_flagged(self) -> None:
        elf = _elf(machine="EM_S390", ei_data="LSB")
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "s390x"}
        )
        assert len(changes) == 1

    def test_riscv64_claim_with_matching_binary_clean(self) -> None:
        # Codex review #583: riscv64/loongarch64 are valid manylinux/
        # musllinux single-arch wheel tags too.
        elf = _elf(machine="EM_RISCV", ei_data="LSB")
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "riscv64"}
            )
            == []
        )

    def test_riscv64_claim_with_mismatched_binary_flagged(self) -> None:
        elf = _elf(machine="EM_AARCH64", soname="libfoo.so.1")
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "riscv64"}
        )
        assert len(changes) == 1
        assert changes[0].new_value == "EM_AARCH64"

    def test_loongarch64_claim_with_matching_binary_clean(self) -> None:
        elf = _elf(machine="EM_LOONGARCH", ei_data="LSB")
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "loongarch64"}
            )
            == []
        )

    def test_loongarch64_claim_with_mismatched_binary_flagged(self) -> None:
        elf = _elf(machine="EM_X86_64", soname="libfoo.so.1")
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "loongarch64"}
        )
        assert len(changes) == 1

    def test_riscv64_claim_with_32bit_binary_flagged(self) -> None:
        # Codex review #583, follow-up: RV32/RV64 share one e_machine
        # value (EM_RISCV) — a 32-bit RISC-V ELF must not pass a riscv64
        # claim just because e_machine/EI_DATA matched.
        elf = _elf(
            machine="EM_RISCV", ei_data="LSB", elf_class=32, soname="libfoo.so.1"
        )
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "riscv64"}
        )
        assert len(changes) == 1
        assert "32-bit" in changes[0].new_value

    def test_loongarch64_claim_with_32bit_binary_flagged(self) -> None:
        elf = _elf(machine="EM_LOONGARCH", ei_data="LSB", elf_class=32)
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "loongarch64"}
        )
        assert len(changes) == 1

    def test_s390x_claim_with_32bit_binary_flagged(self) -> None:
        elf = _elf(machine="EM_S390", ei_data="MSB", elf_class=32)
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "s390x"}
        )
        assert len(changes) == 1

    def test_x86_64_claim_with_x32_abi_flagged(self) -> None:
        # Codex review #583, follow-up: the x86-64 x32 ABI uses EM_X86_64
        # with ELFCLASS32 (ILP32 pointers on a 64-bit CPU) — a distinct,
        # non-interchangeable ABI a plain x86_64 wheel claim must reject.
        elf = _elf(
            machine="EM_X86_64", ei_data="LSB", elf_class=32, soname="libfoo.so.1"
        )
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "x86_64"}
        )
        assert len(changes) == 1
        assert "32-bit" in changes[0].new_value

    def test_x86_64_claim_with_64bit_binary_clean(self) -> None:
        elf = _elf(machine="EM_X86_64", ei_data="LSB", elf_class=64)
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "x86_64"}
            )
            == []
        )

    def test_aarch64_claim_with_ilp32_abi_flagged(self) -> None:
        # AArch64's (now-deprecated) ILP32 ABI uses EM_AARCH64 with
        # ELFCLASS32 the same way x32 does for x86_64.
        elf = _elf(machine="EM_AARCH64", ei_data="LSB", elf_class=32)
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "aarch64"}
        )
        assert len(changes) == 1

    def test_i686_claim_with_unset_elf_class_not_flagged(self) -> None:
        # Codex review #583, follow-up: ElfMetadata.elf_class defaults to
        # 64, so checking it for i686/armv7l claims (both 32-bit only) would
        # false-positive an otherwise-matching binary from a legacy/partial
        # snapshot that captured `machine` but never set `elf_class`.
        # e_machine (EM_386) already unambiguously proves 32-bit, so
        # _ARCH_CLAIM_TO_ELF_CLASS deliberately excludes i686/armv7l.
        elf = _elf(machine="EM_386", ei_data="LSB")
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "i686"}
            )
            == []
        )

    def test_armv7l_claim_with_unset_elf_class_and_hard_float_not_flagged(
        self,
    ) -> None:
        elf = _elf(
            machine="EM_ARM",
            ei_data="LSB",
            abi_flags=frozenset({"float-hard", "eabi5"}),
        )
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "armv7l"}
            )
            == []
        )

    def test_armv7l_claim_with_hard_float_clean(self) -> None:
        elf = _elf(
            machine="EM_ARM",
            ei_data="LSB",
            elf_class=32,
            abi_flags=frozenset({"float-hard", "eabi5"}),
        )
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "armv7l"}
            )
            == []
        )

    def test_armv7l_claim_with_soft_float_flagged(self) -> None:
        # Codex review #583: manylinux's armv7l tag specifically means the
        # hard-float ARM EABI — a soft-float binary shares the same
        # e_machine/EI_DATA but cannot satisfy the tag's runtime
        # expectations.
        elf = _elf(
            machine="EM_ARM",
            ei_data="LSB",
            elf_class=32,
            abi_flags=frozenset({"float-soft", "eabi5"}),
            soname="libfoo.so.1",
        )
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "armv7l"}
        )
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH
        assert "soft-float" in changes[0].new_value

    def test_armv7l_claim_with_older_eabi_flagged(self) -> None:
        # Codex review #583, follow-up: a hard-float binary built against
        # an older EABI (e.g. eabi4) still doesn't satisfy manylinux's
        # armhf contract, which requires EABI version 5 specifically.
        elf = _elf(
            machine="EM_ARM",
            ei_data="LSB",
            elf_class=32,
            abi_flags=frozenset({"float-hard", "eabi4"}),
            soname="libfoo.so.1",
        )
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "armv7l"}
        )
        assert len(changes) == 1
        assert "eabi4" in changes[0].new_value

    def test_armv7l_claim_with_eabi5_but_no_hard_float_marker_flagged(self) -> None:
        # Codex review #583, follow-up: manylinux's armhf contract requires
        # the explicit EF_ARM_ABI_FLOAT_HARD e_flags bit, not merely EABI5
        # — _decode_abi_flags always evaluates both float bits for EM_ARM,
        # so a non-empty abi_flags set naming eabi5 but no "float-hard"
        # token means the real e_flags genuinely lacks that bit.
        elf = _elf(
            machine="EM_ARM",
            ei_data="LSB",
            elf_class=32,
            abi_flags=frozenset({"eabi5"}),
            soname="libfoo.so.1",
        )
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "armv7l"}
        )
        assert len(changes) == 1
        assert "hard-float" in changes[0].new_value

    def test_armv7l_claim_with_hard_float_but_no_eabi_token_flagged(self) -> None:
        # Codex review #583, follow-up: _decode_abi_flags always evaluates
        # the EABI-version field for EM_ARM too — a missing "eabiN" token
        # means the real e_flags EABI-version field is exactly 0 (GNU/bare
        # EABI), definitively not 5, even though "float-hard" is present.
        elf = _elf(
            machine="EM_ARM",
            ei_data="LSB",
            elf_class=32,
            abi_flags=frozenset({"float-hard"}),
            soname="libfoo.so.1",
        )
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "armv7l"}
        )
        assert len(changes) == 1
        assert "eabi0" in changes[0].new_value

    def test_armv7l_claim_with_no_abi_flags_degrades_safely(self) -> None:
        # A legacy/undecoded snapshot without abi_flags captured must not
        # false-positive purely from having no evidence to compare.
        elf = _elf(machine="EM_ARM", ei_data="LSB", elf_class=32, abi_flags=frozenset())
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "armv7l"}
            )
            == []
        )

    def test_mismatched_elf_machine_flagged(self) -> None:
        elf = _elf(machine="EM_AARCH64", soname="libtest.so.1")
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "x86_64"}
        )
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH
        assert changes[0].old_value == "x86_64"
        assert changes[0].new_value == "EM_AARCH64"

    def test_matching_macho_cpu_type_no_finding(self) -> None:
        macho = _macho(cpu_type="ARM64")
        assert (
            check_wheel_tag_architecture_mismatch(
                None, macho, {"WHEEL_ARCH": "arm64"}
            )
            == []
        )

    def test_arm64e_does_not_satisfy_arm64_claim(self) -> None:
        # Codex review #583: ARM64E (pointer-authenticating arm64e ABI
        # variant) is a distinct, non-interchangeable ABI — macho_metadata.py
        # keeps it a separate cpu_type label for exactly this reason, and
        # third-party wheels are never actually built for it, so it must not
        # silently satisfy a plain arm64 claim.
        macho = _macho(cpu_type="ARM64E", install_name="@rpath/libfoo.dylib")
        changes = check_wheel_tag_architecture_mismatch(
            None, macho, {"WHEEL_ARCH": "arm64"}
        )
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH
        assert "ARM64E" in changes[0].new_value

    def test_mismatched_macho_cpu_type_flagged(self) -> None:
        macho = _macho(cpu_type="ARM64", install_name="@rpath/libopenblas.dylib")
        changes = check_wheel_tag_architecture_mismatch(
            None, macho, {"WHEEL_ARCH": "x86_64"}
        )
        assert len(changes) == 1
        assert changes[0].new_value == "ARM64"
        assert "libopenblas" in changes[0].description

    def test_unrecognized_claim_no_finding(self) -> None:
        elf = _elf(machine="EM_AARCH64")
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, None, {"WHEEL_ARCH": "win_amd64"}
            )
            == []
        )

    def test_no_elf_or_macho_evidence_no_finding(self) -> None:
        assert (
            check_wheel_tag_architecture_mismatch(
                None, None, {"WHEEL_ARCH": "x86_64"}
            )
            == []
        )

    def test_elf_present_with_empty_machine_falls_through_to_macho(
        self,
    ) -> None:
        # An ElfMetadata with no captured machine (falsy) must not shadow
        # Mach-O evidence when both are somehow present on the same call —
        # the ELF branch should fall through rather than returning early.
        elf = _elf(machine="")
        macho = _macho(cpu_type="ARM64")
        assert (
            check_wheel_tag_architecture_mismatch(
                elf, macho, {"WHEEL_ARCH": "arm64"}
            )
            == []
        )

    def test_unrecognized_claim_with_macho_evidence_no_finding(self) -> None:
        macho = _macho(cpu_type="ARM64", cpu_types=["ARM64"])
        assert (
            check_wheel_tag_architecture_mismatch(
                None, macho, {"WHEEL_ARCH": "win_amd64"}
            )
            == []
        )

    def test_lowercase_and_uppercase_claim_key_match(self) -> None:
        elf = _elf(machine="EM_AARCH64")
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"wheel_arch": "X86_64"}
        )
        assert len(changes) == 1

    def test_fat_macho_claimed_slice_present_not_flagged(self) -> None:
        # Codex review #583: cpu_type is only the ONE slice
        # parse_macho_metadata selected for the host running abicheck
        # (arm64 preferred on Apple Silicon) — a single-arch x86_64 wheel
        # tag whose binary still carries both slices must not false-positive
        # just because an Apple Silicon host happened to parse it and select
        # the arm64 slice.
        macho = _macho(cpu_type="ARM64", cpu_types=["X86_64", "ARM64"])
        assert (
            check_wheel_tag_architecture_mismatch(
                None, macho, {"WHEEL_ARCH": "x86_64"}
            )
            == []
        )

    def test_fat_macho_claimed_slice_absent_still_flagged(self) -> None:
        macho = _macho(
            cpu_type="ARM64",
            cpu_types=["ARM64"],
            install_name="@rpath/libfoo.dylib",
        )
        changes = check_wheel_tag_architecture_mismatch(
            None, macho, {"WHEEL_ARCH": "x86_64"}
        )
        assert len(changes) == 1
        assert "ARM64" in changes[0].new_value

    def test_legacy_snapshot_without_cpu_types_falls_back_to_cpu_type(
        self,
    ) -> None:
        # A snapshot predating the cpu_types field deserializes with
        # cpu_types=[] — must still compare against the single cpu_type
        # rather than treating an empty slice list as "no evidence."
        macho = _macho(cpu_type="ARM64", cpu_types=[])
        changes = check_wheel_tag_architecture_mismatch(
            None, macho, {"WHEEL_ARCH": "x86_64"}
        )
        assert len(changes) == 1

    def test_empty_cpu_type_with_populated_cpu_types_still_checked(
        self,
    ) -> None:
        # Codex review #583: cpu_types (all slices) is the primary evidence;
        # gating the whole check on `cpu_type` truthiness first bypassed a
        # snapshot with cpu_type="" but cpu_types populated — a mismatched
        # claim would silently pass with no finding at all.
        macho = _macho(
            cpu_type="", cpu_types=["ARM64"], install_name="@rpath/libfoo.dylib"
        )
        changes = check_wheel_tag_architecture_mismatch(
            None, macho, {"WHEEL_ARCH": "x86_64"}
        )
        assert len(changes) == 1
        assert "ARM64" in changes[0].new_value

    def test_empty_cpu_type_and_empty_cpu_types_no_finding(self) -> None:
        macho = _macho(cpu_type="", cpu_types=[])
        assert (
            check_wheel_tag_architecture_mismatch(
                None, macho, {"WHEEL_ARCH": "x86_64"}
            )
            == []
        )


class TestWheelTagArchitectureMismatchCliEndToEnd:
    def test_elf_mismatch_surfaces_as_breaking(self) -> None:
        old = _elf_snap(_elf(machine="EM_AARCH64"))
        new = _elf_snap(_elf(machine="EM_AARCH64"))
        result = compare(
            old, new, env_matrix=EnvironmentMatrix(runtime_floors={"WHEEL_ARCH": "x86_64"})
        )
        assert ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH in _kinds(result.changes)
        assert result.verdict is Verdict.BREAKING

    def test_macho_mismatch_surfaces_as_breaking(self) -> None:
        old = _snap(_macho(cpu_type="ARM64"))
        new = _snap(_macho(cpu_type="ARM64"))
        result = compare(
            old, new, env_matrix=EnvironmentMatrix(runtime_floors={"WHEEL_ARCH": "x86_64"})
        )
        assert ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH in _kinds(result.changes)
        assert result.verdict is Verdict.BREAKING

    def test_matching_arch_clean(self) -> None:
        old = _elf_snap(_elf(machine="EM_X86_64"))
        new = _elf_snap(_elf(machine="EM_X86_64"))
        result = compare(
            old, new, env_matrix=EnvironmentMatrix(runtime_floors={"WHEEL_ARCH": "x86_64"})
        )
        assert ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH not in _kinds(result.changes)


class TestWheelRpathNotPortableUnit:
    def test_no_wheel_context_no_finding(self) -> None:
        elf = _elf(rpath="/usr/local/lib")
        assert check_wheel_rpath_not_portable(elf, None) == []
        assert check_wheel_rpath_not_portable(elf, {}) == []

    def test_declared_floor_without_wheel_context_not_flagged(self) -> None:
        # Codex review #583: GLIBC/GLIBCXX/CXXABI are a general-purpose
        # ADR-020b symbol-version-floor mechanism, unrelated to wheel
        # packaging — an ordinary non-wheel DSO declaring one must not get
        # a wheel-portability finding it never opted into.
        elf = _elf(rpath="/usr/local/lib")
        assert check_wheel_rpath_not_portable(elf, {"GLIBC": "2.28"}) == []

    def test_no_rpath_no_finding(self) -> None:
        elf = _elf(rpath="", runpath="")
        assert (
            check_wheel_rpath_not_portable(elf, {"WHEEL_CONTEXT": "1"}) == []
        )

    def test_origin_relative_rpath_clean(self) -> None:
        elf = _elf(rpath="$ORIGIN/../foo.libs")
        assert (
            check_wheel_rpath_not_portable(elf, {"WHEEL_CONTEXT": "1"}) == []
        )

    def test_origin_relative_runpath_clean(self) -> None:
        elf = _elf(runpath="$ORIGIN/../foo.libs")
        assert (
            check_wheel_rpath_not_portable(elf, {"WHEEL_CONTEXT": "1"}) == []
        )

    def test_runpath_present_ignores_stale_absolute_rpath(self) -> None:
        # Codex review #583, follow-up: DT_RPATH is only consulted by the
        # dynamic loader when the same object carries no DT_RUNPATH at all
        # (see resolver.py's own loader-accurate precedence modeling) — a
        # stale absolute DT_RPATH alongside a portable DT_RUNPATH is never
        # actually used, so it must not be flagged.
        elf = _elf(rpath="/build/sysroot/lib", runpath="$ORIGIN/../foo.libs")
        assert (
            check_wheel_rpath_not_portable(elf, {"WHEEL_CONTEXT": "1"}) == []
        )

    def test_runpath_present_flags_its_own_absolute_entry_not_rpath(
        self,
    ) -> None:
        elf = _elf(
            rpath="$ORIGIN/../ignored.libs",
            runpath="/build/sysroot/lib",
            soname="libfoo.so.1",
        )
        changes = check_wheel_rpath_not_portable(elf, {"WHEEL_CONTEXT": "1"})
        assert len(changes) == 1
        assert changes[0].new_value == "/build/sysroot/lib"

    def test_absolute_rpath_flagged(self) -> None:
        elf = _elf(rpath="/usr/local/lib", soname="libfoo.so.1")
        changes = check_wheel_rpath_not_portable(elf, {"WHEEL_CONTEXT": "1"})
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.WHEEL_RPATH_NOT_PORTABLE
        assert changes[0].new_value == "/usr/local/lib"

    def test_mixed_absolute_and_origin_relative_flags_only_absolute(self) -> None:
        elf = _elf(rpath="/usr/local/lib:$ORIGIN/../foo.libs")
        changes = check_wheel_rpath_not_portable(elf, {"WHEEL_CONTEXT": "1"})
        assert len(changes) == 1
        assert changes[0].new_value == "/usr/local/lib"

    def test_origin_prefixed_but_not_token_bounded_flagged(self) -> None:
        # Codex review #583: ld.so substitutes $ORIGIN as a raw substring
        # wherever it occurs, so "$ORIGIN_BACKUP" expands to an unrelated
        # sibling path (<origin-dir>_BACKUP), not a subdirectory of the
        # binary's own directory — a startswith("$ORIGIN") check wrongly
        # treated it as the portable token.
        elf = _elf(rpath="$ORIGIN_BACKUP", soname="libfoo.so.1")
        changes = check_wheel_rpath_not_portable(elf, {"WHEEL_CONTEXT": "1"})
        assert len(changes) == 1
        assert changes[0].new_value == "$ORIGIN_BACKUP"

    def test_empty_rpath_component_flagged_as_cwd(self) -> None:
        # Codex review #583: an empty RPATH/RUNPATH component (e.g. a
        # doubled ":") means "current working directory" to the dynamic
        # loader — a non-portable (and unsafe) entry, not a no-op to
        # silently drop.
        elf = _elf(rpath="$ORIGIN/../foo.libs::/usr/lib", soname="libfoo.so.1")
        changes = check_wheel_rpath_not_portable(elf, {"WHEEL_CONTEXT": "1"})
        assert len(changes) == 1
        assert "current working directory" in changes[0].new_value
        assert "/usr/lib" in changes[0].new_value

    def test_no_elf_no_finding(self) -> None:
        assert (
            check_wheel_rpath_not_portable(None, {"WHEEL_CONTEXT": "1"}) == []
        )

    def test_wheel_context_combined_with_other_floors_still_flags(self) -> None:
        elf = _elf(rpath="/usr/local/lib")
        changes = check_wheel_rpath_not_portable(
            elf, {"GLIBC": "2.28", "WHEEL_CONTEXT": "1"}
        )
        assert len(changes) == 1


class TestWheelClosureDependencyViolationUnit:
    def test_no_wheel_context_no_finding(self) -> None:
        elf = _elf(needed=["libopenblas-a1b2c3d4.so.0"])
        assert check_wheel_closure_dependency_violation(elf, None) == []
        assert check_wheel_closure_dependency_violation(elf, {}) == []

    def test_declared_floor_without_wheel_context_not_flagged(self) -> None:
        elf = _elf(needed=["libopenblas-a1b2c3d4.so.0"])
        assert (
            check_wheel_closure_dependency_violation(elf, {"GLIBC": "2.28"}) == []
        )
        assert (
            check_wheel_closure_dependency_violation(elf, {"MUSLLINUX": "1.2"})
            == []
        )

    def test_no_vendored_dependency_no_finding(self) -> None:
        elf = _elf(needed=["libc.so.6"])
        assert (
            check_wheel_closure_dependency_violation(
                elf, {"WHEEL_CONTEXT": "1"}
            )
            == []
        )

    def test_vendored_dependency_with_origin_rpath_clean(self) -> None:
        elf = _elf(
            needed=["libopenblas-a1b2c3d4.so.0"], rpath="$ORIGIN/../foo.libs"
        )
        assert (
            check_wheel_closure_dependency_violation(
                elf, {"WHEEL_CONTEXT": "1"}
            )
            == []
        )

    def test_vendored_dependency_with_stale_origin_rpath_still_flagged(
        self,
    ) -> None:
        # Codex review #583, follow-up: DT_RPATH is entirely ignored by the
        # loader once the same object also carries DT_RUNPATH — a stale
        # $ORIGIN-relative DT_RPATH alongside an absolute DT_RUNPATH must
        # not mask a genuine closure-violation finding, since the loader
        # never actually consults that DT_RPATH entry.
        elf = _elf(
            needed=["libopenblas-a1b2c3d4.so.0"],
            rpath="$ORIGIN/../ignored.libs",
            runpath="/build/sysroot/lib",
            soname="libfoo.so.1",
        )
        changes = check_wheel_closure_dependency_violation(
            elf, {"WHEEL_CONTEXT": "1"}
        )
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.WHEEL_CLOSURE_DEPENDENCY_VIOLATION

    def test_vendored_dependency_without_origin_rpath_flagged(self) -> None:
        elf = _elf(
            needed=["libopenblas-a1b2c3d4.so.0"], rpath="", soname="libfoo.so.1"
        )
        changes = check_wheel_closure_dependency_violation(
            elf, {"WHEEL_CONTEXT": "1"}
        )
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.WHEEL_CLOSURE_DEPENDENCY_VIOLATION
        assert changes[0].new_value == "libopenblas-a1b2c3d4.so.0"

    def test_vendored_dependency_with_absolute_rpath_only_flagged(self) -> None:
        # An absolute (non-$ORIGIN) rpath doesn't count as a bundling
        # mechanism for this check.
        elf = _elf(needed=["libopenblas-a1b2c3d4.so.0"], rpath="/usr/local/lib")
        changes = check_wheel_closure_dependency_violation(
            elf, {"WHEEL_CONTEXT": "1"}
        )
        assert len(changes) == 1

    def test_no_elf_no_finding(self) -> None:
        assert (
            check_wheel_closure_dependency_violation(
                None, {"WHEEL_CONTEXT": "1"}
            )
            == []
        )


class TestWheelRpathAndClosureCliEndToEnd:
    def test_absolute_rpath_surfaces_as_risk(self) -> None:
        old = _elf_snap(_elf(rpath="/usr/local/lib"))
        new = _elf_snap(_elf(rpath="/usr/local/lib"))
        result = compare(
            old,
            new,
            env_matrix=EnvironmentMatrix(
                runtime_floors={"WHEEL_CONTEXT": "1"}
            ),
        )
        assert ChangeKind.WHEEL_RPATH_NOT_PORTABLE in _kinds(result.changes)
        assert result.verdict is Verdict.COMPATIBLE_WITH_RISK

    def test_unresolvable_vendored_dependency_surfaces_as_breaking(self) -> None:
        old = _elf_snap(_elf(needed=["libopenblas-a1b2c3d4.so.0"]))
        new = _elf_snap(_elf(needed=["libopenblas-a1b2c3d4.so.0"]))
        result = compare(
            old,
            new,
            env_matrix=EnvironmentMatrix(
                runtime_floors={"WHEEL_CONTEXT": "1"}
            ),
        )
        assert ChangeKind.WHEEL_CLOSURE_DEPENDENCY_VIOLATION in _kinds(
            result.changes
        )
        assert result.verdict is Verdict.BREAKING

    def test_glibc_alone_does_not_trigger_wheel_checks(self) -> None:
        # Codex review #583: the exact scenario the finding raised — an
        # ordinary non-wheel DSO declaring a GLIBC floor for unrelated
        # deployment-floor reasons must not suddenly get wheel-portability/
        # closure findings.
        old = _elf_snap(
            _elf(rpath="/usr/local/lib", needed=["libopenblas-a1b2c3d4.so.0"])
        )
        new = _elf_snap(
            _elf(rpath="/usr/local/lib", needed=["libopenblas-a1b2c3d4.so.0"])
        )
        result = compare(
            old, new, env_matrix=EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        )
        assert ChangeKind.WHEEL_RPATH_NOT_PORTABLE not in _kinds(result.changes)
        assert ChangeKind.WHEEL_CLOSURE_DEPENDENCY_VIOLATION not in _kinds(
            result.changes
        )

    def test_no_env_matrix_no_finding(self) -> None:
        old = _elf_snap(_elf(rpath="/usr/local/lib"))
        new = _elf_snap(_elf(rpath="/usr/local/lib"))
        result = compare(old, new)
        assert ChangeKind.WHEEL_RPATH_NOT_PORTABLE not in _kinds(result.changes)
        assert ChangeKind.WHEEL_CLOSURE_DEPENDENCY_VIOLATION not in _kinds(
            result.changes
        )

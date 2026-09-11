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

"""Codex review, PR #1221, Finding 1: a ``WHEEL_ARCH`` claim that is a real,
config-accepted token (:data:`abicheck.model.wheel_arch_claims.
WHEEL_ARCH_CLAIMS`) but belongs to the *other* binary format -- an ELF-only
token (e.g. ``aarch64``) compared against a Mach-O artifact, or a Mach-O-only
token (e.g. ``arm64``) compared against an ELF artifact -- used to make
``_elf_arch_mismatch``/``_macho_arch_mismatch`` silently return ``[]``
unconditionally for that token, disabling the hard architecture-mismatch
gate no matter how obviously the artifact's own recorded machine/cpu_type
disagreed. Split into its own file (rather than growing
``test_diff_wheel_deployment.py`` further) since that file already sits at
the architecture gate's file-size ceiling.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind
from abicheck.diff_wheel_deployment import check_wheel_tag_architecture_mismatch
from abicheck.elf_metadata import ElfMetadata
from abicheck.macho_metadata import MachoMetadata


def _elf(**kwargs) -> ElfMetadata:
    kwargs.setdefault("soname", "libtest.so.1")
    return ElfMetadata(**kwargs)


def _macho(**kwargs) -> MachoMetadata:
    kwargs.setdefault("install_name", "@rpath/libtest.dylib")
    return MachoMetadata(**kwargs)


class TestCrossFormatWheelArchClaims:
    def test_macho_only_claim_against_elf_binary_flagged(self) -> None:
        # `arm64` is config-accepted but Mach-O only -- no
        # `_ARCH_CLAIM_TO_ELF_MACHINE` entry. Before this fix,
        # `_elf_arch_mismatch` returned `[]` unconditionally for it, even
        # for a genuinely x86_64 ELF binary.
        elf = _elf(machine="EM_X86_64", ei_data="LSB", soname="libfoo.so.1")
        changes = check_wheel_tag_architecture_mismatch(
            elf, None, {"WHEEL_ARCH": "arm64"}
        )
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH
        assert changes[0].old_value == "arm64"
        assert changes[0].new_value == "EM_X86_64"

    def test_elf_only_claim_against_macho_binary_flagged(self) -> None:
        # Symmetric case: `aarch64` is config-accepted but ELF-only.
        macho = _macho(cpu_type="ARM64", cpu_types=["ARM64"])
        changes = check_wheel_tag_architecture_mismatch(
            None, macho, {"WHEEL_ARCH": "aarch64"}
        )
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH
        assert changes[0].old_value == "aarch64"
        assert "ARM64" in changes[0].new_value

    def test_format_own_claim_still_matches_after_cross_format_fix(self) -> None:
        # A claim genuinely supported by the artifact's own format must
        # still detect match/mismatch correctly (regression guard against
        # the fix above over-broadening).
        matching_elf = _elf(machine="EM_X86_64", ei_data="LSB")
        assert (
            check_wheel_tag_architecture_mismatch(
                matching_elf, None, {"WHEEL_ARCH": "x86_64"}
            )
            == []
        )
        mismatched_elf = _elf(machine="EM_AARCH64", soname="libfoo.so.1")
        assert (
            len(
                check_wheel_tag_architecture_mismatch(
                    mismatched_elf, None, {"WHEEL_ARCH": "x86_64"}
                )
            )
            == 1
        )
        matching_macho = _macho(cpu_type="ARM64", cpu_types=["ARM64"])
        assert (
            check_wheel_tag_architecture_mismatch(
                None, matching_macho, {"WHEEL_ARCH": "arm64"}
            )
            == []
        )
        mismatched_macho = _macho(cpu_type="X86_64", cpu_types=["X86_64"])
        assert (
            len(
                check_wheel_tag_architecture_mismatch(
                    None, mismatched_macho, {"WHEEL_ARCH": "arm64"}
                )
            )
            == 1
        )

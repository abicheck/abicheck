"""Operand refusals: one owner decides, each frontend words its own refusal.

The rules live in :mod:`abicheck.workflows.operand_rules`. The ``compare`` CLI
renders them through ``cli_compare_helpers._operand_rule_errors`` and must keep
its exact wording and error class; the typed API keeps the ``debug_format=``
wording. These tests pin both, so moving a rule cannot silently re-word a flag
message or swap a usage error for a bad-parameter one.
"""

from __future__ import annotations

import click
import pytest

from abicheck.cli_compare_helpers import _operand_rule_errors
from abicheck.errors import ValidationError
from abicheck.workflows.operand_rules import (
    DebugFormatNotElfError,
    ManifestOperandError,
    reject_debug_format_for_binaries,
    reject_debug_format_for_non_elf,
    reject_manifest_header_conflicts,
    reject_manifest_non_elf,
)


class TestDebugFormatRule:
    @pytest.mark.parametrize("binary_fmt", ["pe", "macho"])
    def test_refuses_a_non_elf_side_and_names_it(self, binary_fmt: str) -> None:
        with pytest.raises(DebugFormatNotElfError) as info:
            reject_debug_format_for_non_elf("dwarf", "elf", binary_fmt)
        assert info.value.side == "new"
        assert info.value.binary_fmt == binary_fmt
        assert info.value.debug_format == "dwarf"
        assert isinstance(info.value, ValidationError)

    def test_names_the_old_side_first_when_both_are_refused(self) -> None:
        with pytest.raises(DebugFormatNotElfError) as info:
            reject_debug_format_for_non_elf("btf", "pe", "macho")
        assert info.value.side == "old"

    @pytest.mark.parametrize(
        ("debug_format", "fmt"),
        [(None, "pe"), ("dwarf", "elf"), ("dwarf", None)],
    )
    def test_accepts_an_unforced_format_an_elf_side_or_a_snapshot(
        self, debug_format: str | None, fmt: str | None
    ) -> None:
        reject_debug_format_for_non_elf(debug_format, fmt, fmt)

    def test_typed_api_wording_is_unchanged(self) -> None:
        with pytest.raises(ValidationError) as info:
            reject_debug_format_for_binaries("dwarf", (("input", "macho"),))
        assert str(info.value) == (
            "debug_format='dwarf' is only supported for ELF binaries, "
            "but the input input is MACHO."
        )

    def test_compare_cli_wording_names_the_config_key(self) -> None:
        with pytest.raises(click.BadParameter) as info:
            with _operand_rule_errors():
                reject_debug_format_for_non_elf("dwarf", "elf", "pe")
        assert info.value.message == (
            "debug.format: dwarf (.abicheck.yml) is only supported for ELF "
            "binaries, but the new input is PE."
        )


class TestManifestRules:
    def test_header_conflict_is_a_usage_error_with_the_flag_wording(self) -> None:
        with pytest.raises(click.UsageError) as info:
            with _operand_rule_errors():
                reject_manifest_header_conflicts(object(), None, ("a.h",), ())
        assert info.value.message == (
            "--dump-manifest old=... and a header for the old side "
            "(-H/--header) are mutually exclusive -- declare the old side's "
            "public surface in the manifest's own base profile instead."
        )

    def test_header_conflict_checks_each_side_on_its_own_headers(self) -> None:
        reject_manifest_header_conflicts(object(), None, (), ("b.h",))
        with pytest.raises(ManifestOperandError) as info:
            reject_manifest_header_conflicts(None, object(), (), ("b.h",))
        assert (info.value.rule, info.value.side) == ("header_conflict", "new")

    def test_non_elf_names_the_format_it_found(self) -> None:
        with pytest.raises(click.UsageError) as info:
            with _operand_rule_errors():
                reject_manifest_non_elf(object(), None, "pe", "elf")
        assert info.value.message == (
            "--dump-manifest old=... requires the old input to be an ELF "
            "binary; got pe."
        )

    def test_non_elf_says_non_binary_when_no_format_was_detected(self) -> None:
        with pytest.raises(ManifestOperandError) as info:
            reject_manifest_non_elf(None, object(), "elf", None)
        assert info.value.binary_fmt is None
        assert str(info.value) == (
            "--dump-manifest new=... requires the new input to be an ELF "
            "binary; got a non-binary input."
        )

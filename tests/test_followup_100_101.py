"""Follow-up tests for PR #101 (--policy CLI).

Covers:
- _normalize_arch
- policy-aware compute_verdict: sdk_vendor, plugin_abi
- CLI/report filtering honoring --policy
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from abicheck.checker import DiffResult
from abicheck.checker_policy import (
    PLUGIN_ABI_DOWNGRADED_KINDS,
    SDK_VENDOR_COMPAT_KINDS,
    SDK_VENDOR_DOWNGRADED_KINDS,
    ChangeKind,
    Verdict,
    compute_verdict,
)
from abicheck.dwarf_advanced import _normalize_arch
from abicheck.model import AbiSnapshot

# ── helpers ──────────────────────────────────────────────────────────────────


def _change(kind: ChangeKind) -> Any:
    c = MagicMock()
    c.kind = kind
    return c


# ── _normalize_arch ───────────────────────────────────────────────────────────


class TestNormalizeArch:
    def test_x64(self) -> None:
        elf = MagicMock()
        elf.get_machine_arch.return_value = "x64"
        assert _normalize_arch(elf) == "x64"

    def test_aarch64(self) -> None:
        elf = MagicMock()
        elf.get_machine_arch.return_value = "AArch64"
        assert _normalize_arch(elf) == "aarch64"

    def test_unknown_passthrough(self) -> None:
        elf = MagicMock()
        elf.get_machine_arch.return_value = "riscv"
        assert _normalize_arch(elf) == "riscv"


# ── _extract_cfa_reg_from_fde ─────────────────────────────────────────────────


# ── compute_verdict — sdk_vendor ──────────────────────────────────────────────


class TestSdkVendorVerdict:
    """sdk_vendor downgrades source-level API_BREAK kinds to COMPATIBLE."""

    def test_alias_kept_for_backward_compat(self) -> None:
        assert SDK_VENDOR_DOWNGRADED_KINDS == SDK_VENDOR_COMPAT_KINDS

    def test_enum_member_renamed_is_compatible(self) -> None:
        assert (
            compute_verdict(
                [_change(ChangeKind.ENUM_MEMBER_RENAMED)], policy="sdk_vendor"
            )
            == Verdict.COMPATIBLE
        )

    def test_field_renamed_is_compatible(self) -> None:
        assert (
            compute_verdict([_change(ChangeKind.FIELD_RENAMED)], policy="sdk_vendor")
            == Verdict.COMPATIBLE
        )

    def test_source_level_kind_changed_is_compatible(self) -> None:
        assert (
            compute_verdict(
                [_change(ChangeKind.SOURCE_LEVEL_KIND_CHANGED)], policy="sdk_vendor"
            )
            == Verdict.COMPATIBLE
        )

    def test_default_value_changed_strict_is_compatible(self) -> None:
        assert (
            compute_verdict(
                [_change(ChangeKind.PARAM_DEFAULT_VALUE_CHANGED)], policy="strict_abi"
            )
            == Verdict.COMPATIBLE
        )

    def test_func_removed_still_breaking(self) -> None:
        assert (
            compute_verdict([_change(ChangeKind.FUNC_REMOVED)], policy="sdk_vendor")
            == Verdict.BREAKING
        )

    def test_strict_abi_enum_rename_is_api_break(self) -> None:
        assert (
            compute_verdict(
                [_change(ChangeKind.ENUM_MEMBER_RENAMED)], policy="strict_abi"
            )
            == Verdict.API_BREAK
        )

    def test_all_sdk_compat_kinds_produce_compatible(self) -> None:
        for kind in SDK_VENDOR_COMPAT_KINDS:
            result = compute_verdict([_change(kind)], policy="sdk_vendor")
            assert result == Verdict.COMPATIBLE, (
                f"{kind} with sdk_vendor → {result!r}, expected COMPATIBLE"
            )


# ── compute_verdict — plugin_abi ──────────────────────────────────────────────


class TestPluginAbiVerdict:
    """plugin_abi downgrades calling-convention kinds to COMPATIBLE."""

    def test_calling_convention_changed_is_compatible(self) -> None:
        assert (
            compute_verdict(
                [_change(ChangeKind.CALLING_CONVENTION_CHANGED)], policy="plugin_abi"
            )
            == Verdict.COMPATIBLE
        )

    def test_value_abi_trait_changed_is_compatible(self) -> None:
        assert (
            compute_verdict(
                [_change(ChangeKind.VALUE_ABI_TRAIT_CHANGED)], policy="plugin_abi"
            )
            == Verdict.COMPATIBLE
        )

    def test_calling_convention_strict_is_breaking(self) -> None:
        assert (
            compute_verdict(
                [_change(ChangeKind.CALLING_CONVENTION_CHANGED)], policy="strict_abi"
            )
            == Verdict.BREAKING
        )

    def test_func_removed_still_breaking_in_plugin(self) -> None:
        assert (
            compute_verdict([_change(ChangeKind.FUNC_REMOVED)], policy="plugin_abi")
            == Verdict.BREAKING
        )

    def test_symbol_version_required_added_is_breaking_in_plugin_policy(self) -> None:
        """plugin_abi treats deployment floor raises as BREAKING (host/plugin load risk)."""
        assert (
            compute_verdict(
                [_change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED)], policy="plugin_abi"
            )
            == Verdict.BREAKING
        )

    def test_symbol_version_required_added_is_risk_in_strict_policy(self) -> None:
        """strict_abi keeps this as COMPATIBLE_WITH_RISK."""
        assert (
            compute_verdict(
                [_change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED)], policy="strict_abi"
            )
            == Verdict.COMPATIBLE_WITH_RISK
        )

    def test_all_plugin_downgraded_kinds_produce_compatible(self) -> None:
        for kind in PLUGIN_ABI_DOWNGRADED_KINDS:
            result = compute_verdict([_change(kind)], policy="plugin_abi")
            assert result == Verdict.COMPATIBLE, (
                f"{kind} with plugin_abi → {result!r}, expected COMPATIBLE"
            )


# ── CLI/report-filter policy integration ─────────────────────────────────────


class TestCliPolicyFiltering:
    def _mk_result(self, policy: str = "strict_abi", *kinds: ChangeKind) -> DiffResult:
        return DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="lib.so",
            changes=[_change(k) for k in kinds],
            verdict=Verdict.NO_CHANGE,
            policy=policy,
        )

    def test_filter_source_only_strict(self) -> None:
        from abicheck.compat.cli import _filter_source_only

        result = self._mk_result("strict_abi", ChangeKind.ENUM_MEMBER_RENAMED)
        filtered = _filter_source_only(result)

        assert filtered.policy == "strict_abi"
        assert filtered.verdict == Verdict.API_BREAK
        assert len(filtered.source_breaks) == 1

    def test_filter_source_only_sdk_vendor_propagates_policy(self) -> None:
        from abicheck.compat.cli import _filter_source_only

        result = self._mk_result("sdk_vendor", ChangeKind.ENUM_MEMBER_RENAMED)
        filtered = _filter_source_only(result)

        # policy must be propagated — verdict AND .source_breaks both sdk_vendor
        assert filtered.policy == "sdk_vendor"
        assert filtered.verdict == Verdict.COMPATIBLE
        assert len(filtered.source_breaks) == 0
        assert len(filtered.compatible) == 1

    def test_filter_binary_only_strict(self) -> None:
        from abicheck.compat.cli import _filter_binary_only

        result = self._mk_result("strict_abi", ChangeKind.CALLING_CONVENTION_CHANGED)
        filtered = _filter_binary_only(result)

        assert filtered.policy == "strict_abi"
        assert filtered.verdict == Verdict.BREAKING
        assert len(filtered.breaking) == 1

    def test_filter_binary_only_plugin_abi_propagates_policy(self) -> None:
        from abicheck.compat.cli import _filter_binary_only

        result = self._mk_result("plugin_abi", ChangeKind.CALLING_CONVENTION_CHANGED)
        filtered = _filter_binary_only(result)

        assert filtered.policy == "plugin_abi"
        assert filtered.verdict == Verdict.COMPATIBLE
        assert len(filtered.breaking) == 0
        assert len(filtered.compatible) == 1


# ── CLI --policy end-to-end ───────────────────────────────────────────────────


class TestCliPolicy:
    def _write_snapshots(self, tmp_path: Any) -> tuple[Any, Any]:
        from abicheck.serialization import snapshot_to_dict

        old = AbiSnapshot(library="lib.so", version="1.0")
        new = AbiSnapshot(library="lib.so", version="2.0")
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(json.dumps(snapshot_to_dict(old)))
        new_p.write_text(json.dumps(snapshot_to_dict(new)))
        return old_p, new_p

    def test_policy_forwarded_to_compare(self, tmp_path: Any) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        old_p, new_p = self._write_snapshots(tmp_path)

        def _fake_compare(*_args: Any, **kwargs: Any) -> DiffResult:
            assert kwargs["policy"] == "plugin_abi"
            return DiffResult(
                old_version="1.0",
                new_version="2.0",
                library="lib.so",
                changes=[],
                verdict=Verdict.NO_CHANGE,
            )

        with patch("abicheck.service.compare_snapshots", side_effect=_fake_compare):
            result = CliRunner().invoke(
                main, ["compare", str(old_p), str(new_p), "--policy", "plugin_abi"]
            )

        assert result.exit_code == 0, result.output

    def test_policy_invalid_case_rejected(self, tmp_path: Any) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main
        from abicheck.frontends.cli.runtime import _EXIT_USAGE_ERROR

        old_p, new_p = self._write_snapshots(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--policy", "SDK_VENDOR"]
        )
        # Invalid option value → Click usage error, remapped to the dedicated
        # usage-error code so it is not mistaken for a "2 = source break" verdict.
        assert result.exit_code == _EXIT_USAGE_ERROR

    def test_policy_file_forwarded_to_compare(self, tmp_path: Any) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        old_p, new_p = self._write_snapshots(tmp_path)
        policy_p = tmp_path / "policy.yaml"
        policy_p.write_text("overrides: {}\n", encoding="utf-8")

        def _fake_compare(*_args: Any, **kwargs: Any) -> DiffResult:
            assert kwargs["policy_file"] is not None
            return DiffResult(
                old_version="1.0",
                new_version="2.0",
                library="lib.so",
                changes=[],
                verdict=Verdict.NO_CHANGE,
            )

        with patch("abicheck.service.compare_snapshots", side_effect=_fake_compare):
            result = CliRunner().invoke(
                main,
                ["compare", str(old_p), str(new_p), "--policy", str(policy_p)],
            )

        assert result.exit_code == 0, result.output

    def test_last_policy_operand_wins(self, tmp_path: Any) -> None:
        """One flag, one question: --policy takes a built-in profile name or a
        document path, and the last one given wins.

        The separate --policy-file this pair used to need is gone, so there is
        no precedence rule between two flags left to state -- only Click's
        ordinary last-value-wins over one scalar option, which the resolver
        then routes to the profile slot or the document slot by what the
        operand names.
        """
        from click.testing import CliRunner

        from abicheck.cli import main

        old_p, new_p = self._write_snapshots(tmp_path)
        policy_p = tmp_path / "strict.yaml"
        policy_p.write_text(
            "base_policy: strict_abi\noverrides: {}\n", encoding="utf-8"
        )

        captured: dict = {}

        def _fake_compare(*_args: Any, **kwargs: Any) -> DiffResult:
            captured["policy"] = kwargs.get("policy")
            captured["policy_file"] = kwargs.get("policy_file")
            return DiffResult(
                old_version="1.0",
                new_version="2.0",
                library="lib.so",
                changes=[],
                verdict=Verdict.NO_CHANGE,
            )

        # A document last: the document is loaded, the profile slot falls back
        # to the default rather than keeping the earlier profile name.
        with patch("abicheck.service.compare_snapshots", side_effect=_fake_compare):
            result = CliRunner().invoke(
                main,
                [
                    "compare",
                    str(old_p),
                    str(new_p),
                    "--policy",
                    "sdk_vendor",
                    "--policy",
                    str(policy_p),
                ],
            )
        assert result.exit_code == 0, result.output
        assert captured["policy_file"] is not None

        # A profile name last: no document is loaded at all.
        captured.clear()
        with patch("abicheck.service.compare_snapshots", side_effect=_fake_compare):
            result = CliRunner().invoke(
                main,
                [
                    "compare",
                    str(old_p),
                    str(new_p),
                    "--policy",
                    str(policy_p),
                    "--policy",
                    "sdk_vendor",
                ],
            )
        assert result.exit_code == 0, result.output
        assert captured["policy_file"] is None
        assert captured["policy"] == "sdk_vendor"

    def test_help_lists_policy_choices(self) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main
        from abicheck.frontends.cli.options.params import BUILTIN_POLICY_PROFILES

        result = CliRunner().invoke(main, ["compare", "--help"])
        assert result.exit_code == 0
        # --policy is no longer a click.Choice: it takes a built-in profile
        # name OR a document path, so its type is a plain string the callback
        # routes by value. The built-in names are still a closed set, and the
        # help text is where a user reads them -- so assert both, since a
        # metavar alone would let the name list silently drift out of the help.
        assert {"sdk_vendor", "plugin_abi", "strict_abi"} <= set(
            BUILTIN_POLICY_PROFILES
        )
        policy = next(
            p
            for p in main.commands["compare"].params
            if getattr(p, "name", "") == "policy"
        )
        norm = result.output.replace("│", "").replace("\n", "").replace(" ", "")
        assert "--policy" in norm
        assert policy.metavar == "NAME|PATH"
        help_norm = (policy.help or "").replace("\n", " ")
        for name in BUILTIN_POLICY_PROFILES:
            assert name in help_norm, name


class TestDiffResultPolicyAwareProperties:
    """DiffResult.breaking/source_breaks/compatible must honour the active policy."""

    def _mk_result(self, policy: str, *kinds: ChangeKind) -> DiffResult:
        return DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="lib.so",
            changes=[_change(k) for k in kinds],
            verdict=Verdict.NO_CHANGE,
            policy=policy,
        )

    def test_enum_rename_in_source_breaks_strict(self) -> None:
        r = self._mk_result("strict_abi", ChangeKind.ENUM_MEMBER_RENAMED)
        assert len(r.source_breaks) == 1
        assert len(r.compatible) == 0

    def test_enum_rename_in_compatible_sdk_vendor(self) -> None:
        r = self._mk_result("sdk_vendor", ChangeKind.ENUM_MEMBER_RENAMED)
        assert len(r.source_breaks) == 0
        assert len(r.compatible) == 1

    def test_calling_convention_in_breaking_strict(self) -> None:
        r = self._mk_result("strict_abi", ChangeKind.CALLING_CONVENTION_CHANGED)
        assert len(r.breaking) == 1

    def test_calling_convention_in_compatible_plugin(self) -> None:
        r = self._mk_result("plugin_abi", ChangeKind.CALLING_CONVENTION_CHANGED)
        assert len(r.breaking) == 0
        assert len(r.compatible) == 1


class TestCompatPolicyExposure:
    def test_compat_help_has_no_policy_flag(self) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        result = CliRunner().invoke(main, ["compat", "--help"])
        assert result.exit_code == 0, result.output
        assert "--policy" not in result.output

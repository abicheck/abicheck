from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.policy_file import PolicyFile


def _change(kind: ChangeKind):
    c = MagicMock()
    c.kind = kind
    return c


def test_policy_file_defaults_when_empty(tmp_path: Path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")

    pf = PolicyFile.load(p)
    assert pf.base_policy == "strict_abi"
    assert pf.overrides == {}


def test_policy_file_applies_overrides(tmp_path: Path) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
base_policy: strict_abi
overrides:
  enum_member_renamed: ignore
  calling_convention_changed: warn
""".strip(),
        encoding="utf-8",
    )

    pf = PolicyFile.load(p)

    assert pf.compute_verdict([_change(ChangeKind.ENUM_MEMBER_RENAMED)]) == Verdict.COMPATIBLE
    assert pf.compute_verdict([_change(ChangeKind.CALLING_CONVENTION_CHANGED)]) == Verdict.API_BREAK


def test_policy_file_unknown_base_policy_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("base_policy: custom", encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown base_policy"):
        PolicyFile.load(p)


def test_policy_file_invalid_severity_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad_severity.yaml"
    p.write_text(
        """
overrides:
  enum_member_renamed: maybe
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid severity"):
        PolicyFile.load(p)


def test_policy_file_empty_changes_no_change(tmp_path: Path) -> None:
    p = tmp_path / "empty_policy.yaml"
    p.write_text("overrides: {}\n", encoding="utf-8")

    pf = PolicyFile.load(p)
    assert pf.compute_verdict([]) == Verdict.NO_CHANGE


def test_policy_file_describe_contains_base_and_overrides(tmp_path: Path) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
base_policy: sdk_vendor
overrides:
  enum_member_renamed: ignore
""".strip(),
        encoding="utf-8",
    )

    pf = PolicyFile.load(p)
    text = pf.describe()
    assert "base_policy: sdk_vendor" in text
    assert "enum_member_renamed: ignore" in text


def test_policy_file_non_dict_yaml_rejected(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a YAML mapping"):
        PolicyFile.load(p)


def test_policy_file_base_policy_non_string_rejected(tmp_path: Path) -> None:
    p = tmp_path / "list_base.yaml"
    p.write_text("base_policy:\n  - sdk_vendor\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a string"):
        PolicyFile.load(p)


def test_policy_file_overrides_non_dict_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad_overrides.yaml"
    p.write_text("overrides: not_a_mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a YAML mapping"):
        PolicyFile.load(p)


def test_policy_file_mixed_override_and_base(tmp_path: Path) -> None:
    """One change overridden, one falls through to base policy."""
    p = tmp_path / "mixed.yaml"
    p.write_text(
        """
base_policy: strict_abi
overrides:
  enum_member_renamed: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)

    override_change = MagicMock()
    override_change.kind = ChangeKind.ENUM_MEMBER_RENAMED

    base_change = MagicMock()
    base_change.kind = ChangeKind.FUNC_REMOVED  # BREAKING in strict_abi

    # Mix: override says COMPATIBLE, base says BREAKING → BREAKING wins
    result = pf.compute_verdict([override_change, base_change])
    assert result == Verdict.BREAKING


def test_policy_file_describe_no_overrides(tmp_path: Path) -> None:
    """describe() formats correctly when overrides is empty (covers line 177 branch)."""
    p = tmp_path / "no_overrides.yaml"
    p.write_text("base_policy: strict_abi\n", encoding="utf-8")

    pf = PolicyFile.load(p)
    text = pf.describe()
    assert "base_policy: strict_abi" in text
    assert "overrides: (none)" in text


def test_policy_file_unknown_kind_logs_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    p = tmp_path / "unknown_kind.yaml"
    p.write_text(
        """
overrides:
  not_a_real_kind: ignore
""".strip(),
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        pf = PolicyFile.load(p)

    assert pf.overrides == {}
    assert "unknown ChangeKind slugs" in caplog.text


def test_policy_file_risk_severity_produces_compatible_with_risk(tmp_path: Path) -> None:
    """severity: risk in YAML policy file → COMPATIBLE_WITH_RISK verdict."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
base_policy: strict_abi
overrides:
  func_added: risk
""".strip(),
        encoding="utf-8",
    )

    pf = PolicyFile.load(p)
    result = pf.compute_verdict([_change(ChangeKind.FUNC_ADDED)])
    assert result == Verdict.COMPATIBLE_WITH_RISK, (
        f"Expected COMPATIBLE_WITH_RISK for severity=risk override, got {result}"
    )


def test_policy_file_symbol_version_required_added_is_risk_by_default(tmp_path: Path) -> None:
    """SYMBOL_VERSION_REQUIRED_ADDED must produce COMPATIBLE_WITH_RISK with default policy."""
    p = tmp_path / "policy.yaml"
    p.write_text("base_policy: strict_abi", encoding="utf-8")

    pf = PolicyFile.load(p)
    result = pf.compute_verdict([_change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED)])
    assert result == Verdict.COMPATIBLE_WITH_RISK, (
        f"SYMBOL_VERSION_REQUIRED_ADDED must be COMPATIBLE_WITH_RISK, got {result}"
    )


# ── Built-in shipped policies (G12) ───────────────────────────────────────

def test_builtin_security_policy_resolves_by_name() -> None:
    """`--policy-file security` resolves to the packaged security.yaml."""
    from abicheck.policies import builtin_policy_names
    from abicheck.policy_file import builtin_policy_path

    assert "security" in builtin_policy_names()
    resolved = builtin_policy_path("security")
    assert resolved is not None and resolved.is_file()
    assert resolved.name == "security.yaml"


def test_builtin_security_policy_gates_hardening_to_break() -> None:
    pf = PolicyFile.load(Path("security"))
    assert pf.base_policy == "strict_abi"
    for kind in (
        ChangeKind.RELRO_WEAKENED,
        ChangeKind.PIE_DISABLED,
        ChangeKind.STACK_CANARY_REMOVED,
        ChangeKind.FORTIFY_SOURCE_WEAKENED,
        ChangeKind.WRITABLE_EXECUTABLE_SEGMENT,
        ChangeKind.EXECUTABLE_STACK,
    ):
        assert pf.overrides.get(kind) == Verdict.BREAKING, kind
        assert pf.compute_verdict([_change(kind)]) == Verdict.BREAKING


def test_unknown_builtin_policy_name_returns_none() -> None:
    from abicheck.policy_file import builtin_policy_path
    assert builtin_policy_path("does-not-exist") is None


# ── cli_params.PolicyFileParam (Click type) ───────────────────────────────

def test_policy_file_param_accepts_builtin_name() -> None:
    from abicheck.cli_params import POLICY_FILE_PARAM
    out = POLICY_FILE_PARAM.convert("security", None, None)
    assert Path(out).name == "security.yaml"


def test_policy_file_param_accepts_existing_path(tmp_path: Path) -> None:
    from abicheck.cli_params import POLICY_FILE_PARAM
    p = tmp_path / "my.yaml"
    p.write_text("base_policy: strict_abi\n", encoding="utf-8")
    out = POLICY_FILE_PARAM.convert(str(p), None, None)
    assert Path(out) == p


def test_policy_file_param_rejects_unknown_name() -> None:
    import click

    from abicheck.cli_params import POLICY_FILE_PARAM
    with pytest.raises(click.BadParameter):
        POLICY_FILE_PARAM.convert("does-not-exist.yaml", None, None)


def test_builtin_policy_name_not_shadowed_by_file(tmp_path: Path, monkeypatch) -> None:
    """A local file named like a builtin must not shadow the shipped policy."""
    (tmp_path / "security").write_text(
        "base_policy: strict_abi\noverrides:\n  pie_disabled: ignore\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    pf = PolicyFile.load(Path("security"))

    assert pf.source_path is not None
    assert pf.source_path.name == "security.yaml"
    assert pf.overrides.get(ChangeKind.PIE_DISABLED) == Verdict.BREAKING
    assert pf.compute_verdict([_change(ChangeKind.PIE_DISABLED)]) == Verdict.BREAKING


def test_builtin_policy_name_not_shadowed_by_directory(tmp_path: Path, monkeypatch) -> None:
    """A directory named like a builtin (e.g. ``security/``) in CWD must not
    shadow the shipped policy and cause IsADirectoryError (Codex P2)."""
    (tmp_path / "security").mkdir()
    monkeypatch.chdir(tmp_path)
    pf = PolicyFile.load(Path("security"))
    # Resolved to the packaged policy, not the local directory.
    assert pf.base_policy == "strict_abi"
    assert pf.overrides.get(ChangeKind.RELRO_WEAKENED) == Verdict.BREAKING

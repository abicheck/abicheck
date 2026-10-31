"""The stored-bundle-facts ``compare`` rules, exercised on the workflow
interface with no Click context.

The CLI characterization suites (``test_cli_compare_bundle_facts_*.py``) pin
the exit codes and rendered messages end to end. These tests pin the same
rules one layer down: which request is rejected, why, in what order, and that
the release fan-out's shared messages have not drifted.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import click
import pytest

from abicheck.report.release_formats import STORED_RELEASE_FORMATS
from abicheck.workflows.bundle_facts_stored_compare import (
    per_library_report_path,
)
from abicheck.workflows.bundle_facts_stored_request import (
    BundleFactsCompareError,
    BundleFactsCompareRequest,
    resolve_max_json_object_nodes_cfg,
    validate_bundle_facts_request,
)


def _request(tmp_path: Path, **overrides: object) -> BundleFactsCompareRequest:
    new_input = tmp_path / "new"
    new_input.mkdir(exist_ok=True)
    fields: dict[str, object] = {
        "old_facts_path": tmp_path / "old.bundlefacts.json",
        "new_input": new_input,
    }
    fields.update(overrides)
    return BundleFactsCompareRequest(**fields)  # type: ignore[arg-type]


def test_supported_formats_are_json_markdown_junit_oneline_and_html() -> None:
    # --view show= stays out: a stored comparison has no per-member fan-out
    # to project a filter over. Every listed format is one live-shaped render.
    assert STORED_RELEASE_FORMATS == frozenset(
        {"json", "markdown", "junit", "oneline", "html"}
    )


def test_a_plain_request_validates_with_no_config(tmp_path: Path) -> None:
    assert validate_bundle_facts_request(_request(tmp_path)) is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"bundle_facts_out": Path("b.json")},
            "--bundle-facts-out is not supported",
        ),
        ({"dry_run": True}, "--dry-run is not supported"),
        ({"contract_mode": "strict"}, "--contract is not supported"),
        ({"severity_preset": "default"}, "--severity-preset/--pack"),
        ({"used_by_apps": ("app",)}, "--used-by/consumers.manifests is not supported"),
        ({"required_symbols": ("sym",)}, "consumers.required_symbols is not supported"),
        (
            {"use_cases_manifest": Path("uc.yml")},
            "--use-cases is not supported",
        ),
        (
            {"diagnostic_comparison": True},
            "--diagnostic-comparison is not supported",
        ),
        (
            {"include_labels": {Path("a"): "x"}},
            "A labeled --include",
        ),
        (
            {"build_or_source_operands_given": True},
            "--sources/--build-info/--dump-manifest are not supported",
        ),
        (
            {"probe_matrix_given": True},
            "build-configuration matrix is not supported",
        ),
        ({"debug_info_given": True}, "--debug-info is not supported"),
        ({"old_devel_pkg_given": True}, "A --header old=... development package"),
        (
            {"dependency_flags_given": True},
            "--follow-deps/--search-path/--ld-library-path are not",
        ),
        ({"debug_roots_given": True}, "--debug-info is not supported"),
        ({"depth": "build"}, "--depth build is not supported"),
        ({"depth": "source"}, "--depth source is not supported"),
        ({"show_only": True}, "output.view show=... is not supported"),
        ({"report_mode": "filtered"}, "output.view <mode> is not supported"),
        (
            {"old_headers_only": (Path("h"),)},
            "--header old=.../--include old=...",
        ),
        ({"old_version": "v2"}, "--label old=... is not supported"),
    ],
)
def test_each_unsupported_option_is_a_usage_error(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(BundleFactsCompareError, match=re.escape(message)) as exc_info:
        validate_bundle_facts_request(_request(tmp_path, **overrides))
    assert exc_info.value.usage is True


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"new_is_stored": True, "headers": (Path("h"),)},
            "--header new=.../--include new=...",
        ),
        (
            {"new_is_stored": True, "new_devel_pkg": Path("devel")},
            "A --header new=... development package",
        ),
        (
            {"new_is_stored": True, "bundle_facts_library_manifest": Path("m.yml")},
            "--bundle-facts-library-manifest is not supported",
        ),
        (
            {"new_is_stored": True, "new_version": "v9"},
            "--label new=... is not supported",
        ),
        (
            {"new_is_stored": True, "include_dependencies": True},
            "--include-system-declarations is not supported",
        ),
    ],
)
def test_stored_new_side_rejects_live_only_options(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(BundleFactsCompareError, match=re.escape(message)):
        validate_bundle_facts_request(_request(tmp_path, **overrides))


def test_new_side_rules_do_not_apply_to_a_live_new_input(tmp_path: Path) -> None:
    """The same options are fine when NEW_INPUT is live: the stored/stored
    rules are gated on ``new_is_stored``."""
    request = _request(tmp_path, headers=(Path("h"),), new_version="v9")
    assert validate_bundle_facts_request(request) is None


def test_release_only_messages_match_the_release_helper(tmp_path: Path) -> None:
    """The directory/package rules are reused verbatim from the release
    fan-out. Parity is pinned here so the two copies cannot drift apart."""
    from abicheck.cli_compare_options import _reject_set_input_flags

    cases: list[tuple[dict[str, object], dict[str, object]]] = [
        ({"used_by_apps": ("app",)}, {"used_by_apps": ("app",)}),
        ({"required_symbols": ("sym",)}, {"required_symbols": ("sym",)}),
        (
            {"use_cases_manifest": tmp_path / "uc.yml"},
            {"use_cases_manifest": tmp_path / "uc.yml"},
        ),
        ({"diagnostic_comparison": True}, {"diagnostic_comparison": True}),
        (
            {"include_labels": {Path("a"): "x"}},
            {"include_labels": {Path("a"): "x"}},
        ),
    ]
    for release_kwargs, request_kwargs in cases:
        with pytest.raises(click.UsageError) as release:
            _reject_set_input_flags(**release_kwargs)  # type: ignore[arg-type]
        with pytest.raises(BundleFactsCompareError) as stored:
            validate_bundle_facts_request(_request(tmp_path, **request_kwargs))
        assert str(stored.value) == release.value.message


def test_the_first_violated_rule_names_the_error(tmp_path: Path) -> None:
    """Rule order is part of the contract: dry-run is checked before the
    contract and the view modes, so it is the message a multi-fault request
    reports."""
    request = _request(tmp_path, dry_run=True, contract_mode="strict", show_only=True)
    with pytest.raises(BundleFactsCompareError, match="--dry-run is not supported"):
        validate_bundle_facts_request(request)


def test_depth_is_checked_before_the_view_modes(tmp_path: Path) -> None:
    request = _request(tmp_path, depth="build", show_only=True)
    with pytest.raises(BundleFactsCompareError, match="--depth build"):
        validate_bundle_facts_request(request)


def test_a_malformed_config_is_a_usage_error(tmp_path: Path) -> None:
    config = tmp_path / ".abicheck.yml"
    config.write_text("not_a_real_key: 1\n")
    request = _request(tmp_path, config_path=config)
    with pytest.raises(
        BundleFactsCompareError, match="cannot parse build config"
    ) as exc:
        validate_bundle_facts_request(request)
    assert exc.value.usage is True


def test_config_severity_block_is_rejected_on_the_stored_path(tmp_path: Path) -> None:
    config = tmp_path / ".abicheck.yml"
    config.write_text("severity:\n  preset: default\n")
    request = _request(tmp_path, config_path=config)
    with pytest.raises(BundleFactsCompareError, match=r"declares severity: settings"):
        validate_bundle_facts_request(request)


@pytest.mark.parametrize(
    ("library", "expected_name"),
    [
        ("libfoo.so.1", "libfoo.so.1.json"),
        # OLD_FACTS is user-supplied: no traversal may escape the output dir.
        ("../../etc/passwd", "passwd.json"),
        ("/abs/libbar.so", "libbar.so.json"),
        ("", "library.json"),
    ],
)
def test_per_library_report_path_keeps_only_the_final_component(
    tmp_path: Path, library: str, expected_name: str
) -> None:
    assert per_library_report_path(tmp_path, library) == tmp_path / expected_name


def _fake_config(**overrides: object) -> SimpleNamespace:
    """A loaded build config with every field the rules read, all unset."""
    fields: dict[str, object] = {
        "release_dso_only": False,
        "release_include_private_dso": False,
        "gate_fail_on_removed_library": False,
        "release_support_promise": None,
        "assurance_require_complete": False,
        "severity_preset": None,
        "severity_abi_breaking": None,
        "severity_potential_breaking": None,
        "severity_quality_issues": None,
        "severity_addition": None,
        "scope_public": None,
        "collapse_versioned_symbols": None,
        "public_symbols": None,
        "scope_show_redundant": None,
        "contract_post_manifest": None,
        "suppression_strict": None,
        "suppression_require_justification": None,
        "debug_format": None,
        "debug_dwarf_only": None,
        "debug_debuginfod": None,
        "debug_debuginfod_url": None,
        "debug_pdb_path": None,
        "source_method": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.mark.parametrize(
    ("config_overrides", "message"),
    [
        ({"gate_fail_on_removed_library": True}, "gate.fail_on_removed_library"),
        ({"release_support_promise": "enforce"}, "release.support_promise"),
        ({"suppression_strict": True}, "suppression:"),
        ({"severity_preset": "default"}, "severity:"),
        ({"debug_pdb_path": "x.pdb"}, "debug:"),
        ({"source_method": "s1"}, "source:"),
    ],
)
def test_each_config_block_rule_rejects_the_stored_operand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_overrides: dict[str, object],
    message: str,
) -> None:
    """Config-file settings with no channel into a stored comparison are
    rejected the same way their explicit-flag twins are."""
    from abicheck.workflows import bundle_facts_stored_request as mod

    monkeypatch.setattr(
        mod,
        "load_request_config",
        lambda _path: _fake_config(**config_overrides),
    )
    config = tmp_path / ".abicheck.yml"
    config.write_text("")
    with pytest.raises(BundleFactsCompareError, match=re.escape(message)) as exc:
        validate_bundle_facts_request(_request(tmp_path, config_path=config))
    assert exc.value.usage is True


def test_a_clean_config_passes_every_config_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from abicheck.workflows import bundle_facts_stored_request as mod

    loaded = _fake_config()
    monkeypatch.setattr(mod, "load_request_config", lambda _path: loaded)
    config = tmp_path / ".abicheck.yml"
    config.write_text("")
    assert (
        validate_bundle_facts_request(_request(tmp_path, config_path=config)) is loaded
    )


@pytest.mark.parametrize(
    ("configured", "explicit", "expected"),
    [
        (None, False, None),
        (50_000_000, True, 50_000_000),
        (50_000_000, False, 1_000_000),
        (500, False, 500),
    ],
)
def test_the_decode_budget_is_only_raised_by_an_explicit_config(
    configured: int | None, explicit: bool, expected: int | None
) -> None:
    assert (
        resolve_max_json_object_nodes_cfg(
            configured, config_explicit=explicit, default=1_000_000
        )
        == expected
    )


@pytest.mark.parametrize(
    ("param_hint", "usage", "expected_type"),
    [
        ("--config", True, click.BadParameter),
        (None, True, click.UsageError),
        (None, False, click.ClickException),
    ],
)
def test_a_workflow_error_maps_to_its_click_exit_kind(
    param_hint: str | None, usage: bool, expected_type: type[Exception]
) -> None:
    from abicheck.frontends.cli.commands.compare_bundle_facts import _click_error

    exc = _click_error(
        BundleFactsCompareError("msg", usage=usage, param_hint=param_hint)
    )
    assert type(exc) is expected_type
    assert "msg" in exc.format_message()

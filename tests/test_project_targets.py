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

"""Tests for ``buildsource.project_targets`` (ADR-047 §3, G30 P1.5).

Covers the schema round-trip, ``BuildConfig``'s recognition of the four new
top-level ``.abicheck.yml`` keys, and the cross-reference validator's rules:
kind-specific required/forbidden fields, ``library``/``bundle``/``channel``/
``profiles`` reference resolution, and the identifier charset every id must
satisfy to stay embeddable in a report ``check_id``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.inline import BuildConfig
from abicheck.buildsource.project_targets import (
    BaselineChannelSpec,
    BundleSpec,
    CheckSpec,
    ProfileSpec,
    ProjectTargetsConfig,
    TargetSpec,
    load_project_targets_config,
    validate_project_targets,
)
from abicheck.cli import main

# A full, valid PVXS-shaped example matching ADR-047 §3's excerpt.
_VALID_RAW = {
    "targets": {
        "libpvxs": {
            "kind": "library",
            "binary_pattern": "lib/libpvxs.so*",
            "public_headers": ["headers/pvxs"],
            "bundle": "pvxs-release",
            "bundle_only": False,
            "checks": [
                {
                    "channel": "accepted-main",
                    "depth": "headers",
                    "required": True,
                    "gate_mode": "local",
                }
            ],
        },
        "libpvxsIoc": {
            "kind": "library",
            "binary_pattern": "lib/libpvxsIoc.so*",
            "public_headers": ["headers/pvxsIoc"],
            "bundle": "pvxs-release",
        },
        "myapp-consumer": {
            "kind": "app-consumer",
            "consumer_binary_pattern": "bin/myapp",
            "library": "libpvxs",
        },
        "ioc-plugin-contract": {
            "kind": "plugin-contract",
            "contract_file": "contracts/ioc-plugin.syms",
            "library": "libpvxsIoc",
        },
    },
    "bundles": {"pvxs-release": {"targets": ["libpvxs", "libpvxsIoc"]}},
    "profiles": {
        "linux-x86_64-gcc13-release": {
            "contract": True,
            "os": "linux",
            "arch": "x86_64",
        },
        "ubuntu-latest-clang-debug-sanitizer": {"contract": False},
    },
    "baseline": {
        "channels": {
            "release-contract": {
                "source": "github-release",
                "asset_pattern": "abicheck-baseline-*.tar.zst",
            },
            "accepted-main": {
                "source": "actions-cache",
                "key_prefix": "abicheck-baseline-main",
            },
        }
    },
}


def test_valid_config_round_trips() -> None:
    config = ProjectTargetsConfig.from_dict(_VALID_RAW)
    assert set(config.targets) == {
        "libpvxs",
        "libpvxsIoc",
        "myapp-consumer",
        "ioc-plugin-contract",
    }
    assert config.targets["libpvxs"].checks == [
        CheckSpec(
            channel="accepted-main", depth="headers", required=True, gate_mode="local"
        )
    ]
    round_tripped = ProjectTargetsConfig.from_dict(config.to_dict())
    assert round_tripped == config


def test_valid_config_has_no_validation_errors() -> None:
    config = ProjectTargetsConfig.from_dict(_VALID_RAW)
    report = validate_project_targets(config)
    assert report.ok, report.errors


def test_empty_config_is_all_defaults() -> None:
    config = ProjectTargetsConfig.from_dict({})
    assert config.targets == {}
    assert config.bundles == {}
    assert config.profiles == {}
    assert config.baseline_channels == {}
    report = validate_project_targets(config)
    assert report.ok
    assert report.warnings


# ── BuildConfig recognizes the four new top-level keys ─────────────────────


def test_build_config_does_not_reject_the_new_top_level_keys() -> None:
    # BuildConfig itself doesn't parse targets/bundles/profiles/baseline (that's
    # this module's job) but must not treat their presence as an unknown key.
    config = BuildConfig.from_dict(_VALID_RAW)
    assert isinstance(config, BuildConfig)


def test_build_config_still_rejects_unknown_top_level_keys() -> None:
    with pytest.raises(ValueError, match="unknown .abicheck.yml key"):
        BuildConfig.from_dict({"totally_bogus_key": {}})


# ── from_dict structural/type errors (raise, ADR-043 strict convention) ────


@pytest.mark.parametrize(
    "raw,match",
    [
        ({"targets": {"foo": {"kind": "bogus"}}}, "kind must be one of"),
        ({"targets": {"foo": {"unknown_key": 1}}}, "unknown key"),
        ({"targets": "not-a-mapping"}, "must be a mapping"),
        ({"bundles": {"b": {"targets": []}}}, "non-empty list"),
        ({"bundles": {"b": {"targets": [1, 2]}}}, "list of strings"),
        ({"profiles": {"p": {"contract": "yes"}}}, "must be a boolean"),
        ({"baseline": {"unexpected": {}}}, "unknown key"),
        ({"baseline": {"channels": {"c": {"source": "ftp"}}}}, "source must be one of"),
        (
            {
                "targets": {
                    "foo": {
                        "checks": [
                            {"channel": "c", "depth": "headers", "required": "yes"}
                        ]
                    }
                }
            },
            "required must be a boolean",
        ),
        (
            {"targets": {"foo": {"checks": [{"depth": "headers"}]}}},
            "channel must be a non-empty string",
        ),
        (
            {"targets": {"foo": {"checks": ["not-a-mapping"]}}},
            "must be a mapping",
        ),
    ],
)
def test_from_dict_rejects_malformed_input(raw: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        ProjectTargetsConfig.from_dict(raw)


# ── validate_project_targets cross-reference rules ──────────────────────────


def test_library_kind_requires_binary_pattern() -> None:
    config = ProjectTargetsConfig.from_dict(
        {"targets": {"libfoo": {"kind": "library"}}}
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("requires binary_pattern" in e for e in report.errors)


def test_app_consumer_requires_consumer_binary_pattern_and_library() -> None:
    config = ProjectTargetsConfig.from_dict(
        {"targets": {"consumer": {"kind": "app-consumer"}}}
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("consumer_binary_pattern" in e for e in report.errors)
    assert any("requires library" in e for e in report.errors)


def test_app_consumer_library_must_resolve_to_a_library_kind_target() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "consumer": {
                    "kind": "app-consumer",
                    "consumer_binary_pattern": "bin/app",
                    "library": "other-consumer",
                },
                "other-consumer": {
                    "kind": "app-consumer",
                    "consumer_binary_pattern": "bin/other",
                    "library": "libfoo",
                },
                "libfoo": {"kind": "library", "binary_pattern": "lib/libfoo.so"},
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("must be a kind: library target" in e for e in report.errors)


def test_app_consumer_library_must_exist() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "consumer": {
                    "kind": "app-consumer",
                    "consumer_binary_pattern": "bin/app",
                    "library": "does-not-exist",
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("is not declared under targets:" in e for e in report.errors)


def test_plugin_contract_requires_contract_file_and_library() -> None:
    config = ProjectTargetsConfig.from_dict(
        {"targets": {"plugin": {"kind": "plugin-contract"}}}
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("requires contract_file" in e for e in report.errors)


def test_kind_specific_forbidden_fields_are_rejected() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "contract_file": "x.syms",
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("must not set contract_file" in e for e in report.errors)


def test_bundle_only_requires_bundle() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "bundle_only": True,
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("bundle_only requires bundle" in e for e in report.errors)


def test_bundle_reference_must_be_declared() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "bundle": "no-such-bundle",
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("is not declared under bundles:" in e for e in report.errors)


def test_bundle_members_must_exist_and_be_library_kind() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "consumer": {
                    "kind": "app-consumer",
                    "consumer_binary_pattern": "bin/app",
                    "library": "consumer",  # bogus self-reference, irrelevant here
                }
            },
            "bundles": {"rel": {"targets": ["consumer", "missing"]}},
        }
    )
    report = validate_project_targets(config)
    errors = "\n".join(report.errors)
    assert "must be kind: library" in errors
    assert "'missing' is not declared under targets:" in errors


def test_bundle_membership_must_agree_with_target_bundle_field() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "bundle": "bundle-a",
                }
            },
            "bundles": {
                "bundle-a": {"targets": ["libfoo"]},
                "bundle-b": {"targets": ["libfoo"]},
            },
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("must agree" in e for e in report.errors)


def test_check_channel_must_resolve_or_be_none_sentinel() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "checks": [{"channel": "no-such-channel", "depth": "headers"}],
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("is not declared under baseline.channels" in e for e in report.errors)


def test_check_channel_none_sentinel_skips_baseline_lookup() -> None:
    """ADR-047 §6 S5: `channel: none` must validate even with zero declared
    baseline channels — it's the explicit no-baseline-audit bypass."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "checks": [
                        {"channel": "none", "depth": "headers", "gate_mode": "local"}
                    ],
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert report.ok, report.errors


def test_check_depth_must_be_a_valid_rung() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "checks": [{"channel": "none", "depth": "quantum"}],
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("depth must be one of" in e for e in report.errors)


def test_check_gate_mode_must_be_valid() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "checks": [
                        {
                            "channel": "none",
                            "depth": "headers",
                            "gate_mode": "aggressive",
                        }
                    ],
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("gate_mode must be one of" in e for e in report.errors)


def test_check_profiles_selector_must_resolve() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "checks": [
                        {
                            "channel": "none",
                            "depth": "headers",
                            "profiles": ["no-such-profile"],
                        }
                    ],
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("is not declared under profiles:" in e for e in report.errors)


@pytest.mark.parametrize(
    "kind,name",
    [
        ("target", "libfoo bad name"),
        ("target", "@libfoo"),
        ("target", ""),
    ],
)
def test_identifier_charset_is_enforced(kind: str, name: str) -> None:
    config = ProjectTargetsConfig.from_dict(
        {"targets": {name: {"kind": "library", "binary_pattern": "lib/libfoo.so"}}}
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("is not a valid identifier" in e for e in report.errors)


def test_two_targets_sharing_a_bundle_is_the_pvxs_shape_and_is_valid() -> None:
    """The exact ADR-047 §3 excerpt: two library targets under one bundle,
    each still individually checkable (S14/S15 coexistence)."""
    config = ProjectTargetsConfig.from_dict(_VALID_RAW)
    assert validate_project_targets(config).ok


# ── loader ───────────────────────────────────────────────────────────────


def test_load_project_targets_config_missing_file_is_all_defaults(
    tmp_path: Path,
) -> None:
    config = load_project_targets_config(tmp_path / "no-such-file.yml")
    assert config == ProjectTargetsConfig()


def test_load_project_targets_config_reads_real_file(tmp_path: Path) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text(
        "targets:\n  libfoo:\n    kind: library\n    binary_pattern: lib/libfoo.so\n"
    )
    config = load_project_targets_config(config_path)
    assert set(config.targets) == {"libfoo"}
    assert config.targets["libfoo"].binary_pattern == "lib/libfoo.so"


# ── CLI ──────────────────────────────────────────────────────────────────


def test_cli_validate_ok(tmp_path: Path) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text(
        "targets:\n  libfoo:\n    kind: library\n    binary_pattern: lib/libfoo.so\n"
    )
    result = CliRunner().invoke(main, ["project-targets", "validate", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_cli_validate_reports_errors(tmp_path: Path) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text("targets:\n  libfoo:\n    kind: library\n")
    result = CliRunner().invoke(main, ["project-targets", "validate", str(config_path)])
    assert result.exit_code == 1, result.output
    assert "requires binary_pattern" in result.output


def test_cli_validate_json_output(tmp_path: Path) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text(
        "targets:\n  libfoo:\n    kind: library\n    binary_pattern: lib/libfoo.so\n"
    )
    result = CliRunner().invoke(
        main, ["project-targets", "validate", str(config_path), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    assert '"ok": true' in result.output


def test_cli_validate_malformed_yaml_is_usage_error(tmp_path: Path) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text("targets:\n  libfoo:\n    kind: bogus-kind\n")
    result = CliRunner().invoke(main, ["project-targets", "validate", str(config_path)])
    assert result.exit_code == 64, result.output


def test_cli_validate_missing_config_is_usage_error() -> None:
    result = CliRunner().invoke(
        main, ["project-targets", "validate", "/no/such/config.yml"]
    )
    assert result.exit_code != 0


def test_cli_group_help() -> None:
    result = CliRunner().invoke(main, ["project-targets", "--help"])
    assert result.exit_code == 0
    assert "validate" in result.output


# ── dataclass sanity (mirrors build_output.py's own coverage shape) ────────


def test_target_spec_to_dict_only_emits_kind_relevant_fields() -> None:
    lib = TargetSpec(id="libfoo", kind="library", binary_pattern="lib/libfoo.so")
    assert lib.to_dict() == {"kind": "library", "binary_pattern": "lib/libfoo.so"}

    consumer = TargetSpec(
        id="consumer",
        kind="app-consumer",
        consumer_binary_pattern="bin/app",
        library="libfoo",
        binary_pattern="should-not-appear",  # forbidden field, still stored on the dataclass
    )
    d = consumer.to_dict()
    assert d["kind"] == "app-consumer"
    assert d["consumer_binary_pattern"] == "bin/app"
    assert d["library"] == "libfoo"
    assert "binary_pattern" not in d


def test_bundle_profile_baseline_channel_round_trip() -> None:
    bundle = BundleSpec(id="rel", targets=["a", "b"])
    assert bundle.to_dict() == {"targets": ["a", "b"]}

    profile = ProfileSpec(id="linux", contract=True, os="linux", arch="x86_64")
    assert profile.to_dict() == {"contract": True, "os": "linux", "arch": "x86_64"}

    channel = BaselineChannelSpec(
        id="release-contract", source="github-release", asset_pattern="*.tar.zst"
    )
    assert channel.to_dict() == {
        "source": "github-release",
        "asset_pattern": "*.tar.zst",
    }

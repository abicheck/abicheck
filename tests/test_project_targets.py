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
    with pytest.raises(ValueError, match=r"unknown \.abicheck\.yml key"):
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


def test_bundle_only_target_must_not_declare_its_own_checks() -> None:
    """A bundle_only target is checked only as a bundle member, never
    standalone (per this schema's own docs) -- its own checks: would never
    run, so it's a validation error, not silently dead config."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "bundle": "rel",
                    "bundle_only": True,
                    "checks": [{"channel": "none", "depth": "headers"}],
                }
            },
            "bundles": {"rel": {"targets": ["libfoo"]}},
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("must not set its own checks" in e for e in report.errors)


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


def test_app_consumer_check_with_channel_none_is_rejected() -> None:
    """actions/check-target/validate-inputs.sh rejects baseline-channel:
    none for target-kind: app-consumer -- a no-baseline audit routes to
    `scan`, which has no --used-by equivalent to scope the check against.
    A validated config must not produce an unrunnable run-plan cell."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {"kind": "library", "binary_pattern": "lib/libfoo.so"},
                "myapp": {
                    "kind": "app-consumer",
                    "consumer_binary_pattern": "bin/myapp",
                    "library": "libfoo",
                    "checks": [{"channel": "none", "depth": "headers"}],
                },
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any(
        "channel: 'none' is not supported for kind: 'app-consumer'" in e
        for e in report.errors
    )


def test_plugin_contract_check_with_channel_none_is_rejected() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {"kind": "library", "binary_pattern": "lib/libfoo.so"},
                "plugin": {
                    "kind": "plugin-contract",
                    "contract_file": "contracts/plugin.syms",
                    "library": "libfoo",
                    "checks": [{"channel": "none", "depth": "headers"}],
                },
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any(
        "channel: 'none' is not supported for kind: 'plugin-contract'" in e
        for e in report.errors
    )


def test_library_check_with_channel_none_is_still_accepted() -> None:
    """The restriction is kind-scoped -- a plain library target's own
    channel: none audit check (ADR-047 §6 S5) is unaffected."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "checks": [{"channel": "none", "depth": "headers"}],
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert report.ok, report.errors


def test_bundle_check_depth_build_is_rejected() -> None:
    """actions/check-target/validate-inputs.sh rejects requested-depth:
    build/source for kind: bundle -- a bundle check always compares
    directories, which never collects inline build/source evidence."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "bundle": "release",
                },
            },
            "bundles": {
                "release": {
                    "targets": ["libfoo"],
                    "checks": [{"channel": "none", "depth": "build"}],
                }
            },
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any(
        "depth 'build' is not supported for a bundle check" in e for e in report.errors
    )


def test_bundle_check_depth_binary_and_headers_are_accepted() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "bundle": "release",
                },
            },
            "bundles": {
                "release": {
                    "targets": ["libfoo"],
                    "checks": [
                        {"channel": "none", "depth": "binary"},
                        {"channel": "none", "depth": "headers"},
                    ],
                }
            },
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


# ── Bidirectional bundle membership (code-review finding) ──────────────────


def test_one_way_bundle_declaration_is_rejected() -> None:
    """A target claiming `bundle: rel` that `bundles.rel.targets` doesn't list
    back must fail — both directions must agree (review finding)."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "foo": {
                    "kind": "library",
                    "binary_pattern": "lib/foo.so",
                    "bundle": "rel",
                },
                "bar": {
                    "kind": "library",
                    "binary_pattern": "lib/bar.so",
                    "bundle": "rel",
                },
            },
            "bundles": {"rel": {"targets": ["bar"]}},
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any(
        "does not list 'foo' back" in e and "must agree in both directions" in e
        for e in report.errors
    )


# ── Kind-specific forbidden fields, derived exhaustively (review finding) ──


def test_library_must_not_set_library_field() -> None:
    """A `kind: library` target setting `library:` (meaningless for this
    kind) must fail, not silently pass and get dropped by to_dict()."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "foo": {"kind": "library", "binary_pattern": "x", "library": "bar"},
                "bar": {"kind": "library", "binary_pattern": "y"},
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("must not set library" in e for e in report.errors)


@pytest.mark.parametrize("kind", ["app-consumer", "plugin-contract"])
def test_consumer_kinds_must_not_set_bundle_fields(kind: str) -> None:
    raw = {
        "kind": kind,
        "library": "bar",
        "bundle": "rel",
        "bundle_only": True,
    }
    if kind == "app-consumer":
        raw["consumer_binary_pattern"] = "bin/app"
    else:
        raw["contract_file"] = "x.syms"
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "foo": raw,
                "bar": {"kind": "library", "binary_pattern": "y"},
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("must not set bundle." in e for e in report.errors)
    assert any("must not set bundle_only." in e for e in report.errors)


# ── Strict type-checking on scalar/list target fields ───────────────────────


@pytest.mark.parametrize(
    "raw,match",
    [
        ({"binary_pattern": ["lib/foo.so"]}, r"binary_pattern must be a string"),
        ({"bundle": 123}, r"bundle must be a string"),
        ({"consumer_binary_pattern": []}, r"consumer_binary_pattern must be a string"),
        ({"library": 1.5}, r"library must be a string"),
        ({"contract_file": True}, r"contract_file must be a string"),
        ({"public_headers": "not-a-list"}, r"public_headers must be a list"),
        ({"public_headers": [1, 2]}, r"public_headers must be a list of strings"),
    ],
)
def test_target_scalar_and_list_fields_reject_wrong_types(
    raw: dict, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        ProjectTargetsConfig.from_dict({"targets": {"foo": {"kind": "library", **raw}}})


def test_target_not_a_mapping_raises() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        ProjectTargetsConfig.from_dict({"targets": {"foo": "not-a-mapping"}})


def test_target_bundle_only_wrong_type_raises() -> None:
    with pytest.raises(ValueError, match="bundle_only must be a boolean"):
        ProjectTargetsConfig.from_dict(
            {"targets": {"foo": {"kind": "library", "bundle_only": "yes"}}}
        )


def test_target_checks_not_a_list_raises() -> None:
    with pytest.raises(ValueError, match=r"\.checks must be a list"):
        ProjectTargetsConfig.from_dict(
            {"targets": {"foo": {"kind": "library", "checks": "not-a-list"}}}
        )


# ── CheckSpec.from_dict structural errors ───────────────────────────────────


def test_check_spec_unknown_key_raises() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        ProjectTargetsConfig.from_dict(
            {
                "targets": {
                    "foo": {
                        "kind": "library",
                        "checks": [{"channel": "none", "depth": "headers", "bogus": 1}],
                    }
                }
            }
        )


def test_check_spec_empty_depth_raises() -> None:
    with pytest.raises(ValueError, match="depth must be a non-empty string"):
        ProjectTargetsConfig.from_dict(
            {
                "targets": {
                    "foo": {
                        "kind": "library",
                        "checks": [{"channel": "none", "depth": ""}],
                    }
                }
            }
        )


def test_check_spec_gate_mode_wrong_type_raises() -> None:
    with pytest.raises(ValueError, match="gate_mode must be a string"):
        ProjectTargetsConfig.from_dict(
            {
                "targets": {
                    "foo": {
                        "kind": "library",
                        "checks": [
                            {"channel": "none", "depth": "headers", "gate_mode": 1}
                        ],
                    }
                }
            }
        )


# ── gate_mode default depends on channel (ADR-047 §8 S5: advisory) ─────────


def test_check_spec_none_channel_defaults_gate_mode_to_advisory() -> None:
    """A `channel: "none"` (S5 no-baseline audit) check has no baseline-drift
    verdict to gate CI on, so it must default to advisory, not local -- a
    minimal `{channel: none, depth: ...}` entry must not unexpectedly block
    CI (ADR-047 §8's S5 row: "Advisory by default")."""
    check = CheckSpec.from_dict({"channel": "none", "depth": "headers"}, where="x")
    assert check.gate_mode == "advisory"


def test_check_spec_real_channel_defaults_gate_mode_to_local() -> None:
    check = CheckSpec.from_dict(
        {"channel": "accepted-main", "depth": "headers"}, where="x"
    )
    assert check.gate_mode == "local"


def test_check_spec_explicit_gate_mode_overrides_none_channel_default() -> None:
    check = CheckSpec.from_dict(
        {"channel": "none", "depth": "headers", "gate_mode": "local"}, where="x"
    )
    assert check.gate_mode == "local"


def test_check_spec_to_dict_includes_profiles_when_set() -> None:
    check = CheckSpec(
        channel="accepted-main", depth="headers", profiles=["linux-x86_64"]
    )
    assert check.to_dict() == {
        "channel": "accepted-main",
        "depth": "headers",
        "required": True,
        "gate_mode": "local",
        "profiles": ["linux-x86_64"],
    }


def test_check_profiles_selector_passes_when_declared() -> None:
    """The positive counterpart of test_check_profiles_selector_must_resolve:
    a profiles[] entry that *does* resolve must not be flagged."""
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
                            "profiles": ["linux-x86_64"],
                        }
                    ],
                }
            },
            "profiles": {"linux-x86_64": {"contract": True}},
        }
    )
    report = validate_project_targets(config)
    assert report.ok, report.errors


# ── checks[].profiles scoped to a non-contract (test-only) profile ─────────


def test_check_with_real_channel_cannot_scope_to_a_non_contract_profile() -> None:
    """contract: false profiles never get a baseline (S17) -- a check that
    names a real channel and scopes only to such a profile can never be
    satisfied, so it's a validation error."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "checks": [
                        {
                            "channel": "accepted-main",
                            "depth": "headers",
                            "profiles": ["test-lane"],
                        }
                    ],
                }
            },
            "profiles": {"test-lane": {"contract": False}},
            "baseline": {
                "channels": {
                    "accepted-main": {
                        "source": "actions-cache",
                        "key_prefix": "abicheck-baseline-main",
                    }
                }
            },
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("contract: false" in e for e in report.errors)


def test_check_with_none_channel_may_scope_to_a_non_contract_profile() -> None:
    """The exemption: a channel: "none" audit check has no baseline to
    resolve in the first place, so scoping it to a test-only lane is a
    legitimate S5 use case, not an error."""
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
                            "profiles": ["test-lane"],
                        }
                    ],
                }
            },
            "profiles": {"test-lane": {"contract": False}},
        }
    )
    report = validate_project_targets(config)
    assert report.ok, report.errors


# ── BundleSpec/ProfileSpec/BaselineChannelSpec structural errors ───────────


def test_bundle_not_a_mapping_raises() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        ProjectTargetsConfig.from_dict({"bundles": {"rel": "not-a-mapping"}})


def test_bundle_unknown_key_raises() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        ProjectTargetsConfig.from_dict(
            {"bundles": {"rel": {"targets": ["a"], "bogus": 1}}}
        )


# ── Bundle-level checks: (review finding — ADR-047 §5 needs bundle cells) ──


def test_bundle_checks_round_trip_and_validate() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "a": {"kind": "library", "binary_pattern": "a.so", "bundle": "rel"},
                "b": {"kind": "library", "binary_pattern": "b.so", "bundle": "rel"},
            },
            "bundles": {
                "rel": {
                    "targets": ["a", "b"],
                    "checks": [{"channel": "none", "depth": "headers"}],
                }
            },
        }
    )
    assert config.bundles["rel"].checks == [
        CheckSpec(channel="none", depth="headers", gate_mode="advisory")
    ]
    assert config.bundles["rel"].to_dict()["checks"] == [
        {
            "channel": "none",
            "depth": "headers",
            "required": True,
            "gate_mode": "advisory",
        }
    ]
    round_tripped = ProjectTargetsConfig.from_dict(config.to_dict())
    assert round_tripped == config
    report = validate_project_targets(config)
    assert report.ok, report.errors


def test_bundle_checks_not_a_list_raises() -> None:
    with pytest.raises(ValueError, match=r"\.checks must be a list"):
        ProjectTargetsConfig.from_dict(
            {"bundles": {"rel": {"targets": ["a"], "checks": "not-a-list"}}}
        )


def test_bundle_checks_item_not_a_mapping_raises() -> None:
    with pytest.raises(ValueError, match=r"\.checks\[0\] must be a mapping"):
        ProjectTargetsConfig.from_dict(
            {"bundles": {"rel": {"targets": ["a"], "checks": ["not-a-mapping"]}}}
        )


def test_bundle_checks_invalid_entry_is_flagged_by_validator() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {"a": {"kind": "library", "binary_pattern": "a.so"}},
            "bundles": {
                "rel": {
                    "targets": ["a"],
                    "checks": [{"channel": "none", "depth": "not-a-real-depth"}],
                }
            },
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any(
        "bundle 'rel'.checks[0]" in e and "depth must be one of" in e
        for e in report.errors
    )


# ── Top-level key strictness (review finding — a typo'd block was silently
# ── ignored instead of caught) ──────────────────────────────────────────────


def test_misspelled_top_level_key_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"unknown \.abicheck\.yml key"):
        ProjectTargetsConfig.from_dict(
            {"tagrets": {"foo": {"kind": "library", "binary_pattern": "x"}}}
        )


def test_mixed_type_top_level_keys_raise_value_error_not_type_error() -> None:
    """A bare PyYAML 1.1 boolean-like top-level key (``on:``/``off:``/``yes:``/
    ``no:``) alongside another unknown string key must still be reported as
    the documented ``ValueError`` usage error, not crash with ``TypeError``
    from comparing ``bool`` and ``str`` inside ``sorted()`` (Codex finding)."""
    with pytest.raises(ValueError, match=r"unknown \.abicheck\.yml key"):
        ProjectTargetsConfig.from_dict({True: {}, "tagrets": {}})


@pytest.mark.parametrize(
    "raw",
    [
        {"targets": {"foo": {True: 1, "unknown_field": 2, "kind": "library"}}},
        {"bundles": {"b": {True: 1, "unknown_field": 2, "targets": ["x"]}}},
        {"profiles": {"p": {True: 1, "unknown_field": 2}}},
        {
            "baseline": {
                "channels": {"c": {True: 1, "unknown_field": 2, "source": "git"}}
            }
        },
        {
            "targets": {
                "foo": {
                    "kind": "library",
                    "checks": [
                        {
                            True: 1,
                            "unknown_field": 2,
                            "channel": "c",
                            "depth": "headers",
                        }
                    ],
                }
            }
        },
    ],
)
def test_mixed_type_nested_keys_raise_value_error_not_type_error(raw: dict) -> None:
    """Same PyYAML 1.1 boolean-key pitfall as the top-level check, but inside
    a target/bundle/profile/baseline-channel/check entry's own unknown-key
    check (Codex finding — the top-level fix didn't cover these nested
    ``sorted(set(d) - known)`` call sites)."""
    with pytest.raises(ValueError, match="unknown key"):
        ProjectTargetsConfig.from_dict(raw)


def test_other_abicheck_yml_blocks_are_accepted_and_ignored() -> None:
    """A real `.abicheck.yml` legitimately carries blocks this module doesn't
    own (severity, scope, ...) alongside targets/bundles/profiles/baseline —
    those must not be rejected as unknown."""
    config = ProjectTargetsConfig.from_dict(
        {
            "severity": {"preset": "strict_abi"},
            "scope": {"public": True},
            "targets": {"foo": {"kind": "library", "binary_pattern": "x"}},
        }
    )
    assert set(config.targets) == {"foo"}


# ── Non-string mapping keys are rejected, not silently str()-coerced ───────


@pytest.mark.parametrize(
    "raw",
    [
        {"targets": {123: {"kind": "library", "binary_pattern": "x"}}},
        {"bundles": {123: {"targets": ["a"]}}},
        {"profiles": {123: {"contract": True}}},
        {"baseline": {"channels": {123: {"source": "git"}}}},
        # PyYAML's default (YAML 1.1) resolver reads a bare `on` key as `True`.
        {"targets": {True: {"kind": "library", "binary_pattern": "x"}}},
    ],
)
def test_non_string_mapping_keys_are_rejected(raw: dict) -> None:
    with pytest.raises(ValueError, match=r"key.*must be strings"):
        ProjectTargetsConfig.from_dict(raw)


# ── "none" is reserved as the no-baseline sentinel ──────────────────────────


def test_none_cannot_be_declared_as_a_real_baseline_channel() -> None:
    config = ProjectTargetsConfig.from_dict(
        {"baseline": {"channels": {"none": {"source": "git"}}}}
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("is reserved as the no-baseline sentinel" in e for e in report.errors)


def test_profile_not_a_mapping_raises() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        ProjectTargetsConfig.from_dict({"profiles": {"linux": "not-a-mapping"}})


def test_profile_unknown_key_raises() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        ProjectTargetsConfig.from_dict({"profiles": {"linux": {"bogus": 1}}})


@pytest.mark.parametrize("key", ["os", "arch"])
def test_profile_os_arch_wrong_type_raises(key: str) -> None:
    with pytest.raises(ValueError, match=f"{key} must be a string"):
        ProjectTargetsConfig.from_dict({"profiles": {"linux": {key: 1}}})


def test_baseline_channel_not_a_mapping_raises() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        ProjectTargetsConfig.from_dict(
            {"baseline": {"channels": {"c": "not-a-mapping"}}}
        )


def test_baseline_channel_unknown_key_raises() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        ProjectTargetsConfig.from_dict(
            {"baseline": {"channels": {"c": {"source": "git", "bogus": 1}}}}
        )


@pytest.mark.parametrize("key", ["asset_pattern", "key_prefix"])
def test_baseline_channel_pattern_fields_wrong_type_raises(key: str) -> None:
    with pytest.raises(ValueError, match=f"{key} must be a string"):
        ProjectTargetsConfig.from_dict(
            {"baseline": {"channels": {"c": {"source": "git", key: 1}}}}
        )


def test_baseline_channel_github_release_requires_asset_pattern() -> None:
    config = ProjectTargetsConfig.from_dict(
        {"baseline": {"channels": {"release-contract": {"source": "github-release"}}}}
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("requires asset_pattern" in e for e in report.errors)


def test_baseline_channel_actions_cache_requires_key_prefix() -> None:
    config = ProjectTargetsConfig.from_dict(
        {"baseline": {"channels": {"accepted-main": {"source": "actions-cache"}}}}
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("requires key_prefix" in e for e in report.errors)


# ── ProjectTargetsConfig.to_dict() partial-block branches ──────────────────


def test_to_dict_targets_only() -> None:
    config = ProjectTargetsConfig(
        targets={"foo": TargetSpec(id="foo", kind="library", binary_pattern="x")}
    )
    d = config.to_dict()
    assert set(d) == {"targets"}


def test_to_dict_bundles_only() -> None:
    config = ProjectTargetsConfig(bundles={"rel": BundleSpec(id="rel", targets=["a"])})
    d = config.to_dict()
    assert set(d) == {"bundles"}


def test_to_dict_profiles_only() -> None:
    config = ProjectTargetsConfig(profiles={"linux": ProfileSpec(id="linux")})
    d = config.to_dict()
    assert set(d) == {"profiles"}


def test_to_dict_baseline_only() -> None:
    config = ProjectTargetsConfig(
        baseline_channels={
            "c": BaselineChannelSpec(id="c", source="git"),
        }
    )
    d = config.to_dict()
    assert set(d) == {"baseline"}
    assert d["baseline"] == {"channels": {"c": {"source": "git"}}}


# ── TargetSpec.to_dict() per-kind field combinations ────────────────────────


def test_target_spec_to_dict_library_with_all_optional_fields() -> None:
    target = TargetSpec(
        id="libfoo",
        kind="library",
        binary_pattern="lib/libfoo.so",
        public_headers=["headers/foo"],
        bundle="rel",
        bundle_only=True,
        checks=[CheckSpec(channel="none", depth="headers")],
    )
    d = target.to_dict()
    assert d["binary_pattern"] == "lib/libfoo.so"
    assert d["public_headers"] == ["headers/foo"]
    assert d["bundle"] == "rel"
    assert d["bundle_only"] is True
    assert d["checks"] == [
        {"channel": "none", "depth": "headers", "required": True, "gate_mode": "local"}
    ]


def test_target_spec_to_dict_plugin_contract() -> None:
    target = TargetSpec(
        id="plugin",
        kind="plugin-contract",
        contract_file="x.syms",
        library="libfoo",
        checks=[CheckSpec(channel="none", depth="headers")],
    )
    d = target.to_dict()
    assert d["kind"] == "plugin-contract"
    assert d["contract_file"] == "x.syms"
    assert d["library"] == "libfoo"
    assert d["checks"]


def test_target_spec_to_dict_library_with_no_optional_fields_set() -> None:
    target = TargetSpec(id="libfoo", kind="library")
    assert target.to_dict() == {"kind": "library"}


def test_target_spec_to_dict_on_a_kind_that_bypasses_from_dict_validation() -> None:
    """Same direct-construction scenario as the validator test below, for
    `to_dict()`: an unrecognized `kind` emits no kind-specific fields."""
    target = TargetSpec(id="foo", kind="totally-unknown-kind", binary_pattern="x")
    assert target.to_dict() == {"kind": "totally-unknown-kind"}


def test_target_issues_on_a_kind_that_bypasses_from_dict_validation() -> None:
    """`TargetSpec` is a plain dataclass — direct construction (bypassing
    `from_dict`'s `kind` enum check) with an unrecognized `kind` must not
    crash `validate_project_targets`; it simply gets no kind-specific
    required/forbidden-field checks (only the identifier check applies)."""
    config = ProjectTargetsConfig(
        targets={"foo": TargetSpec(id="foo", kind="totally-unknown-kind")}
    )
    report = validate_project_targets(config)
    assert report.ok, report.errors


# ── loader error paths ──────────────────────────────────────────────────────


def test_load_project_targets_config_malformed_yaml_raises(tmp_path: Path) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text("targets: [this is not: valid: yaml: at: all\n")
    with pytest.raises(ValueError, match="cannot read project config"):
        load_project_targets_config(config_path)


def test_load_project_targets_config_non_mapping_yaml_is_all_defaults(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text("- just\n- a\n- list\n")
    config = load_project_targets_config(config_path)
    assert config == ProjectTargetsConfig()


# ── CLI error paths ──────────────────────────────────────────────────────


def test_cli_validate_empty_file_is_ok(tmp_path: Path) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text("")
    result = CliRunner().invoke(main, ["project-targets", "validate", str(config_path)])
    assert result.exit_code == 0, result.output


def test_cli_validate_non_mapping_yaml_is_usage_error(tmp_path: Path) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text("- just\n- a\n- list\n")
    result = CliRunner().invoke(main, ["project-targets", "validate", str(config_path)])
    assert result.exit_code == 64, result.output
    assert "must contain a yaml mapping" in result.output.lower()


def test_cli_validate_with_warnings_shown_in_text_output(tmp_path: Path) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text("")
    result = CliRunner().invoke(main, ["project-targets", "validate", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "warning(s)" in result.output


def test_cli_validate_malformed_yaml_raises_usage_error(tmp_path: Path) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text("targets: [this is not: valid: yaml: at: all\n")
    result = CliRunner().invoke(main, ["project-targets", "validate", str(config_path)])
    assert result.exit_code == 64, result.output
    assert "cannot read" in result.output.lower()


def test_cli_validate_writes_to_output_file(tmp_path: Path) -> None:
    config_path = tmp_path / ".abicheck.yml"
    config_path.write_text(
        "targets:\n  libfoo:\n    kind: library\n    binary_pattern: lib/libfoo.so\n"
    )
    out_path = tmp_path / "report.txt"
    result = CliRunner().invoke(
        main,
        [
            "project-targets",
            "validate",
            str(config_path),
            "-o",
            str(out_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "OK" in out_path.read_text()

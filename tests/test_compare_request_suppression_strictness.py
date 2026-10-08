# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""``CompareRequest.strict_suppressions``/``require_justification``.

The typed API now honors the two suppression-strictness settings the CLI
resolves from ``.abicheck.yml`` (``suppression.strict`` /
``suppression.require_justification``), through the one shared loader. Each
rejection is checked on both front ends, and the expired-rule message must be
the same text on both.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _family_f2_routes import _invoke, write_operands

from abicheck.errors import ValidationError
from abicheck.service import CompareRequest, InputSpec
from abicheck.service_compare_pipeline import run_compare_request

EXPIRED = (
    "version: 1\nsuppressions:\n"
    "  - symbol: foo\n    reason: old\n    expires: 2001-01-01\n"
)
UNJUSTIFIED = "version: 1\nsuppressions:\n  - symbol: foo\n"
VALID = "version: 1\nsuppressions:\n  - symbol: foo\n    reason: ok\n"


def _files(tmp: Path, rules: str, **cfg: bool) -> tuple[Path, Path]:
    sup = tmp / "sup.yaml"
    sup.write_text(rules, encoding="utf-8")
    conf = tmp / "c.abicheck.yml"
    body = "".join(f"  {k}: {str(v).lower()}\n" for k, v in cfg.items())
    conf.write_text("suppression:\n" + body, encoding="utf-8")
    return sup, conf


def _api(tmp: Path, sup: Path, **fields: bool) -> object:
    ops = write_operands(tmp, "removal_and_addition")
    return run_compare_request(
        CompareRequest(
            old=InputSpec(path=ops.old),
            new=InputSpec(path=ops.new),
            suppress=sup,
            **fields,
        )
    )


def _cli(tmp: Path, sup: Path, conf: Path) -> object:
    ops = write_operands(tmp, "removal_and_addition")
    return _invoke(
        [
            "compare",
            str(ops.old),
            str(ops.new),
            "--suppress",
            str(sup),
            "--config",
            str(conf),
            "-o",
            "json=-",
        ]
    )


@pytest.mark.parametrize(
    ("rules", "key", "needle"),
    [
        (EXPIRED, "strict", "expired suppression rule(s)"),
        (UNJUSTIFIED, "require_justification", "reason"),
    ],
)
def test_both_front_ends_reject(
    rules: str, key: str, needle: str, tmp_path: Path
) -> None:
    sup, conf = _files(tmp_path, rules, **{key: True})
    field = "strict_suppressions" if key == "strict" else "require_justification"
    with pytest.raises(ValidationError, match=r".") as api_err:
        _api(tmp_path / "api", sup, **{field: True})
    cli = _cli(tmp_path / "cli", sup, conf)
    assert cli.exit_code != 0
    assert needle in str(api_err.value)
    assert needle in cli.output


def test_expired_message_is_identical_on_both_front_ends(tmp_path: Path) -> None:
    sup, conf = _files(tmp_path, EXPIRED, strict=True)
    with pytest.raises(ValidationError) as api_err:
        _api(tmp_path / "api", sup, strict_suppressions=True)
    cli = _cli(tmp_path / "cli", sup, conf)
    assert str(api_err.value) in cli.output


@pytest.mark.parametrize("rules", [EXPIRED, UNJUSTIFIED])
def test_defaults_off_keep_the_previous_behavior(rules: str, tmp_path: Path) -> None:
    """Off (the default), neither setting rejects anything -- unchanged."""
    sup, _ = _files(tmp_path, rules)
    assert _api(tmp_path / "api", sup).diff is not None


def test_settings_on_accept_a_valid_file(tmp_path: Path) -> None:
    sup, _ = _files(tmp_path, VALID)
    result = _api(
        tmp_path / "api", sup, strict_suppressions=True, require_justification=True
    )
    assert result.diff is not None

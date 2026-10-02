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

"""The suppression audit reaches the release artifact, not only stderr.

One-comparison-product slice 7o left one per-library disclosure that the
directory/package ``compare`` wrote only to stderr: the suppression audit
(stale rules, and rules that hid a BREAKING change). These tests run the
public CLI. The oracle is the single-pair report for the same pair and the
same suppression document: the release's per-library block must equal it,
across several independently chosen rule sets rather than one fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _snap(version: str, names: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version=version,
        functions=[
            Function(
                name=n,
                mangled=f"_Z{len(n)}{n}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
            for n in names
        ],
        from_headers=True,
    )


def _layout(tmp_path: Path, rules: list[str]) -> tuple[Path, Path, Path, Path, Path]:
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old_file = old_dir / "libfoo.json"
    new_file = new_dir / "libfoo.json"
    old_file.write_text(
        snapshot_to_json(_snap("1.0", ["foo", "keep"])), encoding="utf-8"
    )
    new_file.write_text(snapshot_to_json(_snap("2.0", ["keep"])), encoding="utf-8")
    sup = tmp_path / "suppress.yaml"
    body = "".join(f"  - symbol: {r}\n    reason: test rule {r}\n" for r in rules)
    sup.write_text(f"version: 1\nsuppressions:\n{body}", encoding="utf-8")
    return old_dir, new_dir, old_file, new_file, sup


def _run(*args: str) -> str:
    result = CliRunner().invoke(main, list(args))
    assert result.exit_code in (0, 1, 2, 4), result.output
    return result.stdout


#: (rules in the suppression document, expect a high-risk match, expect stale)
CASES = [
    (["_Z3foov"], True, False),  # hides the one BREAKING removal
    (["bar"], False, True),  # matches nothing
    (["_Z3foov", "bar"], True, True),  # both at once
    (["bar", "baz"], False, True),  # two stale rules, order preserved
]


@pytest.mark.parametrize(("rules", "high_risk", "stale"), CASES)
def test_release_json_block_equals_the_single_pair_block(
    tmp_path: Path, rules: list[str], high_risk: bool, stale: bool
) -> None:
    old_dir, new_dir, old_file, new_file, sup = _layout(tmp_path, rules)
    single = json.loads(
        _run(
            "compare",
            str(old_file),
            str(new_file),
            "--suppress",
            str(sup),
            "-o",
            "json=-",
        )
    )
    release = json.loads(
        _run(
            "compare",
            str(old_dir),
            str(new_dir),
            "--suppress",
            str(sup),
            "-o",
            "json=-",
        )
    )
    assert release["release_schema_version"] == "1.12"
    (lib,) = release["libraries"]
    assert lib["suppression_audit"] == single["suppression_audit"]
    # Vacuity guard: the oracle itself says something for every case.
    assert bool(single["suppression_audit"]["high_risk_matches"]) is high_risk
    assert bool(single["suppression_audit"]["stale_rules"]) is stale


@pytest.mark.parametrize(("rules", "high_risk", "stale"), CASES)
def test_release_markdown_names_every_audited_rule(
    tmp_path: Path, rules: list[str], high_risk: bool, stale: bool
) -> None:
    old_dir, new_dir, _, _, sup = _layout(tmp_path, rules)
    release = json.loads(
        _run(
            "compare",
            str(old_dir),
            str(new_dir),
            "--suppress",
            str(sup),
            "-o",
            "json=-",
        )
    )
    audit = release["libraries"][0]["suppression_audit"]
    md = _run(
        "compare",
        str(old_dir),
        str(new_dir),
        "--suppress",
        str(sup),
        "-o",
        "markdown=-",
    )
    assert "## 🧾 Suppression Audit" in md
    assert "### `libfoo" in md
    for label in audit["stale_rules"]:
        assert f"- `{label}`" in md
    for match in audit["high_risk_matches"]:
        assert f"`{match['rule']}` suppressed {match['kind']}" in md
    assert ("High-risk matches" in md) is high_risk
    if high_risk:
        # The symbol tail is demangled; the rule label above stayed verbatim.
        assert "suppressed func_removed: foo()" in md
    assert ("Stale rules" in md) is stale


def test_no_suppression_document_adds_nothing(tmp_path: Path) -> None:
    old_dir, new_dir, *_ = _layout(tmp_path, ["_Z3foov"])
    release = json.loads(_run("compare", str(old_dir), str(new_dir), "-o", "json=-"))
    assert "suppression_audit" not in release["libraries"][0]
    md = _run("compare", str(old_dir), str(new_dir), "-o", "markdown=-")
    assert "Suppression Audit" not in md


def test_renderer_covers_expiry_lists_and_skips_empty_members() -> None:
    """Expired/near-expiry need a dated rule; exercise the renderer directly."""
    from abicheck.report.render_release_markdown import _release_md_suppression_audit

    libs: list[dict[str, object]] = [
        {
            "library": "liba.so",
            "suppression_audit": {
                "total_rules": 3,
                "stale_rules": [],
                "high_risk_matches": [],
                "expired_rules": ["old-rule"],
                "near_expiry_rules": ["soon-rule"],
            },
        },
        {
            "library": "libb.so",
            "suppression_audit": {
                "total_rules": 1,
                "stale_rules": [],
                "high_risk_matches": [],
                "expired_rules": [],
                "near_expiry_rules": [],
            },
        },
        {"library": "libc.so"},
    ]
    md = "\n".join(_release_md_suppression_audit(libs))
    assert "Expired rules:\n- `old-rule`" in md
    assert "Rules nearing expiry:\n- `soon-rule`" in md
    assert "liba.so" in md
    assert "libb.so" not in md and "libc.so" not in md
    assert _release_md_suppression_audit(libs[1:]) == []

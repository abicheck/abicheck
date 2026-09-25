"""One contract, many providers, with ownership: the MKL/oneDAL scenario end
to end through the real directory ``compare`` (evidence-entity-model Phase 3,
ADR-075 D7; the plan's Problem #3).

A release-wide header tree carries two components' headers, ``include/a``
(``liba``'s API) and ``include/b`` (``libb``'s). Compared with only ``liba``'s
binary, today's per-member check demands ``b_fn`` from ``liba`` -- a
declaration another member provides. With ``include/b`` declared as another
owner's root, that declaration is ``external`` and owes ``liba`` nothing,
while ``liba``'s own genuinely missing ``a_missing`` is still reported (the
negative control). The two-member release reaches the same answer through
the release-level reconciliation, and a one-member package matches the
scalar path (``AGENTS.md`` "One model, any cardinality").
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ELF fixture"),
]

_CONFIG = """\
scope:
  public_header_dirs: [include/a]
  dependencies:
    - name: libb
      header_roots: [include/b]
"""


def _require(*tools: str) -> None:
    for tool in tools:
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")


def _tree(root: Path, members: tuple[str, ...], *, config: bool) -> Path:
    (root / "include/a").mkdir(parents=True)
    (root / "include/b").mkdir(parents=True)
    (root / "include/a/a.h").write_text(
        "#pragma once\nint a_fn(void);\nint a_missing(void);\n"
    )
    (root / "include/b/b.h").write_text("#pragma once\nint b_fn(void);\n")
    sources = {
        "liba": "int a_fn(void){return 1;}\n",
        "libb": "int b_fn(void){return 2;}\n",
    }
    for side in ("old", "new"):
        (root / side).mkdir()
        for member in members:
            src = root / f"{member}.c"
            src.write_text(sources[member])
            subprocess.run(
                [
                    "gcc",
                    "-shared",
                    "-fPIC",
                    str(src),
                    "-o",
                    str(root / side / f"{member}.so"),
                ],
                check=True,
            )
    if config:
        (root / ".abicheck.yml").write_text(_CONFIG)
    return root


def _compare(root: Path, *operands: str) -> dict:
    out = root / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "compare",
            *operands,
            "-H",
            "include",
            "-o",
            f"json={out}",
        ],
        cwd=root,
        env={**os.environ, "ABICHECK_AST_FRONTEND": "castxml"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(out.read_text())


def _member_public_not_exported(report: dict) -> set[str]:
    return {
        f["symbol"]
        for lib in report.get("libraries", [])
        for f in lib.get("findings", [])
        if f.get("kind") == "public_not_exported"
    }


def _release_missing(report: dict) -> set[str]:
    rec = report.get("public_surface_reconciliation") or {}
    return {m["symbol"] for m in rec.get("missing_exports", [])}


@pytest.fixture
def toolchain() -> None:
    _require("gcc", "castxml")


def test_without_ownership_the_member_is_blamed_for_its_sibling(
    toolchain, tmp_path
) -> None:
    """The defect, pinned so the fix below cannot pass vacuously."""
    report = _compare(_tree(tmp_path, ("liba",), config=False), "old", "new")
    assert _member_public_not_exported(report) == {"a_missing", "b_fn"}


def test_a_declaration_another_owner_provides_is_not_this_members_missing_export(
    toolchain, tmp_path
) -> None:
    report = _compare(_tree(tmp_path, ("liba",), config=True), "old", "new")
    found = _member_public_not_exported(report)
    assert "b_fn" not in found
    assert found == {"a_missing"}  # negative control: a real miss survives


def test_two_member_release_reports_only_the_real_miss(toolchain, tmp_path) -> None:
    """The release-level reconciliation over both members' exports: `b_fn`
    is satisfied by `libb`, `a_fn` by `liba` (a C header tree acquired as C,
    exactly as the members were parsed), and only `a_missing` is missing."""
    for config in (False, True):
        root = tmp_path / f"cfg{int(config)}"
        report = _compare(_tree(root, ("liba", "libb"), config=config), "old", "new")
        assert _release_missing(report) == {"a_missing"}, config
        assert _member_public_not_exported(report) == set(), config


def test_one_member_package_matches_the_scalar_path(toolchain, tmp_path) -> None:
    for config in (False, True):
        root = _tree(tmp_path / f"cfg{int(config)}", ("liba",), config=config)
        package = _member_public_not_exported(_compare(root, "old", "new"))
        scalar_report = _compare(root, "old/liba.so", "new/liba.so")
        scalar = {
            c["symbol"]
            for c in scalar_report.get("changes", [])
            if c.get("kind") == "public_not_exported"
        }
        assert package == scalar, config


def test_a_stated_cxx_language_is_the_release_contract(toolchain, tmp_path) -> None:
    """A stated ``compile.lang: c++`` parses the language-ambiguous tree as
    C++, so C exports do not satisfy its C++ contract. Reading explicitness
    off the spelling instead made every C export satisfy it and the release
    reported nothing missing (Codex review, PR #1374)."""
    root = _tree(tmp_path, ("liba", "libb"), config=False)
    (root / ".abicheck.yml").write_text("compile:\n  lang: c++\n")
    missing = _release_missing(_compare(root, "old", "new"))
    assert missing and all(s.startswith("_Z") for s in missing), missing
    assert {"_Z4a_fnv", "_Z4b_fnv", "_Z9a_missingv"} <= missing


def test_scalar_receipt_records_the_roots_the_run_classified_under(
    toolchain, tmp_path
) -> None:
    """ADR-075 D7 on the single-pair path: both sides are extracted under
    ``-H include``, so the contract receipt names that root as an explicit
    CLI input (CodeRabbit review, PR #1377: it was dropped before the
    resolver and the receipt read "not stated")."""
    root = _tree(tmp_path, ("liba",), config=False)
    report = _compare(root, "old/liba.so", "new/liba.so", "--contract", "auto")
    ctx = report["contract_context"]["evaluation_context"]
    assert (
        ctx["field_provenance"]["surface.ownership.header_dirs"]["layer"]
        == "explicit_cli"
    )
    roots = ctx["resolved_config"]["surface"]["ownership"]["target_roots"]
    assert roots == [str((root / "include").resolve())]

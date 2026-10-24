"""The field-shapes corpus (``tests/field_shapes/``): one directory per
real-world shape that escaped every other test, each checked end to end.

Every directory holds ``v1/`` and ``v2/`` (``api.h`` + ``api.cpp``) and a
``ground_truth.json`` using the ``catalog/ground_truth.json`` vocabulary
(``expected``, ``expected_kinds``, ``expected_absent_kinds``, ``description``)
plus the build/parse knobs the shape needs. This one runner compiles both
sides with g++, dumps them with ``abicheck dump`` (castxml headers), and
compares the snapshots with ``abicheck compare`` at each depth the truth
lists -- all through the CLI, in-process, under the
``model_warning_hygiene`` gate, so a misleading model warning is a failure
too.

The oracle is each directory's own ``ground_truth.json``, written from the
field report, never the detectors' helpers.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

CORPUS = Path(__file__).parent / "field_shapes"
SHAPES = sorted(p.name for p in CORPUS.iterdir() if (p / "ground_truth.json").is_file())

#: Keys a ground truth may carry; anything else is a typo, not a silent no-op.
_KNOWN_KEYS = frozenset(
    {
        "description",
        "field_report",
        "bug_class",
        "related_tests",
        "cxxflags",
        "parse_defines",
        "parse_includes",
        "suppress",
        "old_generation",
        "depths",
        "expected",
        "expected_kinds",
        "expected_absent_kinds",
        "expected_findings",
        "coverage_contains",
        "coverage_absent",
        "suppressed_rule_id_pattern",
    }
)


def _truth(shape: str) -> dict:
    return json.loads(
        (CORPUS / shape / "ground_truth.json").read_text(encoding="utf-8")
    )


def test_corpus_has_the_reported_shapes() -> None:
    assert set(SHAPES) >= {
        "auto_return_setters",
        "overloaded_member_template",
        "same_short_name_namespaces",
        "ifdef_sycl_overload",
        "system_typedef_via_vanished_instance",
        "policy_without_ids",
    }


@pytest.mark.parametrize("shape", SHAPES)
def test_ground_truth_is_well_formed(shape: str) -> None:
    truth = _truth(shape)
    assert set(truth) <= _KNOWN_KEYS, set(truth) - _KNOWN_KEYS
    assert truth["description"].strip()
    assert truth["bug_class"].strip()
    assert truth["depths"] and set(truth["depths"]) <= {"headers", "build", "source"}
    for rel in truth.get("related_tests", []):
        assert (Path(__file__).parent.parent / rel).is_file(), rel
    for side in ("v1", "v2"):
        assert (CORPUS / shape / side / "api.h").is_file()
        assert (CORPUS / shape / side / "api.cpp").is_file()


def _cli(args: list[str]) -> str:
    result = CliRunner().invoke(main, args, catch_exceptions=False)
    assert result.exit_code in (0, 1, 2, 4), (args, result.output[-3000:])
    return result.output


def _build_and_dump(shape: str, side: str, truth: dict, tmp: Path) -> Path:
    src = CORPUS / shape / side
    out = tmp / side
    out.mkdir()
    lib = out / "libapi.so"
    includes = [str(CORPUS / shape / i) for i in truth.get("parse_includes", [])]
    defines = truth.get("parse_defines", [])
    cmd = [
        "g++",
        "-std=c++17",
        "-shared",
        "-fPIC",
        *truth.get("cxxflags", []),
        *(f"-D{d}" for d in defines),
        f"-I{src}",
        *(f"-I{i}" for i in includes),
        str(src / "api.cpp"),
        "-o",
        str(lib),
        # Same install name on both sides (as ELF builds carry no -soname at
        # all): Mach-O otherwise records each side's own output path in
        # LC_ID_DYLIB, a soname_changed that is a build artefact, not the API
        # change the shape encodes.
        *(["-Wl,-install_name,@rpath/libapi.so"] if sys.platform == "darwin" else []),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, (cmd, proc.stderr)
    snap = out / "snap.json"
    args = ["dump", str(lib), "-H", str(src / "api.h"), "-I", str(src)]
    for inc in includes:
        args += ["-I", inc]
    for d in defines:
        args += ["-D", d]
    _cli([*args, "-o", str(snap)])
    return snap


def _freeze_old_generation(snap: Path) -> None:
    """Rewrite *snap* as a pre-generation-6 extractor wrote it (H1): castxml's
    ``AutoType`` spelling, and no generation stamp (it predates the stamp).
    The dump CLI writes the sectioned layout, so the stamp is removed
    wherever it sits rather than only at the top level."""
    from abicheck.storage.generation_migrations import FACT_GENERATIONS_KEY

    def unstamp(node: object) -> None:
        if isinstance(node, dict):
            node.pop(FACT_GENERATIONS_KEY, None)
            for v in node.values():
                unstamp(v)
        elif isinstance(node, list):
            for v in node:
                unstamp(v)

    text = snap.read_text(encoding="utf-8")
    assert re.search(r"\bauto\b", text), "the shape must store an auto spelling"
    doc = json.loads(re.sub(r"\bauto\b", "AutoType", text))
    unstamp(doc)
    snap.write_text(json.dumps(doc), encoding="utf-8")


def _compare(
    shape: str, truth: dict, old: Path, new: Path, depth: str, tmp: Path
) -> dict:
    report = tmp / f"{depth}.json"
    args = ["compare", str(old), str(new), "--depth", depth, "-o", f"json={report}"]
    if "suppress" in truth:
        args += ["--suppress", str(CORPUS / shape / truth["suppress"])]
    _cli(args)
    return json.loads(report.read_text(encoding="utf-8"))


def check_report(truth: dict, report: dict) -> list[str]:
    """Every way *report* disagrees with *truth* (empty: it agrees)."""
    errors = []
    kinds = {c["kind"] for c in report.get("changes", [])}
    findings = {(c["kind"], c.get("symbol") or "") for c in report.get("changes", [])}
    if "expected" in truth and report["verdict"] != truth["expected"]:
        errors.append(f"verdict {report['verdict']} != {truth['expected']}")
    for k in truth.get("expected_kinds", []):
        if k not in kinds:
            errors.append(f"missing kind {k}")
    for k in truth.get("expected_absent_kinds", []):
        if k in kinds:
            errors.append(f"unexpected kind {k}")
    for kind, symbol in truth.get("expected_findings", []):
        if (kind, symbol) not in findings:
            errors.append(f"missing finding {kind} on {symbol}")
    warnings = "\n".join(report.get("coverage_warnings", []))
    for text in truth.get("coverage_contains", []):
        if text not in warnings:
            errors.append(f"coverage lacks {text!r}")
    for text in truth.get("coverage_absent", []):
        if text in warnings:
            errors.append(f"coverage has {text!r}")
    pattern = truth.get("suppressed_rule_id_pattern")
    if pattern:
        ids = _suppression_rule_ids(report)
        if not ids:
            errors.append("no suppressed finding carries a rule_id")
        errors += [
            f"rule_id {i!r} !~ {pattern}" for i in ids if not re.fullmatch(pattern, i)
        ]
    return errors


def _suppression_rule_ids(report: dict) -> list[str]:
    ids: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            rid = node.get("rule_id")
            if isinstance(rid, str):
                ids.append(rid)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(report)
    return sorted(set(ids))


def test_check_report_catches_each_disagreement() -> None:
    truth = {
        "expected": "NO_CHANGE",
        "expected_kinds": ["func_removed"],
        "expected_absent_kinds": ["type_size_changed"],
        "expected_findings": [["func_removed", "f"]],
        "coverage_contains": ["drift"],
        "coverage_absent": ["bad"],
        "suppressed_rule_id_pattern": "rule-[0-9a-f]{12}",
    }
    good = {
        "verdict": "NO_CHANGE",
        "changes": [{"kind": "func_removed", "symbol": "f"}],
        "coverage_warnings": ["generation drift"],
        "suppression": {"rules": [{"rule_id": "rule-0123456789ab"}]},
    }
    assert check_report(truth, good) == []
    bad = {
        "verdict": "BREAKING",
        "changes": [{"kind": "type_size_changed", "symbol": "T"}],
        "coverage_warnings": ["bad"],
        "x": [{"rule_id": "datetime.date(2027, 4, 1)"}],
    }
    assert len(check_report(truth, bad)) == 7


@pytest.mark.integration
@pytest.mark.model_warning_hygiene
@pytest.mark.parametrize("shape", SHAPES)
def test_field_shape_matches_ground_truth(shape: str, tmp_path: Path) -> None:
    if not (shutil.which("g++") and shutil.which("castxml")):
        pytest.skip("needs g++ and castxml")
    truth = _truth(shape)
    old = _build_and_dump(shape, "v1", truth, tmp_path)
    new = _build_and_dump(shape, "v2", truth, tmp_path)
    if truth.get("old_generation"):
        _freeze_old_generation(old)
    failures = {}
    for depth in truth["depths"]:
        report = _compare(shape, truth, old, new, depth, tmp_path)
        errors = check_report(truth, report)
        if errors:
            failures[depth] = errors + [
                f"findings: {sorted((c['kind'], c.get('symbol')) for c in report['changes'])}"
            ]
    assert failures == {}

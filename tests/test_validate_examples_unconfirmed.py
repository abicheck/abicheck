"""The catalog harness reads ADR-090's ``UNCONFIRMED`` floor per lane.

A stripped binary with no public header has no type-level evidence, so a
compatibility claim from it is honestly ``UNCONFIRMED`` and the case's ground
truth is judged against the observed verdict. In every other situation type
evidence exists, so ``UNCONFIRMED`` means evidence was lost and must FAIL.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import tests.validate_examples as ve  # noqa: E402
from tests._abicheck_subprocess import abicheck_command  # noqa: E402

CLAIMS = ("NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK")


def _report(verdict: str, observed: str | None) -> str:
    doc: dict[str, object] = {"verdict": verdict, "changes": [{"kind": "k"}]}
    if observed is not None:
        doc["unconfirmed"] = {"observed_verdict": observed}
    return json.dumps(doc)


def _parse(stdout: str, monkeypatch) -> str:
    def fake_run(cmd, **_kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(ve.subprocess, "run", fake_run)
    got, kinds, err = ve._run_compare_and_parse(abicheck_command("compare"))
    assert err is None and kinds == ("k",)
    return got


@pytest.mark.parametrize("observed", CLAIMS)
@pytest.mark.parametrize("variant", ve.ARTIFACT_VARIANTS)
@pytest.mark.parametrize("headers", ("none", "old", "new", "both"))
def test_unconfirmed_is_judged_on_the_observed_verdict_only_without_evidence(
    observed: str, variant: str, headers: str, tmp_path: Path, monkeypatch
) -> None:
    hdr = tmp_path / "api.h"
    hdr.write_text("int f(void);\n")
    v1 = hdr if headers in {"old", "both"} else None
    v2 = hdr if headers in {"new", "both"} else None
    got = _parse(_report("UNCONFIRMED", observed), monkeypatch)

    judged, note = ve._resolve_unconfirmed(got, variant, v1, v2)

    # Oracle: type evidence is absent only for a stripped build with no header.
    no_evidence = variant == "stripped-headers" and headers == "none"
    assert judged == (observed if no_evidence else "UNCONFIRMED")
    assert bool(note) is no_evidence
    if no_evidence:
        assert observed in note and "UNCONFIRMED" in note


@pytest.mark.parametrize("variant", ve.ARTIFACT_VARIANTS)
@pytest.mark.parametrize("verdict", ("BREAKING", "API_BREAK", *CLAIMS))
def test_a_confirmed_verdict_passes_through_unchanged(
    verdict: str, variant: str, monkeypatch
) -> None:
    got = _parse(_report(verdict, None), monkeypatch)
    assert ve._resolve_unconfirmed(got, variant, None, None) == (verdict, "")


def test_a_missing_header_path_counts_as_no_header(tmp_path: Path) -> None:
    ghost = tmp_path / "absent.h"
    judged, _ = ve._resolve_unconfirmed(
        "UNCONFIRMED:COMPATIBLE", "stripped-headers", ghost, ghost
    )
    assert judged == "COMPATIBLE"


def test_an_unconfirmed_report_without_observed_verdict_stays_unconfirmed(
    monkeypatch,
) -> None:
    got = _parse(_report("UNCONFIRMED", None), monkeypatch)
    assert ve._resolve_unconfirmed(got, "stripped-headers", None, None) == (
        "UNCONFIRMED",
        "",
    )

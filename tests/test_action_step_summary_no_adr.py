"""The job summary the Action publishes must not cite internal ADR ids.

The ``[report-policy]`` cell of ``test-action.yml`` checks the report *files*
for ADR ids, but the job summary was never checked. A step's
``$GITHUB_STEP_SUMMARY`` is that step's own file, so a later workflow step
cannot read what the Action wrote; the check therefore runs here instead.

Each scenario drives the real ``compare`` command over snapshot fixtures (no
binaries, no compiler) and then checks every user-facing output it produces:
the report files, and the summary the publisher derives from the JSON report
(``render_report`` -> ``decide`` -> ``summary_for_plan``) at every detail
level. The Action's own shell summary (``action/run.sh``) is covered by the
literal scan in ``test_no_adr_in_user_text.py``.

**Bug class:** ``report.internal_reference_leaks_to_users``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main as cli_main
from abicheck.frontends.action.report_publication import (
    PublicationIdentity,
    decide,
    render_report,
    summary_for_plan,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from check_no_adr_citations import ADR_CITATION_RE, find_citations  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "contract_context_v1"
OLD = FIXTURES / "old.json"
NEW = FIXTURES / "new.json"
SHA = "a" * 40
IDENTITY = PublicationIdentity(
    identity="abicheck:report-policy", run_id="1000", run_attempt=1, head_sha=SHA
)
DETAILS = ("summary", "standard", "full")


def _run_compare(args: list[str]) -> None:
    result = CliRunner().invoke(cli_main, args, catch_exceptions=False)
    # 0 = compatible, 4 = ABI break: both are real outcomes that must render.
    assert result.exit_code in (0, 4), result.output


def _scenarios(tmp_path: Path) -> dict[str, dict[str, Path]]:
    policy = tmp_path / "versioning-policy.yml"
    policy.write_text(
        "versioning:\n  promise: abi_within_major\n  enforcement: block\n",
        encoding="utf-8",
    )
    operands = {
        "compatible": [str(OLD), str(OLD)],
        "breaking": [str(OLD), str(NEW)],
        "versioning_policy": [str(OLD), str(NEW), "--policy", str(policy)],
    }
    scenarios: dict[str, dict[str, Path]] = {}
    for name, args in operands.items():
        base = tmp_path / name
        base.mkdir()
        outputs = {
            "json": base / "report.json",
            "markdown": base / "report.md",
            "html": base / "report.html",
        }
        emit = [x for kind, path in outputs.items() for x in ("-o", f"{kind}={path}")]
        _run_compare(["compare", *args, *emit])
        scenarios[name] = outputs
    return scenarios


@pytest.fixture(scope="module")
def scenarios(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Path]]:
    return _scenarios(tmp_path_factory.mktemp("summary-scenarios"))


def test_detector_matches_adr_citations() -> None:
    # Vacuity guard: the scan below must be able to see a citation at all.
    assert find_citations("see ADR-049 D8 for details")
    assert find_citations("(ADR 23)")
    assert ADR_CITATION_RE.search("ADR068")
    assert not find_citations("an ADRESS field, CADR-12 is not a citation")


@pytest.mark.parametrize("name", ["compatible", "breaking", "versioning_policy"])
def test_scenario_reports_are_adr_free(
    scenarios: dict[str, dict[str, Path]], name: str
) -> None:
    for kind in ("json", "markdown", "html"):
        text = scenarios[name][kind].read_text(encoding="utf-8")
        assert text.strip(), f"{name}/{kind} is empty: nothing was checked"
        assert not find_citations(text), f"{name}/{kind} cites an ADR"


@pytest.mark.parametrize("detail", DETAILS)
@pytest.mark.parametrize("name", ["compatible", "breaking", "versioning_policy"])
def test_published_summary_is_adr_free(
    scenarios: dict[str, dict[str, Path]], name: str, detail: str
) -> None:
    data = json.loads(scenarios[name]["json"].read_text(encoding="utf-8"))
    rendered = render_report(data, identity=IDENTITY, detail=detail)
    plan = decide(rendered, identity=IDENTITY, comments=[], sha=SHA)
    assert plan.posts, "nothing published, so the summary was never generated"
    summary, _ = summary_for_plan(rendered, plan)
    # Non-vacuous: the summary is about this report, not an empty or
    # placeholder body (a clean run renders its audit line, not the verdict).
    assert summary.strip()
    assert data["library"] in summary
    for label, text in (("body", rendered.body), ("summary", summary)):
        assert not ADR_CITATION_RE.search(text), f"{name}/{detail} {label} cites an ADR"


def test_check_script_exit_codes(
    tmp_path: Path, scenarios: dict[str, dict[str, Path]]
) -> None:
    from check_no_adr_citations import main

    assert main([str(scenarios["breaking"]["markdown"])]) == 0
    dirty = tmp_path / "dirty.md"
    dirty.write_text("Accepted under ADR-066 D4.\n", encoding="utf-8")
    assert main([str(dirty)]) == 1
    # No file given is a usage error, never a silent pass.
    assert main([]) == 2

"""Byte-identical characterization goldens for the HTML report renderers.

These lock the *exact* output of the three HTML renderers
(``generate_html_report``, ``appcompat_to_html``, ``stack_to_html``) so the
``html_template`` page-chrome extraction (architecture-deepening candidate N-A)
can be proven behaviour-preserving: the renderers must collapse onto one shared
page seam without changing a single output byte.

Inputs are fixed strings (no timestamps / paths from the environment), so the
output is deterministic. If the HTML output format ever changes *on purpose*,
regenerate the goldens with ``python tests/test_html_template_golden.py`` in a
deliberate commit and explain why.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.appcompat_html import appcompat_to_html
from abicheck.checker import LibraryMetadata, Verdict
from abicheck.checker_policy import ChangeKind, Confidence
from abicheck.checker_types import Change, DiffResult
from abicheck.contract_relevance_types import (
    CompatibilityEvaluationStatus,
    ContractAssurance,
    ContractRelevance,
)
from abicheck.html_report import generate_html_report
from abicheck.policy_file import PolicyFile
from abicheck.reclassify import ReclassifyRule
from abicheck.severity import SeverityConfig
from abicheck.stack_checker import StackCheckResult, StackVerdict
from abicheck.stack_html import stack_to_html

_GOLDEN_DIR = Path(__file__).parent / "golden" / "html_template"


# ---------------------------------------------------------------------------
# Deterministic input builders (cover as many chrome/section paths as possible)
# ---------------------------------------------------------------------------


def _change(kind: str, symbol: str, desc: str, old: str = "", new: str = "") -> object:
    from enum import Enum

    class K(str, Enum):
        V = kind

    return SimpleNamespace(
        kind=K.V,
        symbol=symbol,
        demangled_symbol=symbol,
        description=desc,
        old_value=old,
        new_value=new,
        source_location=None,
        affected_symbols=None,
        caused_by_type=None,
        caused_count=0,
    )


def _main_report_html() -> str:
    result = SimpleNamespace(
        verdict=SimpleNamespace(value="BREAKING"),
        changes=[
            _change(
                "func_removed", "old_api", "Public function removed", "old_api", ""
            ),
            _change("func_added", "new_api", "Function added", "", "new_api"),
        ],
        suppressed_changes=[],
        suppressed_count=0,
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        suppression_file_provided=False,
    )
    return generate_html_report(
        result, lib_name="libtest.so", old_version="1.0", new_version="2.0"
    )


def _appcompat_html() -> str:
    full_diff = SimpleNamespace(
        verdict=Verdict.BREAKING,
        policy="strict_abi",
        old_metadata=SimpleNamespace(
            path="/old/lib.so", sha256="aa" * 32, size_bytes=4096
        ),
        new_metadata=SimpleNamespace(
            path="/new/lib.so", sha256="bb" * 32, size_bytes=8192
        ),
        confidence=SimpleNamespace(value="medium"),
        evidence_tiers=["elf", "header"],
        coverage_warnings=[],
    )
    result = SimpleNamespace(
        app_path="/bin/myapp",
        old_lib_path="/old/lib.so",
        new_lib_path="/new/lib.so",
        verdict=Verdict.BREAKING,
        symbol_coverage=95.0,
        required_symbol_count=20,
        missing_symbols=["foo", "bar"],
        missing_versions=["GLIBC_2.34"],
        breaking_for_app=[
            _change(
                "func_removed",
                "removed_func",
                "Public function removed",
                "removed_func",
            )
        ],
        irrelevant_for_app=[
            _change("func_added", "added_func", "Function added", "", "added_func")
        ],
        full_diff=full_diff,
    )
    return appcompat_to_html(result)


# ---------------------------------------------------------------------------
# Rich native-report cases (ADR-061 Phase 2's HTML compute/render split)
#
# ``main_report.html`` above pins only the minimal path: no severity gate, no
# confidence block, no impact table, no contract-stamped finding, no scoped
# verdict, no policy-file disclosure. Splitting ``html_report.py`` into
# ``compute_*``/``render_*`` halves needs every one of those sections under a
# byte-exact contract first, the same way Markdown's own split added
# ``tests/golden/review/`` and ``tests/golden/root_cause/`` before touching the
# code they pin. These two builders exist for that: between them they exercise
# every section ``generate_html_report`` can emit.
# ---------------------------------------------------------------------------


def _rich_changes() -> tuple[Change, Change, Change, Change, Change]:
    """Five findings covering every per-change rendering branch: impact
    (``affected_symbols``/``caused_count``), source location, cross-detector
    correlation, a contract-stamped finding, and a NOT_EVALUATED one."""
    removed = Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="_ZN3foo6removeEv",
        description="Public function removed",
        old_value="void foo::remove()",
        new_value="",
        source_location="include/foo.h:42",
        affected_symbols=["_ZN3foo1aEv", "_ZN3foo1bEv"],
        correlated_change_kind="type_vtable_changed",
        contract_relevance=ContractRelevance.IN_CONTRACT,
        contract_reason_code="in_export_table",
        contract_assurance=ContractAssurance.COMPLETE,
        contract_evidence_refs=("export_table:new", "public_header:old"),
        compatibility_evaluation_status=CompatibilityEvaluationStatus.EVALUATED,
        compatibility_decision=Verdict.BREAKING,
    )
    root = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED,
        symbol="foo::Widget",
        description="Struct size changed",
        old_value="16",
        new_value="24",
        # Six entries so the ">5 -> (+N more)" truncation branch renders.
        affected_symbols=[
            "_ZN3foo6WidgetC1Ev",
            "_ZN3foo6Widget3runEv",
            "_ZN3foo6Widget4stopEv",
            "_ZN3foo6Widget4nameEv",
            "_ZN3foo6Widget2idEv",
            "_ZN3foo6Widget5resetEv",
        ],
        caused_count=3,
    )
    added = Change(
        kind=ChangeKind.FUNC_ADDED,
        symbol="_ZN3foo3newEv",
        description="Function added",
        old_value="",
        new_value="void foo::new()",
    )
    suppressed = Change(
        kind=ChangeKind.VAR_REMOVED,
        symbol="_ZN3foo7hiddenVE",
        description="Variable removed (suppressed)",
        old_value="int foo::hiddenV",
    )
    not_evaluated = Change(
        kind=ChangeKind.FUNC_PARAMS_CHANGED,
        symbol="_ZN3foo7privateEi",
        description="Parameter type changed",
        old_value="int",
        new_value="long",
        contract_relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
        contract_reason_code="not_in_export_table",
        contract_assurance=ContractAssurance.COMPLETE,
        correlated_change_kind="type_vtable_changed",
        compatibility_evaluation_status=CompatibilityEvaluationStatus.NOT_EVALUATED,
    )
    return removed, root, added, suppressed, not_evaluated


def _rich_result(*, policy_file: PolicyFile | None = None) -> DiffResult:
    removed, root, added, suppressed, not_evaluated = _rich_changes()
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so",
        changes=[removed, root, added, not_evaluated],
        verdict=Verdict.BREAKING,
        suppressed_count=1,
        suppressed_changes=[suppressed],
        suppression_file_provided=True,
        policy="strict_abi",
        policy_file=policy_file,
        old_metadata=LibraryMetadata(
            path="/old/libfoo.so", sha256="aa" * 32, size_bytes=4096
        ),
        new_metadata=LibraryMetadata(
            path="/new/libfoo.so", sha256="bb" * 32, size_bytes=8192
        ),
        redundant_count=2,
        confidence=Confidence.MEDIUM,
        evidence_tiers=["elf", "dwarf", "header"],
        coverage_warnings=["no DWARF for libfoo.so"],
    )


def _main_report_rich_html() -> str:
    """Library-files table, confidence block, full CI-gate card (with its
    ``Blocked by:`` category line), summary/nav bars, every changes section,
    the suppressed section, the NOT_EVALUATED contract section, the redundancy
    note, and the impact table."""
    return generate_html_report(
        _rich_result(),
        lib_name="libfoo.so",
        old_version="1.0",
        new_version="2.0",
        old_symbol_count=120,
        show_impact=True,
        severity_config=SeverityConfig(),
    )


def _main_report_scoped_html() -> str:
    """The branches ``_main_report_rich_html`` cannot reach at the same time:
    the scoped-verdict box, the *scoped* CI-gate card (which renders a
    different title/note and deliberately no ``Blocked by:`` line), the
    policy-file overrides/reclassify disclosure rows, the ``--show-only``
    filter note, and the empty-suppressed-list fallback section."""
    policy_file = PolicyFile(
        overrides={ChangeKind.FUNC_ADDED: Verdict.COMPATIBLE_WITH_RISK},
        reclassify=[
            ReclassifyRule(
                to_verdict=Verdict.COMPATIBLE,
                symbol="_ZN3foo6removeEv",
                reason="vendored fork, not shipped",
                label="fork-only",
            )
        ],
    )
    result = _rich_result(policy_file=policy_file)
    # `--used-by`/`--required-symbol` scoping attaches these at the CLI layer
    # (they are read via getattr, not DiffResult fields).
    result.scoped_verdict = Verdict.API_BREAK
    result.scoped_exit_code = 2
    result.scoped_exit_code_scheme = "severity"
    result.suppressed_changes = []
    return generate_html_report(
        result,
        lib_name="libfoo.so",
        old_version="1.0",
        new_version="2.0",
        old_symbol_count=120,
        show_only="breaking",
        show_impact=True,
        severity_config=SeverityConfig(),
    )


def _stack_html() -> str:
    def _node(soname: str, depth: int, path: str, reason: str) -> object:
        return SimpleNamespace(
            soname=soname,
            depth=depth,
            path=path,
            needed=[],
            resolution_reason=reason,
        )

    root_key = "/bin/app"
    child_key = "/lib/libfoo.so"
    nodes = {
        root_key: _node("app", 0, root_key, "root"),
        child_key: _node("libfoo.so", 1, child_key, "DT_NEEDED"),
    }
    graph = SimpleNamespace(
        root=root_key,
        nodes=nodes,
        node_count=2,
        edges=[(root_key, child_key)],
        unresolved=[("/bin/app", "libmissing.so.1")],
    )
    binding = SimpleNamespace(
        consumer="/bin/myapp",
        symbol="main",
        version="",
        status=SimpleNamespace(value="bound"),
        explanation="",
    )
    missing = SimpleNamespace(
        consumer="/bin/myapp",
        symbol="missing_func",
        version="GLIBC_2.34",
        status=SimpleNamespace(value="missing"),
        explanation="not found",
    )
    stack_change = SimpleNamespace(
        library="libold.so", change_type="removed", abi_diff=None
    )
    result = StackCheckResult(
        root_binary="/bin/myapp",
        baseline_env="/baseline",
        candidate_env="/candidate",
        loadability=StackVerdict.FAIL,
        abi_risk=StackVerdict.WARN,
        baseline_graph=graph,
        candidate_graph=graph,
        bindings_baseline=[],
        bindings_candidate=[binding],
        missing_symbols=[missing],
        stack_changes=[stack_change],
        risk_score="high",
    )
    return stack_to_html(result)


_CASES = {
    "main_report.html": _main_report_html,
    "main_report_rich.html": _main_report_rich_html,
    "main_report_scoped.html": _main_report_scoped_html,
    "appcompat.html": _appcompat_html,
    "stack.html": _stack_html,
}


@pytest.mark.golden
@pytest.mark.parametrize("filename", sorted(_CASES))
def test_html_renderer_output_is_byte_identical(filename: str) -> None:
    expected = (_GOLDEN_DIR / filename).read_text(encoding="utf-8")
    actual = _CASES[filename]()
    assert actual == expected, (
        f"{filename} drifted from golden. If intentional, regenerate with "
        f"`python tests/test_html_template_golden.py`."
    )


def _generate() -> None:
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for filename, builder in _CASES.items():
        (_GOLDEN_DIR / filename).write_text(builder(), encoding="utf-8")
        print(f"wrote {_GOLDEN_DIR / filename}")


if __name__ == "__main__":
    _generate()

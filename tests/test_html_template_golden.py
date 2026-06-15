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
from abicheck.checker import Verdict
from abicheck.html_report import generate_html_report
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

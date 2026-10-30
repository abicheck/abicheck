"""Every ``CompareRequest`` field is plumbed into the configuration or excluded on purpose.

Bug class (``guard.request_field_not_plumbed``): ``compare_request_inputs``
silently dropped the four per-category severity fields, so a typed run with
``severity_abi_breaking="info"`` gated correctly but recorded
``built_in_default`` provenance in its gate receipt, while the CLI's
``severity.abi_breaking`` config recorded ``project_config``. A field that is
added to the request and never read by the adapter fails the same way, so the
coverage test below makes that omission a failure instead of a silent default.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import abicheck
from abicheck.report.diff_report import to_json
from abicheck.workflows.compare.execute import run_compare_request
from abicheck.workflows.compare_input_adapters import (
    compare_request_inputs,
    compatibility_config_from_compare_request,
)
from abicheck.workflows.request_inputs import InputSpec
from abicheck.workflows.service_requests import CompareRequest
from tests._abicheck_subprocess import abicheck_command

_PACKAGE_PARENT = Path(abicheck.__file__).resolve().parents[1]

# --------------------------------------------------------------------------
# Field coverage (fast, no binaries)
# --------------------------------------------------------------------------


def _attributes_read(tree: ast.AST, names: frozenset[str]) -> set[str]:
    """Attribute names read off a bare ``request``/``self`` name in *tree*."""
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in names
    }


#: Fields :func:`compare_request_inputs` reads, computed from its own source so
#: the set cannot drift from the code it describes.
ADAPTER_READS: frozenset[str] = frozenset(
    _attributes_read(
        ast.parse(textwrap.dedent(inspect.getsource(compare_request_inputs))),
        frozenset({"request"}),
    )
)

#: Request fields the adapter does not read, each with the module that does
#: read it and why the compatibility-evaluation configuration is not the place
#: it is resolved. The consumer named here is verified by the test below (it
#: must actually read ``request.<field>`` or ``self.<field>``), so an entry
#: cannot outlive the code that justified it.
EXCLUDED: dict[str, tuple[str, str]] = {
    "old": (
        "abicheck.workflows.compare.resolve",
        "input operand; resolved into snapshots before any configuration",
    ),
    "lang": (
        "abicheck.workflows.compare.resolve",
        "extraction language selector, not a compatibility setting",
    ),
    "lang_explicit": (
        "abicheck.workflows.compare.resolve",
        "extraction language selector, not a compatibility setting",
    ),
    "frontend": (
        "abicheck.workflows.compare.resolve",
        "extraction front-end label; the adapter states FrontEnd.API itself",
    ),
    "frontend_context": (
        "abicheck.service_compare_evidence",
        "evidence-collection context, not a compatibility setting",
    ),
    "has_sources": (
        "abicheck.workflows.service_requests",
        "legacy flag validated by CompareRequest.__post_init__ itself",
    ),
    "public_surface_allowlist": (
        "abicheck.workflows.compare_classify",
        "allowlist applied to the observed surface during classification",
    ),
    "pattern_verdicts": (
        "abicheck.workflows.compare_classify",
        "pattern verdicts applied to observed changes during classification",
    ),
    "enable_debuginfod": (
        "abicheck.workflows.compare.resolve",
        "debug-info fetch switch for extraction",
    ),
    "debuginfod_url": (
        "abicheck.workflows.compare.resolve",
        "debug-info fetch endpoint for extraction",
    ),
    "env_matrix": (
        "abicheck.workflows.compare_classify",
        "build-environment matrix audit input, not a resolver input",
    ),
    "diagnostic_comparison": (
        "abicheck.workflows.compare_classify",
        "diagnostic-mode switch for the run, not configuration",
    ),
    "require_same_generation": (
        "abicheck.workflows.compare_classify",
        "generation check applied to the pair during classification",
    ),
    "contract_evaluation": (
        "abicheck.workflows.compare_classify",
        "switches contract evaluation on; the mode itself is contract_mode",
    ),
    "depth": (
        "abicheck.workflows.compare.resolve",
        "extraction depth, resolved into the plan rather than the gate",
    ),
    "config_overrides": (
        "abicheck.workflows.compare.execute",
        "ADR-091 D4 config overrides (compare --set); resolved into the request's own"
        " fields by _with_config_overrides before classification",
    ),
    "budget_s": (
        "abicheck.workflows.compare.execute",
        "execution time budget",
    ),
    "dwarf_only": (
        "abicheck.workflows.compare.resolve",
        "extraction mode",
    ),
    "debug_format": (
        "abicheck.service_compare_evidence",
        "debug-info format selector for evidence collection",
    ),
    "include_labels": (
        "abicheck.workflows.compare.resolve",
        "extraction label selector",
    ),
    "follow_dependencies": (
        "abicheck.dependency_info",
        "dependency-resolution input for evidence collection",
    ),
    "dependency_search_paths": (
        "abicheck.dependency_info",
        "dependency-resolution input for evidence collection",
    ),
    "ld_library_path": (
        "abicheck.dependency_info",
        "dependency-resolution input for evidence collection",
    ),
    "pack_policy_overrides": (
        "abicheck.workflows.compare_request_policy",
        "folded into the policy file before resolution (resolve_request_policy_inputs); "
        "its receipt entry comes from _with_pack_forwarded_provenance",
    ),
    "project_policy_overrides": (
        "abicheck.workflows.compare_request_policy",
        "folded at the weakest tier by fold_project_policy_overrides; "
        "its receipt entry comes from the project inputs in compare_gate_receipt",
    ),
    "pack_internal_namespaces": (
        "abicheck.workflows.compare_request_policy",
        "folded into the policy file via policy_file_with_packs before resolution",
    ),
    "acknowledgments_path": (
        "abicheck.workflows.compare_request_policy",
        "acknowledgment records, resolved by resolve_compare_acknowledgments",
    ),
    "acknowledgment_unacknowledged_additions": (
        "abicheck.workflows.compare_request_policy",
        "acknowledgment setting, resolved by resolve_compare_acknowledgments",
    ),
    "acknowledgment_breaks": (
        "abicheck.workflows.compare_request_policy",
        "acknowledgment setting, resolved by resolve_compare_acknowledgments",
    ),
    "strict_suppressions": (
        "abicheck.workflows.compare.execute",
        "suppression-audit strictness applied after classification",
    ),
    "require_justification": (
        "abicheck.workflows.compare.execute",
        "enforced when the --suppress list is loaded; SuppressionSource does not carry it",
    ),
    "old_probe_matrix": (
        "abicheck.workflows.compare_classify",
        "build-configuration matrix joined to the findings during classification",
    ),
    "new_probe_matrix": (
        "abicheck.workflows.compare_classify",
        "build-configuration matrix joined to the findings during classification",
    ),
    "require_complete_analysis": (
        "abicheck.workflows.compare_finalize",
        "exit-gate axis applied by effective_gate_for_compare at finalize",
    ),
    "changed_paths": (
        "abicheck.service_compare_evidence",
        "changed-path scoping seed for evidence collection",
    ),
    "abi3_floor": (
        "abicheck.workflows.compare_classify",
        "stable-ABI audit floor applied during classification",
    ),
    "collapse_versioned_symbols": (
        "abicheck.workflows.compare_classify",
        "applied to the snapshot diff during classification",
    ),
    "resolved_collect_mode": (
        "abicheck.service_compare_evidence",
        "evidence collection mode, resolved before comparison",
    ),
    "allow_build_query": (
        "abicheck.workflows.compare.resolve",
        "permission for build-data queries during extraction",
    ),
    "performance_profile": (
        "abicheck.workflows.compare.resolve",
        "extraction performance profile",
    ),
}

_REQUEST_FIELDS: frozenset[str] = frozenset(
    f.name for f in dataclasses.fields(CompareRequest)
)


def test_every_request_field_is_plumbed_or_excluded():
    unclassified = sorted(_REQUEST_FIELDS - ADAPTER_READS - EXCLUDED.keys())
    assert not unclassified, (
        f"CompareRequest fields not read by compare_request_inputs and not in "
        f"EXCLUDED: {unclassified}. Forward each into ExplicitCompatibilityInputs, "
        "or add it to EXCLUDED with the module that consumes it and why."
    )


def test_exclusions_are_real_unplumbed_fields_with_verified_consumers():
    assert EXCLUDED.keys() <= _REQUEST_FIELDS, sorted(EXCLUDED.keys() - _REQUEST_FIELDS)
    assert not (EXCLUDED.keys() & ADAPTER_READS), sorted(
        EXCLUDED.keys() & ADAPTER_READS
    )
    for field, (module_name, reason) in EXCLUDED.items():
        assert reason.strip(), f"{field}: exclusion needs a justification"
        module = importlib.import_module(module_name)
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        assert field in _attributes_read(tree, frozenset({"request", "self"})), (
            f"{field}: {module_name} no longer reads it; drop the exclusion "
            "or name the module that does"
        )


def test_adapter_reads_the_four_severity_categories():
    # Guards the bug this file exists for: the four per-category fields must be
    # read by the adapter, not only by compare_finalize's exit path.
    categories = {
        "severity_abi_breaking",
        "severity_potential_breaking",
        "severity_quality_issues",
        "severity_addition",
    }
    assert categories <= ADAPTER_READS, sorted(categories - ADAPTER_READS)


SEVERITY_CATEGORY_FIELDS = (
    "severity_abi_breaking",
    "severity_potential_breaking",
    "severity_quality_issues",
    "severity_addition",
)


@pytest.mark.parametrize("field", SEVERITY_CATEGORY_FIELDS)
def test_stated_category_reaches_explicit_inputs_and_receipt(field, tmp_path):
    request = CompareRequest(
        old=InputSpec(path=tmp_path / "old.so"),
        new=InputSpec(path=tmp_path / "new.so"),
        **{field: "info"},
    )
    assert getattr(compare_request_inputs(request), field) == "info"

    config = compatibility_config_from_compare_request(request)
    provenance = config.provenance[f"gate.severity.{field.removeprefix('severity_')}"]
    assert provenance.layer.value == "api_request"
    assert provenance.source_kind == "severity_override"


# --------------------------------------------------------------------------
# Parity: typed API vs CLI config, per severity category (real binaries)
# --------------------------------------------------------------------------

#: category -> (v1 source, v2 source, extra v2 link flags). Each pair produces
#: one finding in the category it names, verified against the real classifier.
_CASES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "removed": (
        "int keep_me(int x) { return x + 1; }\nint gone(int x) { return x * 2; }\n",
        "int keep_me(int x) { return x + 1; }\n",
        (),
    ),
    "added": (
        "int keep_me(int x) { return x + 1; }\n",
        "int keep_me(int x) { return x + 1; }\nint fresh(int x) { return x * 3; }\n",
        (),
    ),
    "needed": (
        "int keep_me(int x) { return x + 1; }\n",
        "#include <math.h>\nint keep_me(int x) { return (int)sin((double)x) + x + 1; }\n",
        ("-lm",),
    ),
    "protected": (
        "int keep_me(int x) { return x + 1; }\n",
        '__attribute__((visibility("protected"))) int keep_me(int x) { return x + 1; }\n',
        (),
    ),
}

#: severity category -> the fixture case that produces a finding in it.
CATEGORY_CASE: dict[str, str] = {
    "abi_breaking": "removed",
    "addition": "added",
    "quality_issues": "needed",
    "potential_breaking": "protected",
}

#: Exit codes a severity-aware run returns for each category at ``error``.
_EXPECTED_ERROR_EXIT: dict[str, int] = {
    "abi_breaking": 4,
    "addition": 1,
    "quality_issues": 1,
    "potential_breaking": 2,
}


def _build_pair(root: Path, case: str) -> tuple[Path, Path]:
    v1_src, v2_src, v2_flags = _CASES[case]
    root.mkdir(parents=True, exist_ok=True)
    libs: list[Path] = []
    for tag, src, flags in (("1", v1_src, ()), ("2", v2_src, v2_flags)):
        c_file = root / f"v{tag}.c"
        c_file.write_text(src, encoding="utf-8")
        so = root / f"libfoo.so.{tag}"
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                "-g",
                "-O0",
                "-o",
                str(so),
                str(c_file),
                *flags,
            ],
            check=True,
        )
        libs.append(so)
    return libs[0], libs[1]


@pytest.fixture(scope="module")
def case_libs(tmp_path_factory) -> dict[str, tuple[Path, Path]]:
    root = tmp_path_factory.mktemp("severity_parity")
    return {case: _build_pair(root / case, case) for case in _CASES}


def _gate_provenance(doc: dict, category: str) -> tuple[str, str | None]:
    entry = doc["contract_context"]["evaluation_context"]["field_provenance"][
        f"gate.severity.{category}"
    ]
    return entry["layer"], entry.get("source_kind")


def _typed_run(old: Path, new: Path, category: str, level: str) -> tuple[int, tuple]:
    request = CompareRequest(
        old=InputSpec(path=old),
        new=InputSpec(path=new),
        contract_evaluation=True,
        contract_mode="all",
        **{f"severity_{category}": level},
    )
    result = run_compare_request(request)
    doc = json.loads(
        to_json(
            result.diff,
            severity_config=result.severity_config,
            contract_evaluation=True,
        )
    )
    return result.exit_decision.code, _gate_provenance(doc, category)


def _cli_config_run(
    old: Path, new: Path, category: str, level: str, workdir: Path
) -> tuple[int, tuple]:
    config = workdir / f"abicheck-{category}-{level}.yml"
    config.write_text(f"severity:\n  {category}: {level}\n", encoding="utf-8")
    report = workdir / f"cli-{category}-{level}.json"
    env = {**os.environ, "PYTHONPATH": str(_PACKAGE_PARENT)}
    proc = subprocess.run(
        abicheck_command(
            "compare",
            str(old),
            str(new),
            "--config",
            str(config),
            "--contract",
            "all",
            "-o",
            f"json={report}",
        ),
        capture_output=True,
        text=True,
        env=env,
    )
    doc = json.loads(report.read_text(encoding="utf-8"))
    return proc.returncode, _gate_provenance(doc, category)


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="builds an ELF .so")
@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not found")
@pytest.mark.parametrize("category", sorted(CATEGORY_CASE))
def test_typed_api_and_cli_config_agree_on_exit_and_provenance(
    category, case_libs, tmp_path
):
    old, new = case_libs[CATEGORY_CASE[category]]
    exit_by_level: dict[str, int] = {}
    for level in ("error", "info"):
        api_exit, api_prov = _typed_run(old, new, category, level)
        cli_exit, cli_prov = _cli_config_run(old, new, category, level, tmp_path)

        assert api_exit == cli_exit, (category, level, api_exit, cli_exit)
        # Same provenance kind: both routes state the level explicitly
        # (never built_in_default). The tier differs by front end by design:
        # the typed request is API_REQUEST, the CLI's config file is
        # PROJECT_CONFIG; both carry the severity_override source kind.
        assert api_prov == ("api_request", "severity_override"), (
            category,
            level,
            api_prov,
        )
        assert cli_prov == ("project_config", "severity_override"), (
            category,
            level,
            cli_prov,
        )
        exit_by_level[level] = api_exit

    # Non-vacuous: the category is actually gated at error and not at info.
    assert exit_by_level["error"] == _EXPECTED_ERROR_EXIT[category]
    assert exit_by_level["info"] == 0

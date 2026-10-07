"""ADR-049 Phase 7's evidence-adaptive default contract domain.

Each rule is checked against an independently written oracle table over its
whole (small) input domain, not against the helper under test.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.compatibility_evaluation_config import ValueProvenance
from abicheck.contract_relevance_types import ContractMode, SelectorLayer
from abicheck.policy.contract_default_mode import (
    evidence_adaptive_contract_mode,
    is_evidence_adaptive,
    observed_export_fallback_applies,
)

#: (public_header_evidence, export_evidence) -> (mode, reference)
_ADAPTIVE_ORACLE = {
    (True, True): ("public", "public_header_evidence"),
    (True, False): ("public", "public_header_evidence"),
    (False, True): ("exports", "export_table_evidence"),
    (False, False): ("all", "no_contract_evidence"),
}


@pytest.mark.parametrize(("header", "exports"), sorted(_ADAPTIVE_ORACLE))
def test_adaptive_mode_matches_the_oracle(header: bool, exports: bool) -> None:
    mode, prov = evidence_adaptive_contract_mode(
        public_header_evidence=header, export_evidence=exports
    )
    assert (mode.value, prov.reference) == _ADAPTIVE_ORACLE[(header, exports)]
    assert prov.layer is SelectorLayer.BUILT_IN_DEFAULT
    assert is_evidence_adaptive(prov)


def test_more_evidence_never_widens_the_domain() -> None:
    """Monotonic: adding an evidence kind can only narrow the contract."""
    width = {"public": 0, "exports": 1, "all": 2}
    for h, e in _ADAPTIVE_ORACLE:
        here = width[_ADAPTIVE_ORACLE[(h, e)][0]]
        for h2, e2 in ((True, e), (h, True)):
            assert width[_ADAPTIVE_ORACLE[(h2, e2)][0]] <= here
            mode, _ = evidence_adaptive_contract_mode(
                public_header_evidence=h2, export_evidence=e2
            )
            assert width[mode.value] <= here


_PROVENANCES = {
    "adaptive": ValueProvenance(
        layer=SelectorLayer.BUILT_IN_DEFAULT,
        source_kind="evidence_adaptive",
        reference="public_header_evidence",
    ),
    "stated_cli": ValueProvenance(
        layer=SelectorLayer.EXPLICIT_CLI, source_kind="cli_flag", reference="--contract"
    ),
    "legacy": ValueProvenance(
        layer=SelectorLayer.LEGACY_ALIAS, source_kind="legacy_scope_flag"
    ),
    "none": None,
}
_REASONS = (
    "closed_domain_no_commitment",
    "required_evidence_incomplete",
    "identity_ambiguous",
    "public_root_membership",
    "terminal_authoritative_exclusion",
    None,
)


#: Header decisions that commit to nothing either way (written from the rule's
#: statement, not imported from the module under test).
_HEADER_SILENT = {
    "closed_domain_no_commitment",
    "required_evidence_incomplete",
    "identity_ambiguous",
}


def test_export_fallback_applies_only_to_an_unstated_public_no_commitment() -> None:
    """Exhaustive over mode x provenance x reason: a stated domain is never
    second-guessed, and only a header decision that commits to nothing --
    no commitment found, an unclosable domain, an unpinned identity -- is
    re-judged; an authoritative exclusion or a membership is not."""
    for mode, prov_key, reason in itertools.product(
        ContractMode, _PROVENANCES, _REASONS
    ):
        expected = (
            mode is ContractMode.PUBLIC
            and prov_key == "adaptive"
            and reason in _HEADER_SILENT
        )
        got = observed_export_fallback_applies(mode, _PROVENANCES[prov_key], reason)
        assert got is expected, (mode, prov_key, reason)


_CASE182 = (
    Path(__file__).resolve().parent.parent
    / "catalog"
    / "cases"
    / "case182_accidental_export_removed_still_breaking"
)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("contract_args", "expected_exit"),
    [
        ([], 4),  # evidence-adaptive public + observed-export fallback
        (["--contract", "exports"], 4),  # unmatched export is still a root
        (["--contract", "all"], 4),
        (["--contract", "public"], 0),  # stated: headers are the whole promise
    ],
)
def test_undeclared_export_removal_per_domain(
    tmp_path: Path, contract_args: list[str], expected_exit: int
) -> None:
    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    for name in ("v1.c", "v2.c", "v1.h", "v2.h"):
        shutil.copy(_CASE182 / name, tmp_path / name)
    for ver in ("v1", "v2"):
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                "-g",
                str(tmp_path / f"{ver}.c"),
                "-o",
                str(tmp_path / f"lib{ver}.so"),
            ],
            check=True,
            capture_output=True,
        )
    argv = [
        "compare",
        str(tmp_path / "libv1.so"),
        str(tmp_path / "libv2.so"),
        "--header",
        f"old={tmp_path / 'v1.h'}",
        "--header",
        f"new={tmp_path / 'v2.h'}",
        *contract_args,
    ]
    result = CliRunner().invoke(main, argv)
    assert result.exit_code == expected_exit, result.output

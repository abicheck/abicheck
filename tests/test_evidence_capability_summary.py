from abicheck.buildsource.evidence_report import LayerCoverage, capability_lines
from abicheck.buildsource.model import CoverageStatus


def test_binary_and_headers_do_not_claim_signatures_or_vtables_are_off() -> None:
    coverage = [
        LayerCoverage("L0", CoverageStatus.PRESENT, detail="binary"),
        LayerCoverage("L2", CoverageStatus.PRESENT, detail="headers"),
    ]
    text = "\n".join(capability_lines(coverage, []))
    assert "[on]  Public declarations, signatures, and declared virtual sequence" in text
    assert "[on]  Exports, linkage, SONAME, and emitted ELF vtable-group sizes" in text
    assert "[off] Debug-derived compiled type layout" in text

from abicheck.buildsource.evidence_report import LayerCoverage, capability_lines
from abicheck.buildsource.model import CoverageStatus


def test_binary_and_headers_do_not_claim_signatures_or_vtables_are_off() -> None:
    coverage = [
        LayerCoverage("L0", CoverageStatus.PRESENT, detail="binary"),
        LayerCoverage("L2", CoverageStatus.PRESENT, detail="headers"),
    ]
    text = "\n".join(capability_lines(coverage, []))
    assert (
        "[on]  Public declarations, signatures, and declared virtual sequence" in text
    )
    assert "[on]  Exports, linkage, SONAME, and emitted ELF vtable-group sizes" in text
    assert "[not requested] Debug-derived compiled type layout" in text


def test_partial_and_failed_evidence_are_not_rendered_as_absence() -> None:
    coverage = [
        LayerCoverage("L1", CoverageStatus.NOT_COLLECTED, detail="extractor failed"),
        LayerCoverage("L2", CoverageStatus.PARTIAL, detail="one header failed"),
    ]
    text = "\n".join(capability_lines(coverage, []))
    assert "[failed] Debug-derived compiled type layout" in text
    assert "[partial] Public declarations, signatures" in text

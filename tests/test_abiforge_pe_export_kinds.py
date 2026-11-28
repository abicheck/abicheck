from pathlib import Path

from abicheck.pe_metadata import parse_pe_metadata
from abiforge.run.pe_export_kinds import build_cases


def test_code_data_and_forwarder_preserve_loader_identity(tmp_path):
    dll = Path(__file__).parent / "fixtures/containers/sources/corpus_pe.dll"
    cases = build_cases(dll, tmp_path, b"pe_corpus_close", b"pe_corpus_version")
    observed = {}
    for name, path in cases.items():
        (export,) = [
            e for e in parse_pe_metadata(path).exports if e.name == "pe_corpus_close"
        ]
        observed[name] = export.ordinal, export.sym_type.value, export.forwarder
    assert observed == {
        "code": (1, "exported", ""),
        "data": (1, "data", ""),
        "forwarder": (1, "forwarded", "NTDLL.Sleep"),
    }

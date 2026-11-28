"""Guard against partial or stale release evidence being scored as complete."""

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abiforge.run import onedal_exports, onedal_pe_exports


def test_product_export_score(tmp_path, monkeypatch):
    old = tmp_path / "libdal.so.3"
    new = tmp_path / "libdal.so.4"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    ledger = tmp_path / "rows.csv.gz"
    with gzip.open(ledger, "wt") as handle:
        handle.write("symbol,providers\nkeep,libdal.so.3\nlost,libdal.so.3\n")
    report = tmp_path / "report.json"
    data = {
        f"{side}_file": {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for side, path in (("old", old), ("new", new))
    }
    data["changes"] = [{"kind": "func_removed", "symbol": "keep"}]
    report.write_text(json.dumps(data))
    monkeypatch.setattr(
        onedal_exports, "symbols", lambda p: {"keep", "lost"} if p == old else {"keep"}
    )
    row = onedal_exports.score(tmp_path, tmp_path, [report], ledger)[old.name]
    assert row["missed"] == ["lost"]
    assert row["false_positives"] == ["keep"]
    with pytest.raises(ValueError, match="missing ledger providers"):
        onedal_exports.score(tmp_path, tmp_path, [], ledger)
    new.write_bytes(b"replacement")
    with pytest.raises(ValueError, match="digest differs"):
        onedal_exports.score(tmp_path, tmp_path, [report], ledger)


def test_forwarded_pe_export(tmp_path):
    import pefile

    reader = shutil.which("objdump")
    if reader is None:
        pytest.skip("GNU objdump required")
    fixture = Path(__file__).parent / "fixtures/containers/sources/corpus_pe.dll"
    with pefile.PE(str(fixture)) as pe:
        exports = pe.DIRECTORY_ENTRY_EXPORT.struct
        # Reuse the DLL-name storage within the export directory for a forwarder.
        pe.set_dword_at_rva(exports.AddressOfFunctions, exports.Name)
        pe.set_bytes_at_rva(exports.Name, b"NTDLL.Sleep\0")
        dll = tmp_path / "forwarded.dll"
        pe.write(str(dll))
    result = onedal_pe_exports.audit(dll, reader)
    assert result["exports"] == 3
    assert result["forwarded"] == 1
    assert result["missing"] == result["unexpected"] == []


@pytest.mark.parametrize(
    "baseline,candidate,missing_count",
    [
        ("2025.9.0", "2025.11.0", 0),
        ("2025.11.0", "2025.9.0", 1),
        ("2025.9.0", "2026.2.0", 174),
        ("2025.11.0", "2026.2.0", 174),
        ("2026.2.0", "2025.9.0", 89),
        ("2026.2.0", "2025.11.0", 88),
    ],
)
def test_pinned_pe_consumer_comparison(tmp_path, baseline, candidate, missing_count):
    root = os.environ.get("ONEDAL_WINDOWS_RELEASES")
    if not root:
        pytest.skip("set ONEDAL_WINDOWS_RELEASES to the pinned Windows DLL tree")
    product = Path(__file__).resolve().parents[1] / "research/onedal-coverage/product"
    receipt = json.loads((product / "pe-exports.json").read_text())
    old = next((Path(root) / baseline).glob("onedal_core.*.dll"))
    new = next((Path(root) / candidate).glob("onedal_core.*.dll"))
    clients = [
        old.with_name(old.name.replace("onedal_core", stem))
        for stem in ("onedal", "onedal_dpc", "onedal_thread")
    ]
    for path in [old, new, *clients]:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        assert digest == receipt[f"{path.parent.name}/{path.name}"]["sha256"]

    def dump(path):
        return subprocess.check_output(["objdump", "-p", path], text=True)

    entries = onedal_pe_exports.export_entries(dump(new))
    assert entries.total() == receipt[f"{candidate}/{new.name}"]["exports"]
    exports = {name for name, _, _ in entries}
    expected = {}
    for client in clients:
        imports = onedal_pe_exports.named_imports(dump(client)).get(old.name, [])
        counts = receipt[f"{baseline}/{client.name}"]["named_imports"]
        assert len(imports) == counts.get(old.name, 0)
        missing = sorted(set(imports) - exports)
        assert len(missing) == (missing_count if imports else 0)
        verdict = "NO_CHANGE"
        if imports:
            verdict = "BREAKING" if missing else "COMPATIBLE"
        expected[client.name] = (len(imports), missing, verdict)

    report = tmp_path / "compare.json"
    command = [sys.executable, "-m", "abicheck", "compare", str(old), str(new)]
    for client in clients:
        command.extend(["--used-by", str(client)])
    result = subprocess.run([*command, "-o", f"json={report}"], check=False)
    assert result.returncode == (4 if missing_count else 1)
    data = json.loads(report.read_text())
    assert data["verdict"] == ("BREAKING" if missing_count else "UNCONFIRMED")
    assert len(data["used_by"]) == len(expected)
    assert {
        Path(row["app"]).name: (
            row["required_symbol_count"],
            sorted(row["missing_symbols"]),
            row["verdict"],
        )
        for row in data["used_by"]
    } == expected


def test_windows_release_receipt_requires_complete_export_evidence():
    product = Path(__file__).resolve().parents[1] / "research/onedal-coverage/product"
    receipt = json.loads((product / "pe-exports.json").read_text())
    onedal_pe_exports.validate_release_receipt(receipt)
    key = "2025.11.0/onedal_core.3.dll"
    incomplete = receipt.copy()
    del incomplete[key]
    with pytest.raises(ValueError, match="missing=.*onedal_core"):
        onedal_pe_exports.validate_release_receipt(incomplete)
    with pytest.raises(ValueError, match="unexpected=.*onedal_extra"):
        onedal_pe_exports.validate_release_receipt({**receipt, "onedal_extra.dll": {}})
    with pytest.raises(ValueError, match="unresolved export mismatch"):
        onedal_pe_exports.validate_release_receipt(
            {**receipt, key: {**receipt[key], "missing": [["example", 1, ""]]}}
        )


def test_pinned_linux_inventory_requires_all_release_members_and_digests():
    from abiforge.run.onedal_elf_extraction import verify_inventory

    receipt = {
        "first": {"a.so": {"sha256": "a"}, "b.so": {"sha256": "b"}},
        "second": {"a.so": {"sha256": "c"}},
    }
    verify_inventory(receipt, receipt)
    with pytest.raises(ValueError, match="missing=.*second"):
        verify_inventory({"first": receipt["first"]}, receipt)
    with pytest.raises(ValueError, match="missing=.*b.so"):
        verify_inventory({**receipt, "first": {"a.so": {"sha256": "a"}}}, receipt)
    with pytest.raises(ValueError, match="unexpected=.*extra"):
        verify_inventory({**receipt, "extra": receipt["second"]}, receipt)
    with pytest.raises(ValueError, match="digest differs"):
        verify_inventory(
            {**receipt, "second": {"a.so": {"sha256": "replacement"}}}, receipt
        )

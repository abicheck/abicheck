"""Guard against partial or stale release evidence being scored as complete."""

import gzip
import hashlib
import json

import pytest

from abiforge.run import onedal_exports


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

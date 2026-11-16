"""TEMPORARY macOS diagnostic for the dSYM read path. Remove after the cause is fixed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS diagnostic")
def test_macho_dsym_diagnostic(tmp_path: Path) -> None:
    lines: list[str] = []
    src = tmp_path / "p.c"
    src.write_text("int foo(int x) { return x + 1; }\n")
    lib = tmp_path / "libp.dylib"
    subprocess.run(["clang", "-g", "-dynamiclib", "-o", str(lib), str(src)], check=True)
    dsym = Path(f"{lib}.dSYM")
    subprocess.run(["dsymutil", str(lib), "-o", str(dsym)], check=True)
    lines.append(
        f"dsym tree: {sorted(str(p.relative_to(dsym)) for p in dsym.rglob('*') if p.is_file())}"
    )

    from abicheck.dwarf_unified import (
        _adjacent_dsym_binary,
        _open_dwarf_session_with_status,
    )
    from abicheck.extract.debug_dump import _is_macho64
    from abicheck.extract.macho_dsym import macho_uuid, read_dsym_dwarf

    lines.append(f"magic={lib.read_bytes()[:4].hex()} is_macho64={_is_macho64(lib)}")
    uuid = macho_uuid(lib)
    lines.append(f"dylib uuid={uuid}")
    bundle = _adjacent_dsym_binary(lib)
    lines.append(
        f"adjacent bundle path={bundle} exists={bool(bundle and bundle.exists())}"
    )
    if bundle is not None:
        r = read_dsym_dwarf(bundle, dylib_uuid=uuid)
        if hasattr(r, "dwarf"):
            lines.append(
                f"read_dsym_dwarf OK uuid={r.uuid} CUs={len(list(r.dwarf.iter_CUs()))}"
            )
        else:
            lines.append(f"read_dsym_dwarf REFUSED: {r.reason}")
    session, status = _open_dwarf_session_with_status(lib)
    lines.append(f"open_dwarf_session status={status} session={session is not None}")
    try:
        from abicheck.dumper import dump

        snap = dump(lib, [], version="1")
        lines.append(f"dump OK functions={len(snap.functions)} types={len(snap.types)}")
        lines.append(f"dump_provenance={snap.dump_provenance!r}"[:600])
    except Exception as exc:  # diagnostic: show the failure, do not hide it
        lines.append(f"dump RAISED {type(exc).__name__}: {exc}")
    pytest.fail("\n".join(lines))

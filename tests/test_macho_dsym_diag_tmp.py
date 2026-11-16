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
    obj = tmp_path / "p.o"
    subprocess.run(["clang", "-g", "-c", "-o", str(obj), str(src)], check=True)
    subprocess.run(["clang", "-dynamiclib", "-o", str(lib), str(obj)], check=True)
    dsym = Path(f"{lib}.dSYM")
    subprocess.run(["dsymutil", str(lib), "-o", str(dsym)], check=True)
    lines.append(
        f"dsym tree: {sorted(str(p.relative_to(dsym)) for p in dsym.rglob('*') if p.is_file())}"
    )

    import struct

    from abicheck.dwarf_unified import (
        _adjacent_dsym_binary,
        _open_dwarf_session_with_status,
    )
    from abicheck.extract.debug_dump import _is_macho64
    from abicheck.extract.macho_dsym import macho_uuid, read_dsym_dwarf

    dsym_file = dsym / "Contents" / "Resources" / "DWARF" / lib.name
    raw = dsym_file.read_bytes()
    ncmds = struct.unpack_from("<I", raw, 16)[0]
    off = 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", raw, off)
        if cmd == 0x19:
            seg = raw[off + 8 : off + 24].rstrip(b"\0").decode("ascii", "replace")
            nsects = struct.unpack_from("<I", raw, off + 64)[0]
            secs = []
            for i in range(nsects):
                s0 = off + 72 + i * 80
                secs.append(
                    (
                        raw[s0 : s0 + 16].rstrip(b"\0").decode("ascii", "replace"),
                        raw[s0 + 16 : s0 + 32].rstrip(b"\0").decode("ascii", "replace"),
                    )
                )
            lines.append(
                f"RAW segment {seg!r} nsects={nsects} sections(sect,seg)={secs}"
            )
        if cmd != 0x19:
            lines.append(f"RAW lc cmd=0x{cmd:x} size={cmdsize}")
        off += cmdsize
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
        lines.append(f"dump OK functions={len(snap.declarations.functions)} ")
        lines.append(f"dump_provenance={snap.dump_provenance!r}"[:600])
    except Exception as exc:  # diagnostic: show the failure, do not hide it
        lines.append(f"dump RAISED {type(exc).__name__}: {exc}")
    pytest.fail("\n".join(lines))

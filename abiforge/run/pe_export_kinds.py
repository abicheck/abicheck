"""Retarget a named PE export while preserving its name, ordinal and consumers."""

from pathlib import Path

import pefile


def build_cases(
    source: Path, root: Path, export: bytes, data: bytes
) -> dict[str, Path]:
    """Use a fixture with a code export, a data export and spare DLL-name storage."""
    paths = {}
    for kind in ("code", "data", "forwarder"):
        with pefile.PE(str(source)) as image:
            directory = image.DIRECTORY_ENTRY_EXPORT
            by_name = {symbol.name: symbol for symbol in directory.symbols}
            slot = directory.struct.AddressOfFunctions + 4 * (
                by_name[export].ordinal - directory.struct.Base
            )
            if kind == "data":
                image.set_dword_at_rva(slot, by_name[data].address)
            elif kind == "forwarder":
                rva = directory.struct.Name
                target = b"NTDLL.Sleep\0"
                if len(image.get_string_at_rva(rva)) + 1 < len(target):
                    raise ValueError("DLL-name storage is too short for the forwarder")
                image.set_bytes_at_rva(rva, target)
                image.set_dword_at_rva(slot, rva)
            paths[kind] = root / f"{kind}.dll"
            image.write(str(paths[kind]))
    return paths

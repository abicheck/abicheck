### Fixed

- **An edited PDB is no longer served from a stale cached PE snapshot** — a PE dump auto-discovers the PDB beside the image (its CodeView path, then `<name>.pdb`) and attaches its debug info, but the whole-snapshot cache key hashed only the DLL, so replacing the PDB kept serving the earlier snapshot's debug data. The discovered PDB is now content-hashed into the key like a header; a DLL with no PDB keeps its existing key.

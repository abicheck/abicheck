### Documentation

- **`--depth binary` is now documented as reading debug info.** The rung was
  described as "symbols only" / "debug-info presence, no deep DWARF type
  walk", but `compare --depth binary` has always compared the DWARF (or
  PDB/BTF/CTF) types a binary carries, so a struct layout break is caught
  with no headers. The evidence-depth guide, the detectability page and the
  `--depth` help text on `compare` and `dump` now describe that behaviour.

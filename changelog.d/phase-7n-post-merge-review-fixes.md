### Fixed

- **A ZIP package carrying a permitted preamble is recognised again.** The
  format allows arbitrary bytes before the first local file header — a
  self-extracting stub — and every real zip reader accepts one, but Phase
  7n's content detection checked for the magic at byte 0 and rejected such
  wheels and conda packages outright. Detection now asks
  `zipfile.is_zipfile`, which locates the central directory from the end of
  the file.
- **A debug artifact that no extraction path can read is refused with
  accurate advice.** `--debug-info`'s rejection of a directly-named `.pdb`
  or DWARF-package file previously suggested passing the directory holding
  it, which is dropped just as silently (`_dump_pe` never receives
  `debug_roots`, and the ELF dump reads only `DebugArtifact.dwarf_path`).
  It now names `.abicheck.yml`'s `debug.pdb_path` for a PDB, and says
  plainly that no input feeds a DWARF package to the dump path today. The
  underlying gap is recorded in `docs/contribute/known-gaps.md`.
- **Legacy-conda detection no longer reads a whole archive to decide.**
  Since the detector stopped gating on the filename it runs for every
  tar-shaped operand, and it listed every member before inspecting the
  first few — so a large or adversarial archive could be fully walked
  before the extraction limits applied. It stops at a bounded number of
  members now.
- **The GitHub Action forwards package-only inputs for a
  content-detected release operand.** `action/run.sh` decided whether an
  operand was a package from a filename-suffix table, which stopped
  matching `abicheck`'s own `is_package()` when that became content-based:
  a tar, conda or wheel archive staged under a nonconventional name was
  compared as a release by the CLI while the Action withheld `devel-pkg1`/
  `devel-pkg2` and `debug-info1`/`debug-info2`, quietly lowering the
  evidence the comparison ran on. The Action now asks the installed
  `abicheck`, keeping the table only for the genuinely pre-install
  `validate-inputs.sh` position.

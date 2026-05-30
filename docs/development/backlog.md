# Backlog

Near-term hardening items that are scoped but not yet implemented. This list
is deliberately small and concrete — strategic / architectural ideas live in
[Goals](goals.md), not here.

## MSVC + PDB end-to-end CI

**Status:** In progress — experimental lane landed (non-blocking).

abicheck implements PDB parsing (`pdb_parser.py`, `pdb_metadata.py`,
`pdb_utils.py`) and PE/COFF metadata extraction (`pe_metadata.py`), and
`service.resolve_input(..., pdb_path=...)` feeds PDB struct/enum layout into the
same `DwarfMetadata` pipeline the ELF/DWARF path uses. The Windows *unit-test*
lane still uses MinGW/GCC, but there is now a dedicated end-to-end lane against
a real Microsoft toolchain:

- **`windows-msvc` CI lane** (`.github/workflows/ci.yml`) on the GitHub-hosted
  `windows-latest` runner. `ilammy/msvc-dev-cmd` puts `cl.exe` on PATH; the lane
  runs `pytest -m msvc`.
- **`tests/test_msvc_pdb_e2e.py`** compiles a DLL with `cl.exe /Zi` (emitting a
  matching `.pdb`), then dumps + compares two versions via abicheck and asserts
  the verdict. Cases: identical DLLs → compatible; a by-value struct that gains
  a field → BREAKING (real layout change exposed by the PDB).
- Gated behind the `msvc` marker (registered in `conftest.py`); skips cleanly
  when `cl.exe` is absent, so it is a no-op on Linux/macOS and on Windows
  runners without the MSVC environment — mirroring the
  `integration` / `libabigail` / `abicc` marker discipline.
- The lane is marked `continue-on-error: true` while the pure-Python PDB parser
  is proven against real MSVC output: the layout-dependent assertions
  **self-skip** if the parser does not extract struct layout from a given MSVC
  PDB version (a parser capability gap, not a regression), and the job does not
  block the PR. The PE-export assertion always runs. Promote the lane to
  blocking once it is consistently green across MSVC versions.

**Remaining follow-ups (not blocking):**

- Broaden the MSVC fixture matrix to mirror more ELF example cases
  (function removal, enum value shift, calling-convention change).
- Full function-signature checking from MSVC builds needs header parsing on
  Windows (castxml is not currently exercised on PE in CI); the current lane
  asserts on PDB-derived struct/enum layout verdicts.
- If GitHub-hosted MSVC proves too constrained for some PDB scenarios, add a
  scheduled self-hosted validation job.

**Risks.** PDB layout differences across MSVC versions; CI runtime/cost.

## Other deferred roadmap items

These came out of an external roadmap review. They are recorded here so they are
not lost, but they are lower priority than the MSVC lane above and several are
strategy decisions rather than engineering tasks.

### Still open

| Item | Notes |
|------|-------|
| Parser/fuzzer safety checks | Add a fuzz/parser-safety harness for ELF/PE/Mach-O/XML/YAML inputs (the security docs already warn that untrusted binaries deserve sandboxing). |

### Done

These were implemented after the review (alongside the canonical
`evidence_tier` work):

| Item | Where |
|------|-------|
| Versioned JSON Schema file + stability guarantee | `abicheck/schemas/compare_report.schema.json` + `abicheck.schemas` module; every report emits `report_schema_version`; documented in `docs/user-guide/output-formats.md`; validated by `tests/test_report_schema.py`. |
| `abicompat` / `abipkgdiff` parity lanes | `tests/test_abicompat_parity.py`, `tests/test_abipkgdiff_parity.py`; wired into the `libabigail-parity` CI lane. |
| Package-format test matrix | `tests/test_package_extractor_matrix.py` — real round-trip extraction + a unified malicious-payload matrix for the stdlib formats. |
| Release-pinned benchmark artifacts | `scripts/benchmark_comparison.py` emits `benchmark_report.json` (versions, git commit, ground-truth digest, accuracy); `publish.yml` attaches it to each GitHub Release. |

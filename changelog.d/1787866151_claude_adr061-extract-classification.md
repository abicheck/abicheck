### Changed

- **The eleven remaining flat metadata-parser modules are now classified
  `extract`** (ADR-061): `elf_metadata.py`, `pe_metadata.py`, `macho_metadata.py`,
  `dwarf_metadata.py`, `dwarf_advanced.py`, `sycl_metadata.py`,
  `symvers_metadata.py`, `python_api.py`, `python_ext.py`, `numpy_capi.py`,
  `build_mode.py`. Classifying them surfaced seven real `frontends -> extract`
  edges (`cli_compare_release.py`, `cli_datasources.py`, `cli_dump_helpers.py`
  x4, `cli_resolve.py`) reaching a parser module directly rather than through
  the engine; each now goes through `abicheck.workflows.extraction`, which
  already owns this re-export role for the sibling extraction helpers.
  `abicheck.buildsource.scan_levels` (the `scan` depth/mode/source-method
  resolver — pure enums and functions over them, no first-party imports) is
  now classified `model` for the same reason.

### Notes

- The same "patch where the call actually resolves" gotcha `workflows`
  re-export modules already carry applies here too: a test patching
  `abicheck.python_ext.detect_python_extension` no longer reaches
  `cli_dump_helpers.perform_elf_dump`, which now calls it through
  `abicheck.workflows.extraction` — patch the re-export module instead.

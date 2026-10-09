### Changed

- **The native dump now lives in `abicheck.workflows.dump.native`.** `abicheck/service_dump_native.py` is deleted, and its contents (`run_dump`, `extract_elf`, `FORMAT_ADAPTERS`) moved there unchanged. `abicheck.service.run_dump` still works. To substitute an extractor, replace its entry in `abicheck.workflows.dump.native.FORMAT_ADAPTERS`.
- **The probe-matrix schema types moved to `abicheck.model.probe_matrix`.** `ProbeSpec`, `ProbeConfiguration`, `Probe`, `ProbeResult` and `MatrixSnapshot` are pure data, and the compare-layer `diff_build_config` reads them. `abicheck.probe_harness` keeps `load_probe_spec`, `parse_probe_spec` and `run_probe_matrix`, and is now classified `workflows`.
- **`dumper.py` is classified `workflows`.** Its `dispositions.yaml` entry is retired. Dump output is unchanged.

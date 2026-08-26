### Changed

- **ADR-061 Phase 3 (converge artifact workflows)**: `service_dump_pipeline.py`
  — which owns `DumpRequest -> ResolvedDumpRequest -> DumpResult` — is now
  free of CLI imports and is classified `workflows` in
  `architecture/modules.yaml`, the first service pipeline to get a
  responsibility owner. Three engine-CLI boundary allowlist entries are gone
  (15 -> 12). No runtime behaviour changes.

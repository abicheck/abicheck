### Fixed

- Project/bundle-facts packages now record the real `ArtifactRef.kind` for
  header-only (`"header_only"`) and Python-only (`"python"`) members instead of
  mislabeling them `"elf"` (ADR-062 A1.8). Bundle-level resolution no longer
  drops a non-ELF (PE, Mach-O, Python, header-only) member silently: it is
  recorded on `BundleSnapshot.resolution_not_applicable` /
  `BundleDiffResult.resolution_not_applicable_members` and emitted as a
  `resolution_not_applicable_members` block in the BundleFacts compare JSON.

### Fixed

- **`abicheck.product_baseline.compare_product_directories`**: five
  correctness findings from review of this same PR.
  - Bundle-level cross-referencing (`bundle_intra_dep_signature_changed`,
    `bundle_intra_type_changed`, `bundle_provider_changed`, and
    `bundle_library_added`/`bundle_library_removed`) now keys each
    library by its own bare filename when building the two
    `BundleSnapshot`s, matching `abicheck.bundle.compare_bundle`'s
    pre-existing internal convention. Previously it used the
    relative-path identity `_discover_library_map` produces for
    per-library pairing, which silently broke that cross-referencing for
    any library not sitting directly at the discovery root, and made a
    library that simply moved directories between releases
    (`lib/provider.so` -> `lib64/provider.so`) read as an unrelated
    removal-plus-addition even though its own per-library ABI diff was
    already correctly paired.
  - `compare_product_directories()` now rejects a nonexistent (or
    non-directory) `old_dir`/`new_dir` with `SnapshotError` instead of
    silently discovering zero libraries and returning a false-green
    `NO_CHANGE` result.
  - The SONAME-major canonical fallback pairing now determines ambiguity
    from the *complete* per-side discovery, not just the libraries an
    exact match left unpaired — a product shipping parallel majors
    (`old={.1,.2}`, `new={.2,.3}`) no longer has an exact match on `.2`
    silently make the unrelated `.1`/`.3` look like one evolving library.
  - Header roots (flat or per-library) are now resolved with the same
    containment check `pack_product_baseline`/`unpack_product_baseline`
    already apply — an absolute or escaping root (`"../etc"`, `/etc`) no
    longer silently analyzes headers outside the product directory.
  - A library removed with no surviving sibling importing it (a
    standalone removal) is now reported as a `bundle_library_removed`
    finding. `compare_bundle()`'s own detector deliberately only reports
    a removal that breaks an internal bundle contract, delegating a
    standalone removal to the CLI's separate `--fail-on-removed-library`
    flag — but this library-only API has no equivalent flag, so it was
    previously silently invisible (`NO_CHANGE`) rather than a false-green
    whole-product compatibility gate.

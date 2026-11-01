# A stored `BundleFacts` operand pairs versioned member names on one side only

**Status (2026-10-31): CLOSED.** The NEW side of a stored comparison is discovered by the live fan-out's own rule, and stored facts are re-keyed on load by `build_match_map`'s canonical key (`facts_keyed_by_match_key`). Stored/live now pairs exactly what live/live pairs, for versioned, unversioned and mixed names (`tests/test_cli_compare_stored_versioned_member_pairing.py`). That CLI test replaces the in-process one named under "Test that pins it". Root cause, corrected: on real binaries the CLI capture already keyed canonically; the stored route found no member because it ran its own native-only discovery of NEW.

## Evidence

A stored operand (`compare OLD.bundle-facts.json NEW_DIR`) keys each member
by its canonical name, the `libX.so` form with the version suffix removed. A
directory NEW side keys the same member by its file name, `libX.so.N`. A
versioned member therefore never pairs on the stored side: the run reports
`no library pair was compared` and exits 1, while the live
`compare OLD_DIR NEW_DIR` pairs the same files.

Reproduced with release fixtures whose member names are `libcore.so.1` and
`libmid.so.1` (each snapshot's ELF soname is set to the same name). The
stored run's `library_filenames` maps `libcore.so` to `libcore.so.1`, and the
NEW directory keys `libcore.so.1`. Unversioned names (`libcore.so`) pair on
both sides.

## Intended resolution

Key both operands by one canonical name, derived in one place. The stored
facts already record the file name in `library_filenames`, so the stored
comparison should match on that name (or on the same canonical key the
directory side uses), not on a separately stripped form. Until that lands, a
stored comparison of a versioned directory should not read as a clean
pass: the zero-comparison run already reports `no comparison completed`.

## Test that pins it

`tests/unit/report/test_release_surface_changes.py` asserts the live/stored
fold agreement in-process, because the CLI stored path cannot yet pair
versioned names. Once the keys agree, that test should move to the CLI path.

<!-- Codex review follow-up round 6 on CLI cleanup phase two, PR 1/PR 2. -->

### Fixed

- **A manifest `gate` block now requires an explicit `aggregate_manifest_version`
  of `2.0` or newer — an absent version is rejected too, not only a
  declared pre-2.0 one.** "Absent version = this reader's own current
  MAJOR" is reader-relative: a genuinely old, pre-gate `aggregate` given
  the identical unversioned manifest applies its *own* "absent = my
  current major" rule and silently ignores `gate` regardless of what a
  newer reader would have done with the same input, restoring the exact
  version-skew inversion the `2.0` bump exists to prevent.
- **`abicheck.service.render_output()`'s `show_recommendation` parameter
  default is `False` again, matching the exact pre-removal Tier-2 Python
  API default.** An earlier revision changed the default to `True` to
  match the CLI's own unconditional-inclusion behaviour, which silently
  changed what an existing direct caller gets when it omits the keyword
  entirely — a public-API default change this PR's docs never announced
  (only the CLI flag removal was). The CLI's own unconditional inclusion
  is now achieved by its internal wrapper explicitly passing
  `show_recommendation=True`, not by changing the library default.

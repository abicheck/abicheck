### Fixed

- **CTF parsing now validates the string section's mandatory leading NUL
  sentinel** — the CTF sibling of the identical BTF fix: offset 0 in a
  CTF string section is reserved for the empty string per the format's
  own spec; a corrupt or hand-crafted blob whose string section didn't
  actually store that byte let a `name_off=0` ("anonymous") reference
  read whatever bytes happened to sit at offset 0 as a fabricated real
  name, with no completeness signal.
- **The mutmut PR lane's `require_baseline_for_pr()` no longer exempts a
  detector module change paired with its own edited test file from
  needing baseline drift** — `--diff-scoped` only gates mutants in the
  specific function(s) a diff actually changed in a mutated module, not
  the whole module, so a PR that changes one function and, in the same
  commit, weakens an existing test assertion for a different, *unchanged*
  function in the same test file previously read as "the module also
  changed, diff-scoped has this covered" and skipped the drift check
  entirely — while diff-scoped's own function-level scope never touched
  the unrelated function at all. Baseline drift is now required whenever
  an `only_mutate` module's own test file is touched, regardless of
  whether the module itself also changed.

### Fixed

- **A declared `compiler_family: msvc` no longer exempts a binding that
  actually resolves to a real, probeable non-MSVC compiler.** The MSVC
  skip (needed because `cl.exe` has no `--version` flag) was keyed purely
  on the *declared* `compiler_family`, so a profile declaring
  `compiler_family: msvc` whose binding resolved to a real `/usr/bin/gcc`
  was also silently exempted — the inverse of the already-fixed
  "`compiler_family: gcc` bound to a real `cl.exe`" gap. The skip now
  requires the resolved binding to itself look like `cl`/`cl.exe`, not
  merely that MSVC (or nothing) was declared, so a genuinely conflicting
  declared family is still probed and reported.

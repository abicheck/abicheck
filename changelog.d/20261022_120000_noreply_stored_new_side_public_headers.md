### Fixed

- **A stored baseline compared against a live build now checks the new side's
  public headers too** (#1715). `compare <bundle facts> <dir> --header new=...`
  dumped each live NEW member without marking its headers as the public-header
  set, so the NEW release surface read "public surface unresolved" and the
  declared-in-a-public-header-but-exported-nowhere check ran only against the
  stored OLD release. A PR that introduced such a gap passed. The NEW side now
  splits its `--header new=` inputs into public header files and directories,
  the same way a live-vs-live comparison does.

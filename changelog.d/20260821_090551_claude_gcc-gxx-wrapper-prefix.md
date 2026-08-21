### Fixed

- **Comparability gate**: the content-driven language-mode divergence
  carve-out's `gcc`/`g++` cross-driver sha256 exemption now also requires
  the two sides' *resolved compiler binary paths* (not just their
  `--version` banners) to be a genuine gcc/g++ pair. Two distinct wrapper
  scripts sharing one directory (e.g. `/opt/bin/vendor-a-g++`,
  `/opt/bin/vendor-b-gcc`) could each faithfully delegate `--version` to
  the same real, bare `gcc`/`g++` underneath -- passing the banner check,
  the shared install directory, and a shared target triple -- while still
  being genuinely different tools (different injected extraction flags)
  with genuinely different `compiler_sha256`. The exemption previously
  skipped the sha256 check for this pair; it now correctly declines,
  since the resolved-binary paths' own vendor-specific prefixes
  (`vendor-a-`/`vendor-b-`) no longer match.

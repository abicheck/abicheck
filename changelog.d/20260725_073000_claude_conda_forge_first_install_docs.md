### Documentation

- **`README.md` and `docs/getting-started.md` now lead with conda-forge as
  the recommended full-scanner installation** (`conda create -n abicheck -c
  conda-forge python=3.12 abicheck`), with `pip install abicheck` presented
  second as the lightweight/core, no-native-scanner-dependency alternative —
  reflecting the actual PyPI-vs-conda-forge distribution contract instead of
  listing `pip install` first with conda-forge as an afterthought. Both pages
  now also explicitly warn against `pip install castxml` (the unmaintained
  legacy PyPI distribution, last released 0.4.5 in 2018, that abicheck's
  version gate already rejects by default for an authoritative L2 scan).

### Added

- **`scripts/check_distribution_metadata.py` now fails if the built wheel's
  `Requires-Dist` pulls in `castxml`**, closing the loop on the
  lightweight/core-PyPI-package contract at the actual packaging-metadata
  level (was previously only true by omission, unchecked). New
  `tests/test_packaging_docs.py` covers the doc-side half of the same
  contract in the default fast lane (no `build`/`twine` required): no
  `castxml` in `pyproject.toml`'s core dependencies or any extra, neither
  install doc recommends `pip install castxml` as a runnable command, and
  both installation sections present conda-forge before pip.

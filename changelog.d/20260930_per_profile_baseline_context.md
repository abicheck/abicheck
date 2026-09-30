### Fixed

- `publish-baseline.yml` and `update-main-baseline.yml` now dump each
  profile's baseline under the compile context its candidate cells use: the
  profile's `consumer_compile:` overlay when declared, else its `compile:`
  overlay, resolved by the same per-profile resolvers the run plan uses
  (`abicheck/buildsource/baseline_extraction_context.py`). Previously every
  profile's baseline was dumped under the bare build config, so a profile
  with a compile overlay (e.g. a Clang client profile) produced a baseline
  the comparability gate refused against its own candidates. New inputs
  `project-config` and `toolchain-bindings-path`; the resolved context is
  recorded as `manifest.json`'s `extraction_context` (new `actions/baseline`
  input `extraction-context`).

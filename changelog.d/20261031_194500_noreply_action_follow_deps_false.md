### Changed

- **The Action's `follow-deps: false` now turns the dependency walk off** —
  an explicit `false` forwards `--no-follow-deps`, which overrides a
  configured `deps.follow: true` for that run. Before, `false` forwarded
  nothing and a configured walk still ran. The input's default is now empty,
  which defers to `.abicheck.yml` as before.

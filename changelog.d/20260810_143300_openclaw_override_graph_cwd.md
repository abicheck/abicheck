### Fixed

- **Windows source-graph override extraction now replays its working directory correctly.** The Clang override-graph pass expands the redacted home-directory placeholder before setting its subprocess `cwd`, so home-rooted compile databases no longer degrade `override_graph` and the dependent virtual-dispatch graph.

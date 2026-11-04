### Fixed

- **Release Markdown no longer repeats a shared coverage warning once per library.** A warning several members carry (a disabled detector, an unmatched `--exclude-header` rule) is now one line naming every library that carries it, e.g. `- sycl disabled (28 libraries: …)`; a single-library warning keeps its `- lib: warning` form. A 28-library MKL directory scan's block went from 281 lines to one per distinct warning.

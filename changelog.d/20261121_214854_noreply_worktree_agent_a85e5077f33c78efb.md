### Fixed

- **A public header the L2 parse had to drop no longer leaves the run
  `complete`.** When castxml or clang could not compile one header of a
  multi-header `-H` set, the parse dropped it and the analysis assurance
  still read `complete`, although its declarations were never observed.
  `analysis_assurance.status` now reads `partial` with a per-side note
  naming the dropped headers; both frontends' drop records are read
  (owner: `policy/analysis_assurance/_unparsed_headers.py`).

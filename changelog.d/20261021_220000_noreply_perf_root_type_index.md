### Performance

- **Root-type redundancy folding no longer scans every root per finding.**
  `split_redundant` searched each derived finding against every changed root
  type in turn (roots x findings regex searches, plus a regex compile per
  root, and a full root-map copy per root-type finding). It now indexes the
  roots by their first identifier run, so each finding searches only the
  roots its spellings can name, compiling each pattern on first use. Same
  matches, same order; `split_redundant` on a 14-workload synthetic sweep
  (n=8000) went 0.87 s -> 0.17 s thread CPU.

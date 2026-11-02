### Performance

- **A directory/package `compare` keeps about a quarter as much memory per
  compared member** — each finished member's result kept its contract type
  graph (the whole declaration surface, once per side) as tuples of strings
  until the run ended. The graph is now held packed and decoded when read;
  reports are unchanged. On a synthetic 8-member release (6000 declarations
  each), memory retained by finished members fell from 36.1 to 8.9 MiB and
  peak RSS from 485 to 432 MiB (ADR-062 A2.5).

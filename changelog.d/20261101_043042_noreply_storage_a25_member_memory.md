### Performance

- **A directory/package `compare` no longer grows its memory with every
  member compared** — each finished member's result kept its contract type
  graph (the whole declaration surface, once per side) as tuples of strings
  until the run ended. The graph is now held packed and decoded when read;
  reports are unchanged. On a synthetic 16-member release, peak traced memory
  fell from 72.1 to 53.6 MiB and stays flat from 2 to 16 members (ADR-062
  A2.5).

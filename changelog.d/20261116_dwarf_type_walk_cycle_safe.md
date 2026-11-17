### Fixed

- **DWARF type-alignment walk terminates on cyclic and deeply nested type graphs** — the walk that decides whether a by-value aggregate has a misaligned member is now iterative with a visited set, so a reference cycle in the DIE graph or very deep legitimate nesting no longer exhausts the interpreter's recursion limit. Qualifier and typedef chains are followed to their leaf rather than cut off after 12 steps. Output is unchanged for every input that terminated before.

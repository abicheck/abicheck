### Performance

- `textual_declaration_hint` now compiles its declaration pattern only when a
  header actually names the symbol, and memoizes it. Compiling one regex per
  undeclared export cycled `re`'s 512-entry cache and cost ~1.4 s per side on
  MKL-sized libraries (~78 s on a 28-member release comparison); the result
  is unchanged.

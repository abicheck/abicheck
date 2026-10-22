### Performance

- **`exported_not_public` no longer rescans whole headers once per export** — `textual_declaration_hint` ran a fresh regex over every line of each header that names an export's leaf, for every export. On a library with tens of thousands of exports and large headers this was quadratic: `compare --no-baseline libmkl_rt.so -H mkl_cblas.h` ran for more than ten minutes and now takes about 30 seconds. Each header now carries an identifier-to-lines index, and only those lines are matched. Findings are unchanged.

### Performance

- **Type-spelling matching no longer repeats its lexical work.** The
  compiled vocabulary alternation and the match list a given (vocabulary,
  text, window) triple produces are now reused from one bounded, explicitly
  owned cache (`abicheck/compare/spelling_match_cache.py`) instead of being rebuilt
  at each of the nine reachability call sites: measured **2.4x** on a
  large-vocabulary reachability scan (1,500 public functions, ~1,200 stdlib
  spellings), with identical results and ~1.2 MiB retained. Only the
  *lexical* result is reused — the scan's own alias/provenance state
  updates still run over every reused match, since the same type string
  reached directly and through two different typedefs contributes different
  evidence. `_compile_spelling_pattern` also breaks ties between
  equal-length candidates deterministically, so one vocabulary always
  builds one pattern text rather than missing CPython's own `re` compile
  cache when its spellings arrive in a different order.

### Fixed

- **`macro_graph.py` didn't recognize C++23's `#elifdef`/`#elifndef`** —
  the same word-boundary gap already fixed for `#ifdef`/`#ifndef` (`\b`
  fails right after "elif" too). Worse than a silent miss: the original
  `#if`'s frame stayed open across the unrecognized `#elifdef`, wrongly
  attributing that branch's own declarations to the first branch's macro
  — a wrong `MACRO_CONTROLS_DECL` edge, not merely a missing one. Fixed by
  widening `_ELIF_RE` to also match both C++23 forms, correctly marking
  the whole chain unmodeled.

### Documentation

- Investigated and documented (not fixed) a narrow, deliberately-deferred
  gap in `macro_graph.py`: a block comment appearing mid-directive
  (`#/**/ifdef X`) is unmodeled, since every directive regex only tolerates
  plain whitespace at those positions. Pinned by a dedicated regression
  test.

### Performance

- Removed per-symbol redundant work found by profiling a real MKL
  (`libmkl_intel_lp64`) comparison: `has_unresolved_component` returns early
  for spellings without the `?` sentinel, the textual declaration hint
  skips header lines that do not contain the export's name, the export
  surface judges each distinct type spelling once per pass, and the internal
  template-leak detector resolves each function identity once. Reports are
  unchanged; the profiled `compare()` stage drops from 153.6 s to 132.4 s.

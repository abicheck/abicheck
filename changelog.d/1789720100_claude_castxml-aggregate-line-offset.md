### Fixed

- The unparseable-header fallback attributed a castxml error to the header
  *after* the one that failed, because the aggregate's new preamble line
  shifted every header down one line. The writer and the attribution now
  share `AGGREGATE_HEADER_LINE_OFFSET`, so a failing header is dropped
  instead of a good one.

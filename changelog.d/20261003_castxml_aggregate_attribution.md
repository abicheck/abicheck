### Fixed

- The castxml unparseable-header fallback blamed the wrong header (the one
  *after* the failing one) once the aggregate gained its preamble `#include`
  line; the aggregate's header-line layout now has one owner
  (`castxml_header_compat.AGGREGATE_FIRST_HEADER_LINE`) that attribution reads.

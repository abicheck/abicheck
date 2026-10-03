### Fixed

- The castxml unparseable-header fallback (`-H include/` dropping a header that cannot compile, e.g. oneDNN's `dnnl_sycl.hpp`) attributes failures to the right header again. The castxml-compat preamble that #1470 placed first in the aggregate shifted every header one line down, while attribution still assumed header `i` sits on line `i+1`, so the failure was re-raised instead of the header being dropped. The writer and the attribution now share one layout constant, `AGGREGATE_FIRST_HEADER_LINE`.

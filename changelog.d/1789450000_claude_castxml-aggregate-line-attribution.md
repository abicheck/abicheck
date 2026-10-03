### Fixed

- **Unparseable-header fallback works again with castxml** — the `_Float128` compatibility preamble added to castxml's aggregate header shifted every listed header down one line, so a header that fails to compile (e.g. one carrying `#error "Unsupported compiler"`) was attributed to the wrong input and a multi-header dump failed outright instead of excluding it. The aggregate now resets its line numbering after the preamble include.

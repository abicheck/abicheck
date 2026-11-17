### Fixed

- **Header text scans and compiler probes answer for the file as it is now** — the scans of a header's text for calling-convention and exception-specification spellings, and the compiler probes of plain-`char` signedness and builtin layout, were memoized on the path alone (or on a stat stamp that a same-size rewrite can leave unchanged), so a rewritten header or a replaced compiler within one process returned the old answer. The text scans are now keyed on the file's content digest, and the probes on the compiler binary's revision.

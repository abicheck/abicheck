### Fixed

- The clang constant/default-value fingerprint (`expr:<hash>`) no longer embeds the checkout path of a lambda closure or anonymous type through a node's own `name`, a referenced declaration's `name`, or its resolved `qualified_name`, so byte-identical headers under two roots no longer report a spurious `constant_changed`. `ABICHECK_CLANG_LAYOUT_TOOL`'s base-offset keys likewise strip the checkout directory from an anonymous/lambda base spelling.

### Fixed

- **A Mach-O image's dyld export trie is its export table** — dyld binds only what the export trie lists, but the parser kept any defined external the nlist marked exported. When an image has an export trie, the trie now decides which names are exported, and its weak and re-export flags decide linkage. Without a trie the symbol table still stands alone.
- **An unread dependency side is reported as unread** — a binary-only side whose dependency-internal types could not be classified (a PE image's decorated export names) reads as not classified in the compare report: a new `dependency_types_read` key holds the per-side state, and a coverage note names the side beside the other coverage lines. Findings on that side still stay in contract.

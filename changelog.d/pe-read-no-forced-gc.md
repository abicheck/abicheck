### Fixed

- **Reading a PE file no longer forces a full garbage collection** — `pefile`'s `close()` ends in an unconditional `gc.collect()`, so every DLL read (exports, the CodeView PDB reference, consumer imports) paid a full collection of the whole process, tens of milliseconds each in a process holding large snapshots, and several per DLL per `compare`. PE images are now opened through `abicheck.extract.pe_image.open_pe`, which maps the file read-only itself and releases the mapping without calling pefile's `close()`. Parsing and memory behaviour are unchanged.

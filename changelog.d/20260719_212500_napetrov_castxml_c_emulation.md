### Fixed

- **CastXML C-header parsing now uses C compiler emulation** — GNU C headers are probed with the host C driver and CastXML's `gnu-c` mode, avoiding invalid C++ `_Float*` builtin shims when the pinned Superbuild parses C APIs.

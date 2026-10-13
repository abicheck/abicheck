### Fixed

- **Header-order witness now covers PE and Mach-O header-scoped dumps** — the direct PE/Mach-O route in `workflows/dump/header_scoped.py` bypassed `dumper.dump()`, so a reordered but equivalent header set was never proven canonical and the profile gate refused it on macOS. The route now wraps its native builder with `extract/header_order_witness.header_order_proof` and attaches an extraction contract to each witness pass. Existing fallback behaviour is unchanged.

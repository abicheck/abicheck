### Changed

- **Three more change kinds declare where they are observable** (ADR-093 WS-1 batch 6) — `sanitizer_runtime_required` is now declared `{B}` on ELF (a sanitized shared object is observed from its binary alone), `rtti_mode_changed` `{B,BD}` (the `-frtti`/`-fno-rtti` flag is visible only through a compile database), and `undocumented_export_ratio_increased` `{B,H}` (the ratio needs public-header declarations). Each has a real-build oracle and a negative control. Nothing about what the tool reports changes; the three kinds leave the UNCLASSIFIED baseline.

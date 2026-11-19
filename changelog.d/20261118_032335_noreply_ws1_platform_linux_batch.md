### Changed

- **`observable_at` declared for four Linux platform kinds (ADR-093 WS-1)** — `func_deleted_elf_fallback` (`{B,H}`: the namesake is only emitted when the public header declares the function on both sides), and `exported_object_alignment_reduced`, `inline_namespace_moved`, `abi_surface_explosion` (`{B}`, ELF). Each declaration is backed by real ELF objects run through the public `compare` in `tests/test_ws1_platform_linux_observability.py`.

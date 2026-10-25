### Fixed

- **Benign system-header duplicates collapse on mingw-w64 too** — a same-key pair such as mingw-w64's `_swprintf` (a dllimport alias in `<wchar.h>`) and `swprintf` (implemented in `swprintf.inl`) was kept and warned "Duplicate mangled symbols skipped (first-wins)" on Windows, because the collapse compared the declared identifiers. The mangled key already names the symbol, so the identifier is no longer compared; conflicting signatures still warn.

### Fixed

- **An unspellable function-template default no longer reads as a clean run on clang** — when the clang header backend cannot spell a function template's default argument (for example `int N = sizeof(T)`), the pattern now records the defaults as `unsupported` instead of looking like an ordinary function, and the analysis reads `partial` with a `template-parameter defaults` gap note instead of `complete` (ADR-085 D3).

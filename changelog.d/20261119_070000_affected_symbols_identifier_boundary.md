### Fixed

- **`affected_symbols` no longer attributes a type change to functions whose type merely contains the name.** A change to `struct s1` listed functions taking `struct s10 *` as affected, because the enrichment matched type names as substrings. A match now counts only as a whole identifier (`ns::Widget`, `const Widget &` and `Widget[4]` still match `Widget`; `Widget2` and `MyWidget` do not). Findings, kinds and verdicts were never affected.

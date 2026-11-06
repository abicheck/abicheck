### Changed

- **Release Markdown caps the Coverage Warnings section for large scans.** Distinct coverage warnings are ranked by how many members carry them and the first 10 are rendered; the rest are counted in one closing line that points to `-o json=...`. A shared warning names up to 5 members and counts the rest. The JSON document is unchanged and still lists every warning on every member. On a 28-library synthetic scan with per-member variants, the section went from 66 lines to 14.

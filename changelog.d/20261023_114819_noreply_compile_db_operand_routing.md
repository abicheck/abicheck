<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **Compile-database entries are split by source operand** — a `compile_commands.json` entry that names several sources (`gcc -c a.c b.c`, `g++ -x c++ a.h -x none b.c -o prog`) now yields one compile unit per source, each with the language the driver applies at its own `-x` position, instead of one unit for the first file. A link with no source operand (`gcc util.o libfoo.a -o app`) no longer becomes a compile unit for its first object; it records one link unit. Single-source entries are unchanged. The source-tier generation moves to 9 (`SOURCE_GENERATION`), so generation-8 source-tier facts for multi-source builds are not re-extracted automatically: re-extract them to get the corrected routing. Existing packages stay readable and report generation drift.

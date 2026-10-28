### Fixed

- Directory and release comparisons no longer print an "Ambiguous match" warning when the candidates are filesystem aliases of one library (for example, a `libfoo.so -> libfoo.so.2` dev symlink or a hard link). The warning still fires when the candidates are different files.

"""abiforge: a completeness generator for C/C++ ABI and API checkers.

It enumerates a formally stated space of library edits (record layout,
inheritance, functions, enums, ELF symbols and versioning, toolchain and
platform settings), builds every old/new pair with real compilers, derives
each pair's ground truth from the language and platform ABI rules plus an
execution oracle (consumers run against the old and new library), asks a
checker for its answer through an adapter, and reports which part of the
space the checker covers and where it is wrong.

See ``abiforge/README.md``.
"""

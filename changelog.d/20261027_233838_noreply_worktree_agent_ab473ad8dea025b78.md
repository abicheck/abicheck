### Fixed

- **Compiler command lines tokenize through one helper** — make recipes, ninja
  commands, compiler-recorded command lines, SYCL `-###` job lines and
  compile-database entries now all split through
  `abicheck._compiler_options.split_command_line`, so the same text no longer
  parses differently between two call sites on one host. A compile-database
  `command` in `header_conditionals` previously always used POSIX quoting; it
  now follows the host convention like the other sites, so an unquoted Windows
  path with backslashes is kept intact on a Windows host. Quoted values with
  spaces and `/I"C:\x y"` tokenize the same on every host.

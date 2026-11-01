### Changed

- **abicheck now requires Click 8.2 or newer** — the declared dependency was
  `click>=8.0`, but Click 8.0/8.1 merge a command's stderr into its stdout in
  `CliRunner` and have no separate `result.stderr`, which the test suite needs
  to read a JSON report apart from the notes printed beside it. Every
  supported Python already resolves a newer Click, so installs are unaffected.

# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.3.x   | ✅ Yes     |
| 0.2.x   | ✅ Yes     |
| 0.1.x   | ✅ Yes     |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report vulnerabilities via [GitHub Private Vulnerability Reporting](https://github.com/napetrov/abicheck/security/advisories/new)
or by emailing the maintainer directly.

Include:
- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Potential impact

We aim to respond within 72 hours and release a fix within 14 days for confirmed issues.

## Security considerations

- abicheck parses ELF binaries and DWARF debug info from potentially untrusted sources.
  Use `--suppress` and policy files from trusted sources only.
- **ELF/DWARF parsing**: Malformed or adversarially crafted ELF binaries may trigger
  bugs in the underlying `pyelftools` library. When analyzing third-party or untrusted
  binaries, run abicheck in a sandboxed/isolated environment.
- ABICC XML descriptors are parsed with `defusedxml` to prevent XML entity expansion attacks.
- Suppression patterns use Python `re` with `re.fullmatch` to limit matching scope.
- Policy files (YAML) are loaded with `yaml.safe_load` — no arbitrary code execution.

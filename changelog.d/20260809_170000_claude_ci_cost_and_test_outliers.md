### Performance

- **Hoisted CTF's zip-bomb decompression cap to a module-level
  `_MAX_DECOMPRESS` constant** in `abicheck/ctf_metadata.py`, and derived the
  guard's error message from it rather than repeating the number in a literal
  string. No behavior change — the production cap is still 256 MiB — but the
  threshold is now a named, patchable knob, which lets the zip-bomb regression
  test cross it with a 2 MiB payload instead of allocating and compressing a
  real 257 MiB buffer (that test went from ~4.4s to ~0.03s).

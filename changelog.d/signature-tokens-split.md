### Changed

- `abicheck/model/signature_normalization.py`'s bracket-aware scanning
  helpers moved verbatim to a new leaf, `abicheck/model/signature_tokens.py`,
  bringing the module back under the model package's 800-line ceiling. No
  behavior change.

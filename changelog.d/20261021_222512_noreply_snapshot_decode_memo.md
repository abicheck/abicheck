### Performance

- **Faster snapshot decode for repeated fact and enum shapes** — the snapshot decoder (`load_snapshot`, and so every snapshot-cache hit) now answers repeat serialized `Fact` shapes and enum strings from small memo tables (`storage/decode_memo.py`) instead of rebuilding each one; declaration decoding is ~13% faster on a 100 MB snapshot. Decoded content, digests and the schema are unchanged.

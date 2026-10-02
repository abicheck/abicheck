### Performance

- Snapshot writes stream to disk instead of building the whole JSON text and its encoded bytes first. On a 238 MB snapshot a write's peak memory drops from +0.52 GiB to +0.14 GiB and the write is slightly faster; plain and gzip output is byte-identical to before. The snapshot cache streams its zstd entries too (`write_snapshot(..., zstd_content_size=False)`); other zstd writes keep the one-shot frame, which declares its decoded size.
- `storage.json_stream.iter_json_indented` batches consecutive elements of a wide list or map into one `json.dumps` call sized by measured output, instead of probing each element with a Python node-count walk. The output is still byte-identical to `json.dumps(..., indent=2)`; the streamed `--bundle-facts-out` write benefits too.

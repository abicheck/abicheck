### Performance

- Finished compare operands now populate only the finished snapshot-cache tier, avoiding a second serialization of an unused raw entry. Raw dump requests still populate the raw tier, and existing cache entries remain readable.

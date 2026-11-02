### Changed

- **The snapshot cache is now bounded by bytes, not by entry count** — the
  dump cache used to keep up to 100 entries, so a few very large release
  members could grow it without limit in bytes. It now evicts least-recently-used
  entries once a tier passes a 4 GiB byte quota (ADR-062 D11, storage plan A2.3).
  The entry just written is never evicted, and temporary files left by an
  interrupted cache write are removed once they are an hour old.

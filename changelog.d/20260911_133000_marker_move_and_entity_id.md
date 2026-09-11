### Fixed

- **A cross-file move carried only in a closure marker's own filename is no
  longer reported as a compatible coordinate-only shift.**
  `closure_location_free_identity` drops each marker's basename along with
  its `:line:col`, and justified that by saying a genuine cross-file move is
  caught separately by the declaring-file check — which holds only where
  such evidence exists. With none recorded, `(lambda at old.h:1:2)` →
  `(lambda at new.h:9:9)` collapsed to nothing. `_classify_outcome` now
  reads the marker basenames as location evidence (via the new
  `model.graph_identity.closure_marker_files`) and reports
  `declaration_moved`.
- **A coordinate-only finding no longer claims "no declaring file was
  recorded on either side" when exactly one side recorded one.** The new
  `partial_declaring_file` evidence value distinguishes a one-sided
  extraction gap from a fully absent one, and the finding's text states
  which it is.
- **`schema_staleness_status`'s content-identity shortcut now compares every
  persisted field.** `entity_id` is `field(compare=False)` yet is persisted
  and part of the canonical content digest, so plain dataclass equality
  called two snapshots one capture while their serializations differed —
  and the digest-driven byte-identical coverage warning, the channel that
  residual is disclosed through, stayed silent.

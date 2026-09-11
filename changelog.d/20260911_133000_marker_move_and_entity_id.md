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
- **Marker extraction is order-correct across mixed spellings.**
  `closure_marker_files` normalizes once and scans the single normalized
  form, so an identity mixing the raw `lambda at path:line:col` and
  normalized `lambda:basename:line:col` spellings yields its declaring
  basenames in source order — two independent scans returned them grouped
  by which regex matched, and the raw form's path group could run greedily
  through a following normalized marker.
- **A declaration that was renamed *and* moved reports the combined
  outcome again.** The marker-carried move evidence is no longer disabled
  during a rename, so the pair reports `declaration_identity_reconciled`
  rather than a bare `declaration_renamed`.
- **Snapshot content identity ignores runtime-only fields.** Calling
  `AbiSnapshot.index()` on one side no longer makes two content-identical
  snapshots report degraded assurance; `from_headers`' conditional
  persistence is modelled explicitly.

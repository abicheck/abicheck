### Fixed

- Writing a bundle whose member carries ADR-062 D14/D15 state no longer fails with an `ImportError`: `storage.snapshot_encode.sectioned_document_for_write` is back, as the materialized form of the streamed sectioned document, for the bundle writers that embed a member document.

### Fixed

- castxml's derived record data size no longer contradicts the target's compilers: an empty record reads 0 under Apple arm64's C++11 POD rule (it read its `sizeof`), and a record with a bit-field reads unknown on MinGW targets, whose default Microsoft bit-field layout gives the field a whole unit of its type. Extractor generation 63: snapshots from generation 62 load with the usual generation-drift notice.

### Fixed

- **A changed const object's initializer is reported from a binary-only compare.** A stripped binary has no header value, so `VAR_VALUE_CHANGED` never fired for an exported read-only object whose initializer moved. The extractor now records that object's section bytes (`Variable.initializer_hex`), and the detector compares them hex to hex when both sides have equal-length bytes. A side without bytes stays unknown, never a change. The declarations section moves to v2 with an identity migration, and a snapshot without the key decodes as not-read.

### Added

- Recognize the experimental native GCC producer for comparisons requiring a known same-producer representation. Comparisons with Clang or CastXML remain representation-gated.
- Preserve explicit extractor coverage limits in stored dump provenance (`extraction_limitations`) and lower analysis assurance to partial when such limits are present, including after snapshot reload.

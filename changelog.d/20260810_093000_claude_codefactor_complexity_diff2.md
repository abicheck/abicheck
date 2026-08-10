### Changed

- **Split two more CodeFactor "Complex Method" findings in the SYCL AST decoder
  and the C++ name classifier.** `sycl_context._iter_json_documents`'s
  document-boundary scanner is now a `_ChunkCursor` (the one-chunk-at-a-time
  buffer) plus a `_BracketState` (the string- and escape-aware nesting counter),
  leaving the generator as the per-document loop it describes.
  `name_classification._strip_cv_in_segment`'s single loop over every C++
  declarator case became `_CvSegmentScan`, one method per syntactic case reached
  through a leading-character dispatch. Both behaviour-preserving and checked
  differentially against their pre-refactor selves: 72,198 runs for the document
  scanner (varying chunk sizes and document-selection policies, including
  malformed and truncated input), and 98,463 type spellings plus all 1,283,689
  ordered pairs through `cv_qualifiers_only_differ` for the cv scanner — no
  differences in either.

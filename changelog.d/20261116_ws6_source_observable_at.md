### Changed

- **Four single-translation-unit source kinds and `public_api_internal_type_renamed` are declared observable from sources at `{B,H,S}`.** The call-graph, virtual-dispatch and internal-dependency kinds, plus the source-tree type rename, were declared to need the multi-TU graph (`G`). Their fact is carried by the source replay (`S`) with the header declarations (`H`) and the export table (`B`); only a fact across translation units needs `G`. Declarations only: no detector changes.

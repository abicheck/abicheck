# Machine-readable metadata

abicheck publishes metadata and JSON Schemas for package indexes, citation
managers, automated validation, and downstream tools.

## Citation and software metadata

- [Citation File Format (`CITATION.cff`)](https://github.com/abicheck/abicheck/blob/main/CITATION.cff)
  is the human- and tool-readable citation record. GitHub exposes it through
  **Cite this repository**.
- [CodeMeta (`codemeta.json`)](https://github.com/abicheck/abicheck/blob/main/codemeta.json)
  expresses project identity, authorship, licensing, repository links, and
  runtime dependencies using the CodeMeta vocabulary.
- [Zenodo deposit metadata (`.zenodo.json`)](https://github.com/abicheck/abicheck/blob/main/.zenodo.json)
  provides the project metadata Zenodo will use when a release is archived.
  It does not itself create a DOI.

These files deliberately contain no unverified ORCID, DOI, or release date.
When an archival release DOI is minted, add it to the citation and CodeMeta
records in that release.

## JSON Schema publication

The package source is authoritative. Identical copies are published with the
documentation at stable, versioned URLs:

- [Build evidence](../schemas/v1/build_evidence.schema.json)
- [BuildSourcePack manifest](../schemas/v1/build_source_pack.schema.json)
- [Compare JSON report](../schemas/v1/compare_report.schema.json)

Every file declares its own canonical HTTPS `$id` at the linked URL. Versioned
paths are immutable contracts: a breaking schema revision receives a new major
path (for example, `v2`) and existing paths remain available. A convenience
"latest" URL, if introduced, must not replace a versioned `$id`.

`python scripts/publish_schemas.py` refreshes the documentation copies;
`python scripts/publish_schemas.py --check` fails when they are stale. CI also
validates the schemas and their metadata. The Pages deployment verifies that
all canonical `$id` URLs are retrievable after publication.

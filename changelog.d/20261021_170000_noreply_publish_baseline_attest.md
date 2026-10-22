### Added

- **`publish-baseline.yml` can attest what it publishes** — new opt-in `attest` input (default `false`). When `true`, a separate `attest` job attests the release-contract assets actually on the release (`actions/attest-build-provenance`, the same SHA pin as `actions/stage-baseline`) and the workflow outputs `attestation-url`. The calling job must grant `id-token: write` and `attestations: write`; no job in the reusable workflow declares them, so existing callers keep working unchanged.

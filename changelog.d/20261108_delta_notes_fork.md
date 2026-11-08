### Added

- With `delta-baseline` and `pr-comment-via: artifact`, a fork PR's comment now carries the same two notes as the direct path ("Changes in this PR (vs main@sha)" and the release gate's verdict). They travel as validated `analysis_context` fields (`delta_outcome`, `delta_base_sha`, `gate_verdict`); the trusted publisher words them itself.

Project check cells now select each target's own source-evidence pack from
`build-output.json`, and `consumer_compile` profiles execute a distinct
candidate header extraction against the unchanged producer binary. The
resolved per-target evidence pack now also reaches `--build-info` for a
`replay`/unset evidence-producer (not just wrapper/clang-plugin), the
consumer-context dump forwards `gcc-prefix` and prefers a candidate-specific
header/include set over unioning it with the shared one, and an omitted
`consumer_compile` field falls back to the workflow's global
frontend/compiler inputs instead of the empty string -- gated on a new
`consumer_compile_active` run-plan field so that fallback only fires for a
cell whose profile actually declares a `consumer_compile:` overlay, not for
every cell whenever the caller sets any global `--ast-frontend`/`--gcc-path`/
`--gcc-options`.

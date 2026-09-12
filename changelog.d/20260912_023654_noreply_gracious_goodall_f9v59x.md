### Fixed

- **GitHub Action: a directory/package (release) `compare` is no longer less
  capable than the same comparison run through the CLI** — `action/run.sh`
  rejected the L2 compile-context inputs (`lang`/`ast-frontend`/`gcc-path`/
  `gcc-prefix`/`gcc-options`/`sysroot`/`nostdinc`) and silently dropped
  `depth: headers` for that operand shape, each citing a CLI restriction that
  had already been lifted: the per-library release fan-out threads the
  both-sides compile context to every pair's header dump
  (`cli_resolve.resolve_directory_compile_context`) and accepts every rung of
  the public depth ladder, with the floor enforced per member. Both inputs are
  now forwarded exactly as they are for a single pair, and
  `action/validate-inputs.sh` no longer fails the step before dependency
  install for a comparison the run itself would serve. `depth: build`/`source`
  and inline `sources`/`build-info`/`compile-db` — which the CLI really does
  still reject for that shape — keep failing loud.

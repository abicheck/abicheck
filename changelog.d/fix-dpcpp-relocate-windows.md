### Fixed

- **DPC++ host-only replay relocates the integration header/footer on every
  host.** The driver's temp directory was derived with `Path(...).parent`,
  which on Windows rewrites `/tmp/icpx-1` to `\tmp\icpx-1`; the textual
  relocation then matched nothing and the replayed jobs kept pointing at the
  driver's temp directory. The directory is now taken from the token's own
  spelling (`buildsource.dpcpp_jobs.spelled_parent`).

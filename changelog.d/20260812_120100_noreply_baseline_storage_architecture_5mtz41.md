<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Documentation

- Documented a known, deliberately-unfixed gap in `publish-baseline.yml`:
  two concurrent runs publishing the same tag/profile can race such that
  the losing run's plain (non-retry) upload fails outright instead of
  falling through to the safe-retry verification path. The failure mode
  is safe (a loud job failure that resolves cleanly on re-run), so this
  is documented in the workflow's own comments rather than fixed
  reactively — see the "Upload release asset" step's header comment for
  the full reasoning.

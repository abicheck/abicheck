"""Reusable invariant oracles over public comparison results.

Each module states one of the four recurring bug classes as a pure function
that takes public results (report JSON, producer kind sets, route outcomes)
and returns a list of violation strings. An empty list means the invariant
holds for that input. The oracles never re-run detectors: they judge what
the public surface reports. They are applied to live catalog runs and to
reduced-evidence runs; the golden files are Markdown and HTML snapshots with
no JSON report to feed them, so no golden run is wired to an oracle.

* :mod:`tests.oracles.absence`      -- A: missing evidence never reads as a clean claim
* :mod:`tests.oracles.producer`     -- B: castxml and clang agree up to an allowlist that only shrinks
* :mod:`tests.oracles.routes`       -- C: equivalent routes decide identically
* :mod:`tests.oracles.conservation` -- D: every rendered total equals the JSON population
"""

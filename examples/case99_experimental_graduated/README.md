# case99 — experimental → stable graduation (compatible)

## What this case demonstrates

A template / header-only library publishes a feature first under an
``experimental::`` namespace and later promotes it to the stable
``lib::`` namespace, keeping the experimental alias as a friendly
forward.

| v1 declares | v2 declares |
|---|---|
| ``lib::experimental::sort`` | ``lib::sort`` *and* ``lib::experimental::sort`` |

## Why abicheck fires

Without the dedicated detector the diff is just a ``func_added`` for
``lib::sort`` — a silent compatible change that gives no hint that the
library is signaling readiness for migration.

``EXPERIMENTAL_GRADUATED`` makes the migration event visible:
reviewers see that consumers can now move from the experimental
spelling to the stable one without code change.

## Expected verdict

``COMPATIBLE`` — the experimental name is preserved, so existing
consumers keep compiling and the addition of the stable name is purely
additive.

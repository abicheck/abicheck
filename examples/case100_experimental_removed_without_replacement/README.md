# case100 — experimental:: removed without replacement (API break)

## What this case demonstrates

A library deletes a declaration that only ever lived under
``experimental::`` and does not republish it at a stable name. The
consumer relied on the experimental spelling; against v2 it does not
compile.

| v1 declares | v2 declares |
|---|---|
| ``lib::experimental::bar`` | (nothing with leaf ``bar``) |

## Why a generic ``func_removed`` is not enough

``func_removed`` describes *what* disappeared, but at the
namespace-shape level the question every reviewer asks is "is there a
migration target?". The dedicated ``EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT``
finding answers that question explicitly: the detector looked for a
stable twin and didn't find one.

## Expected verdict

``API_BREAK`` — source-level break for any consumer that named the
experimental declaration. The mangled symbol disappears too, so the
underlying ``func_removed`` is also reported as ``BREAKING``; the
namespace-level finding is the human-readable framing.

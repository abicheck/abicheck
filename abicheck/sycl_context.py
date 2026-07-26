# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ADR-050 D5 (G32 Phase D) — SYCL/DPC++ host vs. device AST context
selection: decodes a DPC++ frontend's possibly-multi-document
``-ast-dump=json`` output into a stream of ``FrontendContext`` records, and
selects the one matching a requested ``frontend_context`` (``"host"``/
``"device"``) kind.

**Why two channels, not one.** A DPC++ driver invocation (``icpx -fsycl
... -Xclang -ast-dump=json``) spawns one ``-cc1`` sub-invocation per
compilation pass (one ``host`` pass, one or more ``device`` passes per
offload target), and each sub-invocation's own AST dump is written to
stdout back-to-back with no separator — real document-boundary streaming
is required, not a bracket/string split (a naive brace counter would
already be wrong in general: a JSON string value can itself contain a
literal ``{``/``}`` character). The raw AST JSON alone carries no
``"host"``/``"device"`` label of its own — it is ordinary ``clang
-ast-dump=json`` output, oblivious to the driver-level split. That label
(and each pass's target triple, diagnostic-only — see
:func:`select_frontend_context`) comes from the driver's own ``-v``
diagnostic output on **stderr**: each real invocation line is shaped
``... -cc1 -triple <T> ... -fsycl-is-(host|device) ...``, in the same
order as its corresponding stdout document. Confirmed against a real
``icpx`` capture in ``tests/fixtures/g32/dpcpp/`` (``ast_dump.json`` +
``compiler_invocation.log``) — not a guessed format.

Callers (``dumper.py``) are responsible for actually requesting ``-v`` and
capturing stdout/stderr separately, and for deciding *whether* to route an
invocation through this module at all (a plain, non-DPC++ clang/castxml
invocation never reaches here — see that module's fallback-gating rule).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .errors import AstContextAmbiguousError, AstContextMissingError, SnapshotError

#: A real DPC++ driver `-cc1` invocation line on `-v` stderr output: names
#: both the compiled target triple and which pass (`host`/`device`) it is.
#: `-triple` always precedes `-fsycl-is-(host|device)` in observed `icpx`
#: output (confirmed against tests/fixtures/g32/dpcpp/compiler_invocation.log),
#: so this doesn't need to handle the reverse order.
_CC1_INVOCATION_RE = re.compile(
    r"-cc1\b.*?-triple\s+(?P<target>\S+).*?-fsycl-is-(?P<kind>host|device)\b"
)


@dataclass(frozen=True)
class FrontendContext:
    """One decoded AST document, correlated with the driver's own `-cc1`
    invocation metadata for the pass that produced it.

    ``kind`` (``"host"``/``"device"``) is what :func:`select_frontend_context`
    matches against a requested ``frontend_context`` — never ``target``,
    which is diagnostic-only (ADR-050 D5: two toolchains could plausibly
    label the same logical device pass with different target-triple
    spellings; only the driver's own explicit ``-fsycl-is-*`` flag is
    authoritative for *kind*).
    """

    kind: str
    target: str
    ast: dict[str, Any]


def decode_frontend_contexts(stdout: str, stderr: str) -> list[FrontendContext]:
    """Decode *stdout* (a DPC++ frontend's possibly-multi-document
    ``-ast-dump=json`` output) into a list of :class:`FrontendContext`,
    correlated against *stderr*'s ``-cc1`` invocation lines in the same
    order (see this module's own docstring for why two channels).

    Real streaming decode via repeated :meth:`json.JSONDecoder.raw_decode`
    calls, not a bracket/string split. An empty *stdout* (or one with no
    complete documents) decodes to an empty list — not an error here; a
    request against zero contexts is what :func:`select_frontend_context`'s
    own three-outcome logic turns into :class:`AstContextMissingError`
    (ADR-050 D5's "decodes to zero contexts" case is handled by the
    *selector*, not by this function refusing to decode). Genuinely
    malformed input — a document that starts but never finishes, or
    trailing bytes that aren't a valid JSON value — raises
    :class:`abicheck.errors.SnapshotError` immediately; that is a decode
    failure distinct from "there were simply no documents".
    """
    decoder = json.JSONDecoder()
    docs: list[dict[str, Any]] = []
    pos = 0
    length = len(stdout)
    while pos < length:
        stripped = stdout[pos:].lstrip()
        pos += len(stdout[pos:]) - len(stripped)
        if pos >= length:
            break
        try:
            doc, end = decoder.raw_decode(stdout, pos)
        except json.JSONDecodeError as exc:
            raise SnapshotError(
                "DPC++ frontend produced a truncated or malformed AST "
                f"document stream at offset {pos}: {exc}"
            ) from exc
        docs.append(doc)
        pos = end

    invocations = list(_CC1_INVOCATION_RE.finditer(stderr))
    if len(docs) != len(invocations):
        raise SnapshotError(
            f"DPC++ frontend produced {len(docs)} AST document(s) but "
            f"{len(invocations)} `-cc1 ... -fsycl-is-(host|device)` "
            "invocation(s) were observed on its `-v` stderr output -- "
            "cannot correlate documents to a host/device kind. This "
            "frontend invocation must always pass `-v` alongside "
            "`-ast-dump=json` for DPC++-capable compilers."
        )
    return [
        FrontendContext(kind=m.group("kind"), target=m.group("target"), ast=doc)
        for m, doc in zip(invocations, docs)
    ]


def select_frontend_context(
    contexts: list[FrontendContext], requested_kind: str
) -> FrontendContext:
    """Select the one context whose ``kind`` matches *requested_kind*.

    Three outcomes (ADR-050 D5): exactly one match selects; zero matches
    raises :class:`AstContextMissingError` (covers both "this kind was
    never produced" and "the decoded stream was empty" — the same
    underlying condition, an empty ``contexts`` list); more than one match
    raises :class:`AstContextAmbiguousError` — there is no implicit
    tiebreaker, e.g. picking the first. Selection is always by ``kind``,
    never by ``target`` triple pattern-matching (diagnostic-only, see
    :class:`FrontendContext`).
    """
    matches = [c for c in contexts if c.kind == requested_kind]
    if not matches:
        available = sorted({c.kind for c in contexts})
        raise AstContextMissingError(
            f"no AST context with kind={requested_kind!r} found among "
            f"{len(contexts)} decoded context(s) (available kinds: "
            f"{available!r}). Did you mean --frontend-context "
            f"{'device' if requested_kind == 'host' else 'host'}?"
        )
    if len(matches) > 1:
        targets = [c.target for c in matches]
        raise AstContextAmbiguousError(
            f"{len(matches)} AST contexts share kind={requested_kind!r} "
            f"(targets: {targets!r}) -- no implicit tiebreaker; narrow the "
            "request to a specific target."
        )
    return matches[0]

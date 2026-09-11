# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""L5 source-graph load/localize helpers (ADR-031; ADR-043 D1).

The former `graph compare`/`graph explain` CLI commands were removed
(ADR-043): the L5 graph is an internal consequence of `--depth source`, and
its diff/localization feed the source-depth report rather than a separate
top-level command (ADR-028 D3: they only ever explain/prioritize impact,
never decide or suppress an artifact-proven ABI break on their own). The two
loader/resolver functions here remain as plain, Click-free library code —
`cli_buildsource.py` still re-exports them (its lazy `__getattr__` shim) for
callers that resolved them from that module historically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import click

if TYPE_CHECKING:
    from .model.source_graph import SourceGraphSummary


def _load_source_graph(path: Path) -> SourceGraphSummary:
    """Load a source graph summary from a JSON file or an evidence-pack dir.

    Accepts either ``…/graph/source_graph_summary.json`` directly or a pack
    directory (the graph is read from its manifest layout). Raises a Click error
    when neither yields a graph so the failure is actionable.
    """
    from .errors import SnapshotError
    from .model.source_graph import SourceGraphSummary
    from .workflows.extraction import load_pack_or_raise

    if path.is_dir():
        try:
            pack = load_pack_or_raise(path)
        except SnapshotError as exc:
            raise click.ClickException(str(exc)) from exc
        if pack.source_graph is None:
            raise click.ClickException(
                f"Evidence pack at {path} has no L5 source graph."
            )
        return pack.source_graph
    if not path.is_file():
        raise click.ClickException(f"No source graph summary at {path}.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise click.ClickException(
            f"Cannot read source graph at {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise click.ClickException(f"{path} must contain a JSON object.")
    # ADR-063 Phase 10: a full snapshot document (e.g. one written by `dump
    # --sources -o out.abi.json`) carries its L5 graph nested inside the
    # snapshot -- under the top-level `surface_graph` key for a legacy flat
    # document (schema v29+), under `build_source.source_graph` for a
    # pre-Phase-3 flat document or the fallback when `surface_graph` was
    # never populated, or under `sections.graph.payload.surface_graph` for
    # the current single-file *sectioned* wire format (schema v42+,
    # `storage/sectioned_document.py`) -- never as top-level `nodes`/`edges`.
    # Decoding through `serialization.snapshot_from_dict` (rather than
    # hand-walking each shape) is what makes this reader forward-compatible
    # with the sectioned format for free; the graph is then read with the
    # same surface_graph-preferred, build_source.source_graph-fallback rule
    # every other Phase 10 reader uses. See `docs/contribute/plans/
    # one-semantic-pipeline.md`'s Phase 10 checklist.
    embedded = _embedded_source_graph(data)
    if embedded is not None:
        return embedded
    # SourceGraphSummary.from_dict is intentionally forgiving (it defaults a
    # missing nodes/edges to empty), so guard here: an unrelated JSON file (e.g.
    # a pack manifest) would otherwise load as an empty graph and report a bogus
    # diff instead of an actionable error.
    if not isinstance(data.get("nodes"), list) or not isinstance(
        data.get("edges"), list
    ):
        raise click.ClickException(
            f"{path} is not a source graph summary "
            "(expected top-level 'nodes' and 'edges' lists)."
        )
    return SourceGraphSummary.from_dict(data)


def _embedded_source_graph(data: dict[str, Any]) -> SourceGraphSummary | None:
    """The L5 graph nested inside a full snapshot document, ADR-063 Phase 10.

    Only attempted when *data* carries a top-level ``schema_version`` key --
    both the sectioned wire format (schema v42+) and every flat one carry it
    (``serialization.snapshot_from_dict`` itself branches on the sibling
    ``sections`` key; a `frontends`-layer module like this one may not import
    `storage` directly, per `architecture/modules.yaml`, so that branch is
    left entirely to `snapshot_from_dict`). A bare graph JSON has no such key;
    an unrelated JSON object (e.g. a pack manifest) may carry its own
    same-named but differently-scoped key, in which case decoding still fails
    safely below. Either way this returns ``None`` and the caller falls
    through to the bare-graph-JSON contract instead. A document that looks
    like a snapshot but fails to decode (corrupt, unsupported schema, or an
    unrelated document's own ``schema_version``) also returns ``None`` rather
    than raising here, so the caller's own "not a source graph summary" error
    still fires with an accurate message instead of an unrelated decode
    traceback.
    """
    if "schema_version" not in data:
        return None
    from .serialization import snapshot_from_dict

    try:
        snap = snapshot_from_dict(data)
    except Exception:
        return None
    # Phase 10 security correction: build_source.source_graph first (richer
    # real L3-L5 evidence than the always-on, header-only-only surface_graph).
    graph = (
        snap.build_source.source_graph if snap.build_source is not None else None
    ) or snap.surface_graph
    # Always a real SourceGraphSummary at runtime; narrows back from the
    # SurfaceGraphLike protocol model/snapshot.py's surface_graph field uses.
    return cast("SourceGraphSummary | None", graph)


def _resolve_symbol_from_report(report: Path, finding_id: str) -> str:
    """Resolve a symbol from a `compare --format json` report finding.

    ``finding_id`` may be a 0-based index into the report's changes, or a symbol
    substring to match. Returns the matched change's ``symbol`` (or "").
    """
    try:
        data = json.loads(Path(report).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"Cannot read report {report}: {exc}") from exc
    if not isinstance(data, dict):
        raise click.ClickException(f"Report {report} must contain a JSON object.")
    changes = data.get("changes") or data.get("findings") or []
    if not isinstance(changes, list):
        return ""
    if finding_id.isdigit():
        idx = int(finding_id)
        if 0 <= idx < len(changes) and isinstance(changes[idx], dict):
            return str(changes[idx].get("symbol", ""))
        return ""
    for change in changes:
        if (
            isinstance(change, dict)
            and finding_id
            and finding_id in str(change.get("symbol", ""))
        ):
            return str(change.get("symbol", ""))
    return ""

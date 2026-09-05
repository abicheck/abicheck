# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Which symbols a snapshot actually exports.

ADR-061 Phase 3: moved out of ``cli_buildsource_merge`` so engine code can ask
this question without importing the CLI layer. ``embed_build_source`` needs it
to seed L4 decl->symbol linking, and that was the last CLI dependency keeping
it there.

ADR-063 T7: :func:`exported_symbols_from_snapshot` is now a thin wrapper over
the canonical ``model.export_index.export_names_or_modeled_fallback``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.export_index import export_names_or_modeled_fallback

if TYPE_CHECKING:
    from ..model import AbiSnapshot


def exported_symbols_from_snapshot(snap: AbiSnapshot) -> tuple[str, ...]:
    """Exported (mangled) symbol names already parsed into *snap* — no re-dump.

    Used to plumb L0 exports into inline source replay (A1) for the
    ``dump <binary> --sources`` flow. Empty for a source-only snapshot. See
    ``model.export_index.export_names_or_modeled_fallback`` for the full
    raw-table-vs-modeled-fallback contract this delegates to.
    """
    return export_names_or_modeled_fallback(snap)

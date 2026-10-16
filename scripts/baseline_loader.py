"""Strict parsing for the committed gate baseline JSON files.

Five gates read a baseline: ``perf_baseline``, ``check_header_graph_perf``,
``l2_cli_gating``, ``mutation_scope`` and ``perf_antipatterns``. ``json`` accepts
the non-standard tokens ``NaN``, ``Infinity`` and ``-Infinity``, and every gate
comparison against one is ``False`` in the regressing direction, so a poisoned
baseline reads as a pass. This module is the one parser they share: it refuses
those tokens and otherwise returns exactly what ``json.loads`` returns.

The refusal is a :class:`json.JSONDecodeError`, so each caller's existing
handling of a malformed baseline covers a poisoned one too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class BaselineFormatError(json.JSONDecodeError):
    """A baseline carries a non-finite JSON number (``NaN`` or ``Infinity``)."""

    def __init__(self, token: str) -> None:
        super().__init__(
            f"non-finite number {token} is not allowed in a baseline", "", 0
        )


def _refuse_constant(token: str) -> Any:
    raise BaselineFormatError(token)


def parse_baseline(text: str) -> Any:
    """Parse baseline *text*, refusing ``NaN``/``Infinity`` anywhere in it."""
    return json.loads(text, parse_constant=_refuse_constant)


def read_baseline(path: Path) -> Any:
    """Read and strictly parse the baseline at *path* (UTF-8, as RFC 8259 requires)."""
    return parse_baseline(path.read_text(encoding="utf-8"))

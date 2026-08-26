"""Reusable Click-only option declarations (ADR-061 Phase 4 item 1)."""

from .secondary_output import (
    reject_incoherent_secondary_output,
    secondary_output_options,
)

__all__ = [
    "reject_incoherent_secondary_output",
    "secondary_output_options",
]

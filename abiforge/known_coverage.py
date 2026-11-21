"""The configurations a ``Known`` entry's ``when`` clause covers, as a committed record.

The no-growth ratchet (``check_known_no_growth``) keys each entry on its gap
string, so it catches an added entry or a removed one, but not a ``when`` clause
that quietly admits more configurations. This module records, per entry, which
configurations its ``when`` matches over a fixed probe grid, and the ratchet
fails when a later ``when`` covers a configuration the record does not hold.

The grid is ``STANDARDS`` x ``PROBE_TOOLCHAINS`` x ``PROBE_FLAGS`` x
``PROBE_EVIDENCE`` x ``PROBE_PLATFORMS``, every platform pinned explicitly. A covered set is stored as one bitmask per
``"<standard>|<toolchain>"`` pair, whose bit ``(p * len(flags) + i) * len(evidence) + j`` stands
for platform ``p``, flag set ``i`` at evidence ``j``. That keeps each entry near a kilobyte, where
the explicit configuration-id lists run to tens of kilobytes.

The record is exact: a covered set that grows is widening and needs review, and
one that shrinks is a narrowing whose record must shrink in the same change.
Either way the record is regenerated with
``scripts/check_ai_readiness.py --write-known-when``.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from abiforge.model.config import STANDARDS, Config, Flags, Standard, Toolchain

PROBE_TOOLCHAINS: tuple[Toolchain, ...] = (
    Toolchain("gcc", 13, "gcc-13", "g++-13"),
    Toolchain("gcc", 14, "gcc-14", "g++-14"),
    Toolchain("clang", 18, "clang-18", "clang++-18"),
    Toolchain("clang", 19, "clang-19", "clang++-19"),
)
PROBE_FLAGS: tuple[Flags, ...] = tuple(
    Flags(
        opt=opt,
        visibility=vis,
        bits=bits,
        short_enums=short,
        cxx11_abi=abi,
        pack=pack,
    )
    for opt, vis, bits, short, abi, pack in itertools.product(
        ("O0", "O2"),
        ("default", "hidden"),
        (64, 32),
        (False, True),
        (None, 0),
        (None, 1),
    )
)
#: Evidence lanes, spelled ``<level>[.stripped][.<header backend>]``: the
#: stripped ``{B,H}`` primary cell and the non-reference header backends are
#: their own lanes, so an entry gated on either records real cells.
PROBE_EVIDENCE: tuple[str, ...] = (
    "binary",
    "dwarf",
    "headers",
    "headers.stripped",
    "headers.clang",
    "headers.gcc",
    "headers.stripped.clang",
    "headers.stripped.gcc",
)


def _evidence_fields(lane: str) -> dict[str, Any]:
    """The ``Config`` fields one ``PROBE_EVIDENCE`` lane stands for."""
    level, *rest = lane.split(".")
    out: dict[str, Any] = {"evidence": level}
    for part in rest:
        if part == "stripped":
            out["stripped"] = True
        else:
            out["header_backend"] = part
    return out


#: (platform, object format) pinned for every probe configuration. A ``when``
#: may read ``objfmt``, so the grid enumerates the axis instead of taking the
#: runner's host (``Config``'s default), which made the record host-dependent.
PROBE_PLATFORMS: tuple[tuple[str, str], ...] = (
    ("linux", "elf"),
    ("darwin", "macho"),
    ("win32", "pe"),
)

#: The committed record, next to the module it describes.
BASELINE_PATH = Path(__file__).resolve().parent / "known_when_baseline.json"

Covered = dict[str, int]  # "<standard>|<toolchain id>" -> bitmask


def covered_masks(
    when: Callable[[Config], bool],
    *,
    stds: Iterable[Standard] = STANDARDS,
    toolchains: Sequence[Toolchain] = PROBE_TOOLCHAINS,
    flags: Sequence[Flags] = PROBE_FLAGS,
    evidence: Sequence[str] = PROBE_EVIDENCE,
    platforms: Sequence[tuple[str, str]] = PROBE_PLATFORMS,
) -> Covered:
    """The probe-grid configurations *when* matches, as one bitmask per standard and toolchain.

    A grid point the ``Config`` constructor rejects is never covered, so the
    result depends only on the grid and on *when*.
    """
    masks: Covered = {}
    width = len(evidence)
    for std in stds:
        for tc in toolchains:
            mask = 0
            for p, plat in enumerate(platforms):
                for i, fl in enumerate(flags):
                    for j, ev in enumerate(evidence):
                        try:
                            cfg = Config(
                                std=std,
                                producer=tc,
                                flags=fl,
                                platform=plat,
                                **_evidence_fields(ev),
                            )
                        except ValueError:
                            continue
                        if when(cfg):
                            mask |= 1 << ((p * len(flags) + i) * width + j)
            if mask:
                masks[f"{std.name}|{tc.id}"] = mask
    return masks


def known_record(
    entries: Iterable[Any], key_of: Callable[[Any], str], **grid: Any
) -> dict[str, dict[str, str]]:
    """The record for *entries*: ``"<key>#<n>"`` -> ``{pair: hex mask}``.

    *key_of* is the gate's ``known_entry_key``; ``<n>`` numbers entries that
    share a key, so a duplicate keeps its own record.
    """
    out: dict[str, dict[str, str]] = {}
    seen: dict[str, int] = {}
    for entry in entries:
        key = key_of(entry)
        n = seen.get(key, 0)
        seen[key] = n + 1
        masks = covered_masks(entry.when, **grid)
        out[f"{key}#{n}"] = {pair: f"{m:x}" for pair, m in sorted(masks.items())}
    return out


def widened_pairs(
    current: Mapping[str, str], recorded: Mapping[str, str]
) -> dict[str, int]:
    """For one entry: the pairs where *current* covers configurations *recorded* does not.

    Returns ``{pair: number of extra configurations}``; empty when *current* is
    a subset of *recorded* on every pair (unchanged or narrowed).
    """
    extra: dict[str, int] = {}
    for pair, hexmask in current.items():
        now = int(hexmask, 16)
        then = int(recorded.get(pair, "0"), 16)
        added = now & ~then
        if added:
            extra[pair] = bin(added).count("1")
    return extra


def narrowed_pairs(
    current: Mapping[str, str], recorded: Mapping[str, str]
) -> dict[str, int]:
    """For one entry: the pairs where *recorded* covers configurations *current* does not.

    The dual of ``widened_pairs``: a non-empty result means the record is stale
    and must be regenerated in the same change.
    """
    out: dict[str, int] = {}
    for pair, hexmask in recorded.items():
        then = int(hexmask, 16)
        now = int(current.get(pair, "0"), 16)
        dropped = then & ~now
        if dropped:
            out[pair] = bin(dropped).count("1")
    return out


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, dict[str, str]]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_baseline(
    record: Mapping[str, Mapping[str, str]], path: Path = BASELINE_PATH
) -> None:
    path.write_text(
        json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )

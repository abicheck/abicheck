#!/usr/bin/env python3
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

"""Shared conda-forge fetch → extract → ``abicheck compare`` engine.

This is the source-agnostic core behind the validation harness: given a version
pair (from *any* expectation source — a hand-curated manifest, the
abi-laboratory tracker oracle, or a future cross-checker), it resolves each
version to a conda-forge package, downloads and extracts the shared objects,
and runs ``abicheck compare`` to obtain a verdict. The verdict is then scored
against the source's expectation by the caller (``validate.py``).

Nothing here knows *where the expectation came from* — that is the whole point
of the unification: one engine, many sources.

Pure resolution/extraction helpers are unit-tested offline
(``tests/test_conda_harness.py``); only the network/conda/abicheck calls touch
the outside world.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
VALID_DIR = SCRIPTS_DIR.parent  # validation/

ANACONDA_API = "https://api.anaconda.org/package/conda-forge/{pkg}"
CONDA_CHANNEL = "https://conda.anaconda.org/conda-forge/"
USER_AGENT = "abicheck-validation/1.0 (+https://github.com/napetrov/abicheck)"

# Reuse the verdict normalisation/ranking from the scoring module.
sys.path.insert(0, str(SCRIPTS_DIR))
from fetch_tracker_oracle import _normalize_verdict, load_results_map  # noqa: E402

# Breaking-severity finding kinds that are *symbol-level or toolchain-level* and
# therefore sensitive to the public-header scope a header-driven oracle (ABICC /
# abi-laboratory) applies but a binary-only abicheck run cannot. When every
# breaking finding is one of these AND the oracle independently reports no public
# symbol changed (removed_symbols == 0, 100% backward compat), abicheck's
# stricter verdict is an *expected scope divergence*, not a false positive:
#   - func/var removals of exported-but-internal symbols (``_TIFF*``, ``_nettle_*``)
#     that live outside the public headers ABICC was given,
#   - size changes of internal exported data tables,
#   - removal of author-declared-internal version nodes,
#   - libstdc++ dual-ABI ``std::string`` (``Ss`` -> ``__cxx11``) symbol shifts
#     that come from a cross-toolchain rebuild, not an upstream source change.
#
# Every kind here is a *hard symbol-table / mangled-name fact* (a symbol is gone,
# its st_size differs, its ABI-tag set differs) that abicheck reads directly and
# cannot get wrong — so the only open question is public-ness, which the oracle's
# removal counter and backward-compat percentage corroborate. Deliberately
# EXCLUDED: ``func_params_changed``. A parameter/signature change is *inferred*
# from DWARF on a symbol that is still present, so (a) it can itself be an
# abicheck false positive on a genuinely public function, and (b) ``removed_symbols``
# does not speak to a still-present symbol's scope. Auto-excusing it could hide a
# real false positive, so it stays a scored disagreement (Codex review #349).
#
# This is also deliberately *not* a way to excuse type-level layout breaks (those
# stay scored as genuine disagreements); see validate._is_scope_divergence for
# the oracle-corroboration gate that makes this safe.
_SCOPE_SENSITIVE_BREAKING_KINDS = frozenset(
    {
        "func_removed",
        "func_removed_elf_only",
        "func_likely_renamed",
        "var_removed",
        "symbol_size_changed",
        "symbol_size_changed_internal",
        "symbol_version_node_removed",
        "abi_tag_changed",
    }
)


def conda_download_url(basename: str) -> str:
    """Build a conda-forge download URL from an anaconda.org ``basename``.

    The API ``basename`` already carries the subdir (e.g.
    ``linux-64/libxml2-2.9.4-4.tar.bz2``), so it appends directly to the
    channel root.
    """
    return CONDA_CHANNEL + basename.lstrip("/")


def build_number(basename: str) -> int:
    """Best-effort conda build number from a filename (for picking newest)."""
    stem = re.sub(r"\.(conda|tar\.bz2)$", "", basename.rsplit("/", 1)[-1])
    m = re.search(r"_(\d+)$", stem) or re.search(r"-(\d+)$", stem)
    return int(m.group(1)) if m else -1


def select_conda_basename(
    api_json: dict, version: str, subdir: str = "linux-64"
) -> str | None:
    """Pick the newest build's basename for ``version`` in ``subdir``.

    Returns ``None`` if the version is not published for that subdir. ``.tar.bz2``
    and ``.conda`` are both eligible; ties break toward the highest build number,
    then lexicographically (stable, deterministic).
    """
    candidates = [
        f["basename"]
        for f in api_json.get("files", [])
        if f.get("version") == version and f.get("attrs", {}).get("subdir") == subdir
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda b: (build_number(b), b))


def logical_name(path: str) -> str:
    """Strip ``.so`` suffix and any embedded version to a logical library name."""
    base = path.rsplit("/", 1)[-1]
    stem = base.split(".so")[0]
    return re.sub(r"-(?:\d+\.)+\d+$", "", stem)


def query_conda(pkg: str, timeout: float = 30.0) -> dict:
    """Fetch the anaconda.org file listing for a conda-forge package."""
    req = urllib.request.Request(
        ANACONDA_API.format(pkg=pkg), headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # fixed api host
        return json.loads(resp.read())


def fetch_file(url: str, dest: Path, timeout: float = 60.0) -> None:
    """Download ``url`` to ``dest`` (fixed conda-forge host)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # fixed conda host
        dest.write_bytes(resp.read())


def safe_extractall(tf: tarfile.TarFile, into: Path) -> None:
    """tarfile.extractall with the ``data`` filter when the runtime supports it.

    The ``filter`` kwarg predates some Python 3.10.x patch releases; conda
    packages are a trusted source, so fall back to a plain extract there.
    """
    try:
        tf.extractall(into, filter="data")
    except TypeError:
        tf.extractall(into)  # noqa: S202


def extract_tar_zst(zst_path: Path, into: Path) -> None:
    """Extract a ``.tar.zst`` into ``into``.

    Prefers pure-Python backends so the loop runs without a system zstd binary:
    the ``zstandard`` package, then the 3.14+ stdlib ``compression.zstd``; falls
    back to ``tar --zstd`` if only the CLI tool is present. Raises ``RuntimeError``
    when no backend is available (the caller turns that into a skipped pair).
    """
    try:
        import zstandard
    except ImportError:
        zstandard = None  # type: ignore[assignment]
    if zstandard is not None:
        with zst_path.open("rb") as fh:
            reader = zstandard.ZstdDecompressor().stream_reader(fh)
            with tarfile.open(fileobj=reader, mode="r|") as tf:
                safe_extractall(tf, into)
        return

    try:
        from compression import zstd  # type: ignore[import-not-found]  # Python 3.14+
    except ImportError:
        zstd = None  # type: ignore[assignment]
    if zstd is not None:
        with (
            zstd.ZstdFile(zst_path, "rb") as fh,
            tarfile.open(fileobj=fh, mode="r|") as tf,
        ):
            safe_extractall(tf, into)
        return

    if shutil.which("tar"):
        subprocess.run(
            ["tar", "--zstd", "-xf", str(zst_path), "-C", str(into)], check=True
        )
        return

    raise RuntimeError(
        f"cannot extract {zst_path.name}: no zstd backend available "
        "(pip install zstandard, or install zstd + GNU tar)"
    )


def extract_sos(pkg: Path, into: Path) -> dict[str, str]:
    """Extract shared objects from a conda package; return logical_name -> path.

    Handles ``.tar.bz2`` natively and ``.conda`` (a zip of zstd tarballs) via a
    pure-Python zstd backend, with a ``tar --zstd`` fallback. Only real
    (non-symlink) ``lib/*.so*`` files are kept.
    """
    into.mkdir(parents=True, exist_ok=True)
    if pkg.name.endswith(".tar.bz2"):
        with tarfile.open(pkg, "r:bz2") as tf:
            safe_extractall(tf, into)
    elif pkg.name.endswith(".conda"):
        with zipfile.ZipFile(pkg) as zf:
            inner = next(
                (
                    n
                    for n in zf.namelist()
                    if n.startswith("pkg-") and n.endswith(".tar.zst")
                ),
                None,
            )
            if inner is None:
                return {}
            zf.extract(inner, into)
        extract_tar_zst(into / inner, into)
    else:
        return {}

    out: dict[str, str] = {}
    for so in into.glob("lib/*.so*"):
        if so.is_symlink() or not so.is_file():
            continue
        try:
            with so.open("rb") as fh:
                if fh.read(4) != b"\x7fELF":
                    continue  # skip GNU ld linker scripts / other non-ELF .so files
        except OSError:
            continue
        out[logical_name(so.name)] = str(so)
    return out


def run_abicheck(old: str, new: str, old_ver: str, new_ver: str) -> dict | None:
    """Run ``abicheck compare`` on two .so files and return the parsed JSON."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out_path = tmp.name
    cmd = [
        "abicheck",
        "compare",
        old,
        new,
        "--old-version",
        old_ver,
        "--new-version",
        new_ver,
        "--format",
        "json",
        "-o",
        out_path,
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(Path(out_path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    finally:
        # NamedTemporaryFile(delete=False) leaves the file behind; over a full
        # run (hundreds of pairs x several .so) that litters the temp dir.
        Path(out_path).unlink(missing_ok=True)
    return data if isinstance(data, dict) else None


def verdict_of(data: dict) -> str | None:
    """Pull the top-level verdict out of an ``abicheck compare`` JSON result."""
    summ = data.get("summary", {}) if isinstance(data, dict) else {}
    return data.get("verdict") or summ.get("verdict")


def abicheck_verdict(old: str, new: str, old_ver: str, new_ver: str) -> str | None:
    """Run ``abicheck compare`` on two .so files and return its verdict."""
    data = run_abicheck(old, new, old_ver, new_ver)
    return verdict_of(data) if data is not None else None


def _breaking_changes(data: dict) -> list[dict]:
    """Return the breaking-severity findings from an ``abicheck compare`` result."""
    changes = data.get("changes") or data.get("findings") or []
    return [
        c for c in changes if isinstance(c, dict) and c.get("severity") == "breaking"
    ]


def scope_sensitive_breaking_only(data: dict) -> bool:
    """True when a result is breaking *and every* breaking finding is scope-sensitive.

    Returns False when there are no breaking findings (nothing to explain) or
    when any breaking finding is a non-symbol/non-toolchain kind (e.g. a
    type-level layout break), which must stay a genuine disagreement.
    """
    breaking = _breaking_changes(data)
    if not breaking:
        return False
    return all(c.get("kind") in _SCOPE_SENSITIVE_BREAKING_KINDS for c in breaking)


def resolve_pair(pair: dict, api: dict, subdir: str) -> tuple[str, str] | None:
    """Resolve a pair to (old_basename, new_basename), or ``None`` if unavailable.

    A source may pin exact conda filenames (``old_file``/``new_file``, e.g. the
    curated manifest); otherwise the newest build for each version is resolved
    from the anaconda.org listing.
    """
    if pair.get("old_file") and pair.get("new_file"):
        return f"{subdir}/{pair['old_file']}", f"{subdir}/{pair['new_file']}"
    ob = select_conda_basename(api, pair["old_ver"], subdir)
    nb = select_conda_basename(api, pair["new_ver"], subdir)
    if not ob or not nb:
        return None
    return ob, nb


def has_dwarf(so_path: str) -> bool:
    """True if the ELF shared object carries a ``.debug_info`` section.

    Stripped release binaries (typical on conda-forge) have none, so abicheck
    can only see the symbol table — it cannot observe type-level ABI changes.
    Note this only proves a debug *section* exists, not that it covers a
    meaningful surface — see :func:`has_type_evidence`.
    """
    try:
        out = subprocess.run(
            ["readelf", "-S", so_path], capture_output=True, text=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return ".debug_info" in out


def _exported_func_count(so_path: str) -> int:
    """Count defined (non-undefined) exported FUNC dynamic symbols."""
    try:
        out = subprocess.run(
            ["readelf", "--dyn-syms", "-W", so_path], capture_output=True, text=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    return sum(
        1 for line in out.splitlines() if " FUNC " in line and " UND " not in line
    )


def _dwarf_subprogram_count(so_path: str) -> int:
    """Count ``DW_TAG_subprogram`` DIEs in the binary's DWARF (function coverage)."""
    try:
        out = subprocess.run(
            ["readelf", "--debug-dump=info", so_path], capture_output=True, text=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    return out.count("DW_TAG_subprogram")


def has_type_evidence(so_path: str, min_coverage: float = 0.25) -> bool:
    """True only when the binary's DWARF actually *covers* its exported surface.

    A ``.debug_info`` section can be present yet sparse — e.g. conda's openssl
    ``libcrypto`` carries debug info for only a couple of compilation units (4
    ``DW_TAG_subprogram`` DIEs against ~4200 exported functions), so abicheck
    cannot observe the specific public-API change ABICC saw from a full-headers
    source build (validation parity class D — openssl 1.1.1a→1.1.1b). Presence of
    a debug section therefore does **not** prove usable type evidence.

    This requires the DWARF to describe at least ``min_coverage`` of the exported
    functions before the binary is treated as type-evidenced. A genuinely
    debug-built library (e.g. nettle: ~1400 subprograms / ~430 exported funcs)
    clears it easily; a partially-stripped one does not. A library that exports no
    functions (pure data) cannot be measured this way, so the section's presence
    is accepted as-is.
    """
    if not has_dwarf(so_path):
        return False
    funcs = _exported_func_count(so_path)
    if funcs == 0:
        return True
    return _dwarf_subprogram_count(so_path) >= min_coverage * funcs


def evaluate_pair(
    pair: dict,
    api: dict,
    subdir: str,
    tmp: Path,
    idx: int,
    evidence: dict | None = None,
) -> str | None:
    """Fetch, extract, and run abicheck for one pair; return the verdict.

    Source-agnostic: ``pair`` only needs ``old_ver``/``new_ver``/``pair`` (and may
    carry ``old_file``/``new_file`` to pin exact builds). Returns ``None`` when the
    pair can't be evaluated (not on conda-forge, fetch/extract error, no shared
    object common to both versions, or abicheck produced no verdict). Prints a
    per-pair line on success and a diagnostic on hard errors. The loop index
    ``idx`` gives every attempt a fresh extraction slot so a skipped pair can't
    leak stale ``.so`` files into the next.

    If ``evidence`` is provided, records ``evidence[pair] = {"has_dwarf": bool}``
    so the caller can tell whether abicheck had type-level evidence (a stripped
    binary can't reveal type-only ABI changes that a debug-build oracle saw).
    """
    pid = pair["pair"]
    resolved = resolve_pair(pair, api, subdir)
    if resolved is None:
        return None  # version not on conda-forge; stays UNCOMPARABLE upstream
    ob, nb = resolved

    try:
        # Preserve the real extension so extract_sos can dispatch on it.
        op = tmp / f"old_{idx}_{Path(ob).name}"
        npath = tmp / f"new_{idx}_{Path(nb).name}"
        fetch_file(conda_download_url(ob), op)
        fetch_file(conda_download_url(nb), npath)
        old_sos = extract_sos(op, tmp / f"old_{idx}")
        new_sos = extract_sos(npath, tmp / f"new_{idx}")
    except (
        OSError,
        tarfile.TarError,
        subprocess.CalledProcessError,
        RuntimeError,
    ) as exc:
        print(f"  {pid}: fetch/extract failed: {exc}", file=sys.stderr)
        return None

    common = sorted(set(old_sos) & set(new_sos))
    if not common:
        return None
    # Take the most-breaking verdict across the shared objects in the pair.
    ov, nv = pair["old_ver"], pair["new_ver"]
    datas = {
        name: d
        for name in common
        if (d := run_abicheck(old_sos[name], new_sos[name], ov, nv)) is not None
    }
    if not datas:
        return None
    verdict = load_results_map(
        [{"pair": pid, "verdict": verdict_of(d)} for d in datas.values()]
    )[pid]
    if evidence is not None:
        # A pair is a scope divergence only if *every* shared object whose
        # verdict is breaking has exclusively scope-sensitive breaking findings;
        # one genuine break anywhere keeps the pair a real disagreement.
        breaking_datas = [
            d
            for d in datas.values()
            if _normalize_verdict(verdict_of(d) or "") == "BREAKING"
        ]
        # Type-level diffing needs usable debug info on BOTH sides: if only the
        # new build carries DWARF and the old one is stripped, abicheck still
        # cannot compare old-vs-new layouts, so a type-only oracle break is an
        # evidence limit, not a miss (Codex review #349). ``has_type_evidence``
        # additionally rejects a *present-but-sparse* ``.debug_info`` section that
        # does not cover the exported surface (parity class D — openssl), so a
        # partial-DWARF binary is correctly treated as evidence-limited rather
        # than scored as a false negative.
        evidence[pid] = {
            "has_type_evidence": any(
                has_type_evidence(old_sos[name]) and has_type_evidence(new_sos[name])
                for name in common
            ),
            "scope_divergent": bool(breaking_datas)
            and all(scope_sensitive_breaking_only(d) for d in breaking_datas),
        }
    print(
        f"  {pid}: abicheck={verdict} expected={pair.get('expected_verdict')} "
        f"({','.join(common)})"
    )
    return verdict

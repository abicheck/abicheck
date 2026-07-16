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

"""Package extraction layer for compare-release (ADR-006).

Converts RPM, Deb, tar, conda, pip wheel packages into directories that
the existing compare-release pipeline can process.

The extraction flow is:

    Package → Extract → Directory → [compare-release] → AggregateResult

All extractors enforce strict security checks against path traversal,
symlink escapes, absolute paths, and special file types (character/block
devices, FIFOs).  See ``_validate_member_path()`` and
``TarExtractor._safe_extract()`` for the mandatory safety contract.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Protocol, runtime_checkable

from .errors import ExtractionSecurityError, SnapshotError

_log = logging.getLogger(__name__)

# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class ExtractResult:
    """Result of extracting a package."""

    lib_dir: Path  # path to extracted shared libraries
    debug_dir: Path | None = None  # path to extracted debug info
    header_dir: Path | None = None  # path to extracted headers (devel pkg)
    metadata: dict[str, str] = field(default_factory=dict)


# ── Security validation ──────────────────────────────────────────────────────


def _validate_member_path(member_name: str, target_root: Path) -> Path:
    """Validate that an archive member path is safe to extract.

    Raises ExtractionSecurityError if the member contains path traversal,
    absolute paths, or resolves outside the extraction root.
    """
    # Reject absolute paths (check both OS-native and POSIX-style leading slash
    # so that "/etc/passwd" is caught on Windows too, where os.path.isabs("/…") is False)
    if os.path.isabs(member_name) or member_name.startswith("/"):
        raise ExtractionSecurityError(member_name, "absolute path in archive member")

    # Reject path traversal components
    parts = Path(member_name).parts
    if ".." in parts:
        raise ExtractionSecurityError(member_name, "path traversal via '..' component")

    # Canonicalize and verify destination stays within root
    dest = (target_root / member_name).resolve()
    root_resolved = target_root.resolve()
    try:
        dest.relative_to(root_resolved)
    except ValueError:
        raise ExtractionSecurityError(
            member_name, f"resolved path escapes extraction root: {dest}"
        )

    return dest


def _validate_symlink_target(
    member_name: str, link_target: str, target_root: Path
) -> None:
    """Validate that a symlink target resolves within the extraction root."""
    member_parent = (target_root / member_name).resolve().parent
    resolved = (member_parent / link_target).resolve()
    root_resolved = target_root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise ExtractionSecurityError(
            member_name,
            f"symlink target '{link_target}' resolves outside extraction root: {resolved}",
        )


# ── Protocol ─────────────────────────────────────────────────────────────────


@runtime_checkable
class PackageExtractor(Protocol):
    """Extract package contents to a temporary directory."""

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        """Extract package into target_dir and return extraction result."""
        ...

    def detect(self, pkg_path: Path) -> bool:
        """Return True if this extractor can handle the given path."""
        ...


# ── Tar extractor ────────────────────────────────────────────────────────────


class TarExtractor:
    """Extract tar, tar.gz, tar.xz, tar.bz2, and .tgz archives."""

    def detect(self, pkg_path: Path) -> bool:
        name = pkg_path.name.lower()
        return name.endswith((".tar", ".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tgz"))

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        _log.info("Extracting tar archive: %s", pkg_path)
        if pkg_path.name.lower().endswith(".tar.zst"):
            self._safe_extract_zst_tar(pkg_path, target_dir)
        else:
            self._safe_extract(pkg_path, target_dir)
        return ExtractResult(lib_dir=target_dir)

    @staticmethod
    def _safe_extract(archive_path: Path, target_dir: Path) -> None:
        """Extract tar archive with full security validation on every member.

        Validates each member before extraction:
        - Rejects absolute paths and path traversal (via ``_validate_member_path``)
        - Rejects symlinks that escape the extraction root
        - Rejects special file types (character/block devices, FIFOs) that could
          create dangerous filesystem entries
        """
        target_root = target_dir.resolve()
        with tarfile.open(archive_path) as tf:
            for member in tf.getmembers():
                _validate_member_path(member.name, target_root)

                # Reject special device/FIFO types that should never appear
                # in package archives (security risk on extraction)
                if member.ischr() or member.isblk() or member.isfifo():
                    raise ExtractionSecurityError(
                        member.name,
                        "archive contains a device or FIFO entry",
                    )

                if member.issym():
                    _validate_symlink_target(
                        member.name, member.linkname, target_root
                    )
                elif member.islnk():
                    # Hardlink targets are archive member names, not
                    # filesystem-relative paths — validate as a member path.
                    _validate_member_path(member.linkname, target_root)

            # All members validated — now extract
            # Use data_filter if available (Python 3.12+), otherwise manual
            if sys.version_info >= (3, 12):
                tf.extractall(path=target_dir, filter="data")  # nosec B202 — members validated above
            else:
                tf.extractall(path=target_dir)  # nosec B202 — members validated above

    @staticmethod
    def _safe_extract_zst_tar(zst_path: Path, target_dir: Path) -> None:
        """Extract a zstd-compressed tar archive with the normal tar safety checks."""
        staging = Path(tempfile.mkdtemp(dir=target_dir, prefix=".abicheck-zst-"))
        tar_path = staging / "payload.tar"
        try:
            try:
                import zstandard
            except ImportError:
                zstandard_mod: Any | None = None
            else:
                zstandard_mod = zstandard
            if zstandard_mod is not None:
                dctx = zstandard_mod.ZstdDecompressor()
                with open(zst_path, "rb") as compressed, open(tar_path, "wb") as out:
                    with dctx.stream_reader(compressed) as reader:
                        shutil.copyfileobj(reader, out)
            else:
                zstd = shutil.which("zstd")
                if zstd is None:
                    raise SnapshotError(
                        "Cannot extract .tar.zst: install 'zstandard' Python package "
                        "or 'zstd' command-line tool."
                    )
                subprocess.run(
                    [zstd, "-d", "-f", str(zst_path), "-o", str(tar_path)],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
            TarExtractor._safe_extract(tar_path, target_dir)
        finally:
            shutil.rmtree(staging, ignore_errors=True)


# ── RPM extractor ────────────────────────────────────────────────────────────

_RPM_MAGIC = b"\xed\xab\xee\xdb"


class RpmExtractor:
    """Extract RPM packages using rpm2cpio + cpio."""

    def detect(self, pkg_path: Path) -> bool:
        name = pkg_path.name.lower()
        if name.endswith(".rpm"):
            return True
        # Check magic bytes
        try:
            with open(pkg_path, "rb") as f:
                return f.read(4) == _RPM_MAGIC
        except OSError:
            return False

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        _log.info("Extracting RPM: %s", pkg_path)
        self._rpm_extract(pkg_path, target_dir)
        self._post_validate(target_dir)
        return ExtractResult(lib_dir=target_dir)

    @staticmethod
    def _rpm_extract(rpm_path: Path, target_dir: Path) -> None:
        """Extract RPM via rpm2cpio | cpio pipeline."""
        rpm2cpio = shutil.which("rpm2cpio")
        cpio = shutil.which("cpio")
        if not rpm2cpio:
            raise SnapshotError(
                "rpm2cpio not found. Install rpm-tools or use a tar archive instead."
            )
        if not cpio:
            raise SnapshotError(
                "cpio not found. Install cpio or use a tar archive instead."
            )

        _EXTRACT_TIMEOUT = 120  # seconds

        rpm2cpio_proc = subprocess.Popen(
            [rpm2cpio, str(rpm_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        cpio_proc = subprocess.Popen(
            [cpio, "-id", "--no-absolute-filenames", "--quiet"],
            stdin=rpm2cpio_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(target_dir),
        )
        # Allow rpm2cpio to receive SIGPIPE
        if rpm2cpio_proc.stdout:
            rpm2cpio_proc.stdout.close()

        try:
            _cpio_out, cpio_err = cpio_proc.communicate(timeout=_EXTRACT_TIMEOUT)
        except subprocess.TimeoutExpired:
            cpio_proc.kill()
            rpm2cpio_proc.kill()
            cpio_proc.wait()
            rpm2cpio_proc.wait()
            raise SnapshotError(
                f"RPM extraction timed out after {_EXTRACT_TIMEOUT}s"
            )

        try:
            rpm2cpio_proc.wait(timeout=_EXTRACT_TIMEOUT)
        except subprocess.TimeoutExpired:
            rpm2cpio_proc.kill()
            rpm2cpio_proc.wait()
            raise SnapshotError(
                f"rpm2cpio timed out after {_EXTRACT_TIMEOUT}s"
            )

        if rpm2cpio_proc.returncode != 0:
            raise SnapshotError(f"rpm2cpio failed (exit {rpm2cpio_proc.returncode})")
        if cpio_proc.returncode != 0:
            err_msg = cpio_err.decode("utf-8", errors="replace").strip()
            raise SnapshotError(f"cpio extraction failed: {err_msg}")

    @staticmethod
    def _post_validate(target_dir: Path) -> None:
        """Post-extraction validation: check no paths escape root.

        Iterates both directory and file entries to catch directory symlinks
        or escaped paths that file-only validation would miss.  Uses
        ``topdown=True`` so directory symlinks are validated before descent.
        """
        root = target_dir.resolve()
        for dirpath, dirnames, filenames in os.walk(
            target_dir, followlinks=False, topdown=True
        ):
            dp = Path(dirpath)
            # Validate both directory and file entries
            for name in list(dirnames) + filenames:
                full = (dp / name).resolve()
                try:
                    full.relative_to(root)
                except ValueError:
                    raise ExtractionSecurityError(
                        str(full), "extracted path escapes extraction root"
                    )
                # Check symlinks (files and directories)
                fp = dp / name
                if fp.is_symlink():
                    link_target = os.readlink(fp)
                    resolved = fp.resolve()
                    try:
                        resolved.relative_to(root)
                    except ValueError:
                        raise ExtractionSecurityError(
                            str(fp.relative_to(target_dir)),
                            f"symlink target '{link_target}' escapes extraction root",
                        )


# ── Deb extractor ────────────────────────────────────────────────────────────

_DEB_MAGIC = b"!<arch>\n"


class DebExtractor:
    """Extract Debian packages using ar + tar."""

    def detect(self, pkg_path: Path) -> bool:
        name = pkg_path.name.lower()
        if name.endswith(".deb"):
            return True
        try:
            with open(pkg_path, "rb") as f:
                return f.read(8) == _DEB_MAGIC
        except OSError:
            return False

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        _log.info("Extracting Deb: %s", pkg_path)
        self._deb_extract(pkg_path, target_dir)
        return ExtractResult(lib_dir=target_dir)

    def _deb_extract(self, deb_path: Path, target_dir: Path) -> None:
        """Extract Debian package: ar x to get data.tar.*, then tar extract."""
        ar = shutil.which("ar")
        if not ar:
            raise SnapshotError(
                "ar not found. Install binutils or use a tar archive instead."
            )

        # ar extract into a staging area
        staging = Path(tempfile.mkdtemp(dir=target_dir, prefix=".deb_staging_"))
        try:
            subprocess.run(
                [ar, "x", str(deb_path.resolve())],
                cwd=str(staging),
                check=True,
                capture_output=True,
                timeout=120,
            )

            # Find data.tar.* member
            data_tar = None
            for candidate in staging.iterdir():
                if candidate.name.startswith("data.tar"):
                    data_tar = candidate
                    break

            if data_tar is None:
                raise SnapshotError(
                    f"No data.tar.* found in Deb package: {deb_path}"
                )

            # Extract data.tar.* with security checks
            if data_tar.name.endswith(".tar.zst"):
                TarExtractor._safe_extract_zst_tar(data_tar, target_dir)
            else:
                TarExtractor._safe_extract(data_tar, target_dir)
        finally:
            shutil.rmtree(staging, ignore_errors=True)


# ── Zip-based security helper ────────────────────────────────────────────────


def _safe_zip_extract(archive_path: Path, target_dir: Path) -> None:
    """Extract a zip archive with full security validation on every member."""
    target_root = target_dir.resolve()
    with zipfile.ZipFile(archive_path, "r") as zf:
        for info in zf.infolist():
            _validate_member_path(info.filename, target_root)
        zf.extractall(path=target_dir)  # nosec B202 — members validated above


# ── Conda extractor ─────────────────────────────────────────────────────────


class CondaExtractor:
    """Extract conda packages (.conda v2 format and legacy .tar.bz2).

    .conda format is a zip archive containing:
      - metadata.json
      - pkg-<name>-<hash>.tar.zst  (package payload)
      - info-<name>-<hash>.tar.zst (metadata)

    Legacy .tar.bz2 conda packages are plain bzip2-compressed tarballs.
    """

    def detect(self, pkg_path: Path) -> bool:
        name = pkg_path.name.lower()
        if name.endswith(".conda"):
            return True
        # Legacy conda packages end with .tar.bz2 but we need to distinguish
        # from generic tar.bz2.  Check for conda-style naming:
        # <name>-<version>-<build>.tar.bz2
        if name.endswith(".tar.bz2") and name.count("-") >= 2:
            # Peek inside for info/ directory (conda marker)
            try:
                with tarfile.open(pkg_path, "r:bz2") as tf:
                    names = tf.getnames()
                    return any(n.startswith("info/") for n in names[:50])
            except (tarfile.TarError, OSError):
                return False
        return False

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        _log.info("Extracting conda package: %s", pkg_path)
        name = pkg_path.name.lower()

        if name.endswith(".conda"):
            self._extract_v2(pkg_path, target_dir)
        else:
            # Legacy .tar.bz2 format
            TarExtractor._safe_extract(pkg_path, target_dir)

        return ExtractResult(lib_dir=target_dir)

    @staticmethod
    def _extract_v2(conda_path: Path, target_dir: Path) -> None:
        """Extract .conda v2 format (zip containing tar.zst payloads)."""
        # First extract the outer zip
        staging = Path(tempfile.mkdtemp(dir=target_dir, prefix=".conda_staging_"))
        try:
            _safe_zip_extract(conda_path, staging)

            # Find and extract pkg-*.tar.zst (the main payload)
            for member in staging.iterdir():
                if member.name.startswith("pkg-") and member.name.endswith(".tar.zst"):
                    CondaExtractor._extract_zst_tar(member, target_dir)
                elif member.name.startswith("info-") and member.name.endswith(".tar.zst"):
                    # Also extract info for metadata
                    info_dir = target_dir / "info"
                    info_dir.mkdir(exist_ok=True)
                    CondaExtractor._extract_zst_tar(member, info_dir)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _extract_zst_tar(zst_path: Path, target_dir: Path) -> None:
        """Extract a .tar.zst file using zstd + tar or Python zstandard."""
        TarExtractor._safe_extract_zst_tar(zst_path, target_dir)


# ── Wheel (pip) extractor ────────────────────────────────────────────────────


# manylinux platform-tag -> the glibc version it promises as a ceiling
# (PEP 600's ``manylinux_<glibc_major>_<glibc_minor>`` plus the three frozen
# legacy aliases PEP 600 defines as exact synonyms). G10: a wheel's filename
# tag is a promise about the *maximum* glibc symbol version its binaries may
# require — see docs/development/plans/g10-glibc-floor-check.md.
_MANYLINUX_LEGACY_FLOORS: dict[str, tuple[int, int]] = {
    "manylinux1": (2, 5),
    "manylinux2010": (2, 12),
    "manylinux2014": (2, 17),
}
_MANYLINUX_TAG_RE = re.compile(
    r"manylinux(?:_(?P<major>\d+)_(?P<minor>\d+)|(?P<legacy>1|2010|2014))(?=_|$)"
)


def parse_manylinux_glibc_floor(name: str) -> str | None:
    """Derive the strictest declared glibc floor from a manylinux tag string.

    *name* is typically a wheel filename (or its platform-tag segment) such
    as ``scipy-1.18.0-cp312-cp312-manylinux_2_17_x86_64.whl``, which may
    carry a compressed multi-tag platform segment (PEP 600), e.g.
    ``...manylinux_2_17_x86_64.manylinux2014_x86_64.whl`` when a wheel
    declares compatibility with more than one baseline. A multi-tag wheel
    is claiming to work on *every* listed baseline, so the strictest (lowest)
    glibc version among them is the one an actual binary must not exceed.

    When *name* ends in ``.whl``, only its platform-tag segment (the last
    ``-``-delimited component before the extension, per the PEP 427 wheel
    filename spec ``{distribution}-{version}(-{build})?-{python}-{abi}-
    {platform}.whl``) is scanned — not the whole filename. Otherwise a
    ``manylinux``-prefixed *distribution* name (e.g.
    ``manylinux_2_17_helper-1.0-cp312-cp312-linux_x86_64.whl``, whose
    platform tag makes no manylinux promise at all) would be misread as a
    manylinux tag.

    Returns a dotted ``"X.Y"`` string suitable for
    ``EnvironmentMatrix.runtime_floors["GLIBC"]``, or ``None`` if *name*
    carries no recognizable manylinux tag.
    """
    tag_segment = name
    if name.lower().endswith(".whl"):
        stem = name[: -len(".whl")]
        tag_segment = stem.rsplit("-", 1)[-1]
    best: tuple[int, int] | None = None
    for m in _MANYLINUX_TAG_RE.finditer(tag_segment):
        if m.group("legacy"):
            version = _MANYLINUX_LEGACY_FLOORS[f"manylinux{m.group('legacy')}"]
        else:
            version = (int(m.group("major")), int(m.group("minor")))
        if best is None or version < best:
            best = version
    return f"{best[0]}.{best[1]}" if best is not None else None


#: Matches a PEP 425 Python tag: an implementation abbreviation (``cp``
#: CPython, ``pp`` PyPy, ``py`` generic, ``ip`` IronPython, ``jy`` Jython)
#: followed by a single-digit major version and one-or-more-digit minor
#: version run together, e.g. ``cp311`` -> major ``3``, minor ``11``. A bare
#: implementation with no minor digits (``py3``) is intentionally
#: unmatched — it doesn't pin a specific minor version, so there's nothing
#: useful to derive.
_WHEEL_PYTHON_TAG_RE = re.compile(r"^(?:cp|pp|py|ip|jy)(\d)(\d+)$")


def _python_version_from_wheel_filename(filename: str) -> str | None:
    """Derive ``python_version`` (e.g. ``"3.11"``) from a wheel filename's
    own Python tag, for use as a PEP 508 marker-evaluation environment.

    *filename* is a wheel filename ``{distribution}-{version}(-{build})?-
    {python tag}-{abi tag}-{platform tag}.whl`` (PEP 427); the Python tag is
    always the third-from-last ``-``-delimited segment regardless of
    whether an optional build tag is present. Returns ``None`` when
    *filename* isn't a recognizable wheel name or its Python tag doesn't
    pin a specific minor version (e.g. the generic ``py3``).
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    m = _WHEEL_PYTHON_TAG_RE.match(parts[-3])
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _python_full_version_from_wheel_filename(filename: str) -> str | None:
    """Derive ``python_full_version`` from a wheel filename's Python tag.

    A wheel tag only ever encodes major.minor (``cp311`` says nothing
    about the micro/patch version), so this appends a synthetic ``.0`` to
    :func:`_python_version_from_wheel_filename`'s result rather than
    guessing a specific patch release. That's still correct for any marker
    comparison written at the same minor-version granularity a wheel tag
    itself uses (e.g. ``python_full_version < "3.12"``/``>= "3.12"``,
    since PEP 440 version comparison places ``3.11.0`` on the correct side
    either way) — leaving ``python_full_version`` to the host default
    while ``python_version`` is correctly derived would otherwise let a
    marker written in the ``_full_version`` spelling evaluate against the
    wrong interpreter (Codex review). Only a marker checking an exact
    micro/patch version a wheel tag can't express at all would see a
    difference, which no derivation could fix.
    """
    version = _python_version_from_wheel_filename(filename)
    return f"{version}.0" if version is not None else None


#: Wheel Python-tag implementation abbreviation -> PEP 508 marker value, in
#: each of the two marker spellings (``implementation_name`` uses
#: ``sys.implementation.name``'s lowercase spelling;
#: ``platform_python_implementation`` uses ``platform.python_implementation()``'s
#: capitalized spelling). The generic ``py`` abbreviation is deliberately
#: absent from both maps -- it's an implementation-agnostic tag (a
#: pure-Python wheel meant to run under CPython, PyPy, or anything else),
#: so it makes no implementation promise at all to derive.
_WHEEL_IMPLEMENTATION_NAMES = {
    "cp": "cpython",
    "pp": "pypy",
    "ip": "ironpython",
    "jy": "jython",
}
_WHEEL_PYTHON_IMPLEMENTATIONS = {
    "cp": "CPython",
    "pp": "PyPy",
    "ip": "IronPython",
    "jy": "Jython",
}


def _implementation_name_from_wheel_filename(filename: str) -> str | None:
    """Derive ``implementation_name`` (e.g. ``"cpython"``/``"pypy"``) from a
    wheel filename's own Python tag, the same way as
    :func:`_python_version_from_wheel_filename`.

    A ``pp39``-tagged (PyPy) wheel scanned while abicheck itself runs under
    CPython would otherwise have an ``implementation_name``-gated marker
    evaluate against the wrong implementation (Codex review). Returns
    ``None`` for a non-wheel filename, a Python tag that doesn't pin a
    specific minor version, or the implementation-agnostic generic ``py``
    tag (see :data:`_WHEEL_IMPLEMENTATION_NAMES`).
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    tag = parts[-3]
    if not _WHEEL_PYTHON_TAG_RE.match(tag):
        return None
    return _WHEEL_IMPLEMENTATION_NAMES.get(tag[:2])


def _platform_python_implementation_from_wheel_filename(filename: str) -> str | None:
    """Derive ``platform_python_implementation`` (e.g.
    ``"CPython"``/``"PyPy"``) from a wheel filename's own Python tag.

    A different, equally common PEP 508 marker spelling for the same
    distinction as :func:`_implementation_name_from_wheel_filename`
    (``platform_python_implementation == "PyPy"`` vs.
    ``implementation_name == "pypy"``); deriving only one still leaves a
    marker written in the other spelling falling back to the host running
    abicheck (Codex review). Same scope caveats as that function.
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    tag = parts[-3]
    if not _WHEEL_PYTHON_TAG_RE.match(tag):
        return None
    return _WHEEL_PYTHON_IMPLEMENTATIONS.get(tag[:2])


def _platform_system_from_wheel_filename(filename: str) -> str | None:
    """Derive ``platform_system`` (``"Linux"``/``"Darwin"``/``"Windows"``)
    from a wheel filename's own platform tag (the last ``-``-delimited
    segment before ``.whl``), for the same reason as
    :func:`_python_version_from_wheel_filename`: a
    ``python_version``-gated marker correctly scoped to the wheel's own
    interpreter is no help if a ``platform_system``-gated one right next to
    it still falls back to the host running abicheck (Codex review).

    Deliberately does *not* attempt ``platform_machine`` — a fat/universal
    macOS wheel (``macosx_11_0_universal2``) or a compressed multi-tag
    platform segment doesn't name a single unambiguous architecture, and
    guessing wrong would be worse than falling back to the host default.
    Returns ``None`` for a non-wheel filename, the pure-Python ``any`` tag
    (no platform binding at all), or an unrecognized platform tag prefix.
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    tag = parts[-1].lower()
    if tag.startswith(("manylinux", "musllinux", "linux")):
        return "Linux"
    if tag.startswith("macosx"):
        return "Darwin"
    if tag.startswith("win"):
        return "Windows"
    return None


def _sys_platform_from_wheel_filename(filename: str) -> str | None:
    """Derive ``sys_platform`` (``"linux"``/``"darwin"``/``"win32"``, i.e.
    Python's own ``sys.platform`` spelling) from a wheel filename's platform
    tag, the same way as :func:`_platform_system_from_wheel_filename`.

    ``sys_platform`` and ``platform_system`` are two different, both
    commonly-used PEP 508 marker spellings for the same OS distinction
    (``sys_platform == "darwin"`` vs. ``platform_system == "Darwin"``);
    deriving only one of them still leaves a marker written in the other
    spelling falling back to the host running abicheck (Codex review). Same
    scope caveats as that function (no ``platform_machine``, ``None`` for
    ``any``/unrecognized/non-wheel names).
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    tag = parts[-1].lower()
    if tag.startswith(("manylinux", "musllinux", "linux")):
        return "linux"
    if tag.startswith("macosx"):
        return "darwin"
    if tag.startswith("win"):
        return "win32"
    return None


def _os_name_from_wheel_filename(filename: str) -> str | None:
    """Derive ``os_name`` (``"posix"``/``"nt"``, i.e. Python's own
    ``os.name`` spelling) from a wheel filename's platform tag, the same
    way as :func:`_platform_system_from_wheel_filename`.

    ``os_name`` is a third, less common PEP 508 marker spelling for the
    same OS distinction as ``platform_system``/``sys_platform``
    (``os_name == "nt"`` vs. ``platform_system == "Windows"``); deriving
    only the other two spellings still leaves a marker written in this one
    falling back to the host running abicheck (Codex review). Same scope
    caveats as that function (no ``platform_machine``, ``None`` for
    ``any``/unrecognized/non-wheel names). Linux and macOS both map to
    ``"posix"`` since that's the shared ``os.name`` value on any POSIX
    platform, not a Linux-specific one.
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    tag = parts[-1].lower()
    if tag.startswith(("manylinux", "musllinux", "linux", "macosx")):
        return "posix"
    if tag.startswith("win"):
        return "nt"
    return None


#: Single-architecture suffixes a Linux wheel platform tag can end in. Order
#: doesn't matter for correctness (each is ``$``-anchored, so e.g. ``ppc64``
#: can't spuriously match a ``...ppc64le`` tag), but longer/more-specific
#: names are listed first for readability.
_WHEEL_LINUX_MACHINE_RE = re.compile(
    r"(x86_64|aarch64|i686|armv7l|ppc64le|ppc64|s390x)$"
)


def _platform_machine_from_wheel_filename(filename: str) -> str | None:
    """Derive ``platform_machine`` from a wheel filename's platform tag, for
    the *single-architecture* Linux and macOS tags where it's unambiguous.

    Unlike :func:`_platform_system_from_wheel_filename`/
    :func:`_sys_platform_from_wheel_filename`, most wheel platform tags
    *do* name exactly one architecture (``manylinux_2_17_aarch64`` vs.
    ``..._x86_64`` are genuinely different, non-interchangeable wheels), so
    this is worth deriving where it's safe (Codex review). Still returns
    ``None`` for anything that isn't safe to guess: a fat/universal macOS
    wheel (``macosx_11_0_universal2``/``_universal``/``_intel``, which
    supports more than one architecture at once), a PEP 600 compressed
    multi-tag platform segment (``.``-joined, e.g.
    ``macosx_10_9_x86_64.macosx_11_0_arm64``) whose components don't all
    agree on the same single architecture (checking any one arch's slice
    of such a wheel would otherwise silently pick up another arch's
    marker evaluation — Codex review), Windows tags (``win32``/
    ``win_amd64``/``win_arm64`` don't map onto a single well-standardized
    ``platform.machine()`` string the way Linux/macOS tags do), the
    pure-Python ``any`` tag, or an unrecognized platform tag prefix.
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    machines: set[str] = set()
    for component in parts[-1].lower().split("."):
        if component.startswith(("manylinux", "musllinux", "linux")):
            m = _WHEEL_LINUX_MACHINE_RE.search(component)
            if m is None:
                return None
            machines.add(m.group(1))
        elif component.startswith("macosx"):
            if component.endswith("_x86_64"):
                machines.add("x86_64")
            elif component.endswith("_arm64"):
                machines.add("arm64")
            else:
                return None  # universal2/universal/intel: more than one arch
        else:
            return None
    return machines.pop() if len(machines) == 1 else None


# G26: a wheel's *.dist-info/METADATA declares its runtime dependencies —
# the "declared" side of the NumPy C-API compatibility-envelope check (the
# binary-evidence "required" side comes from numpy_capi.py). Mirrors
# parse_manylinux_glibc_floor's role for G10: a pure function callers wire
# in programmatically (see diff_numpy_capi.check_numpy_metadata_contract).

#: A real METADATA file is ordinarily a few KB even with a long dependency
#: list; this bounds how much a single wheel's METADATA member is allowed
#: to decompress to, so a malicious wheel can't zip-bomb this scan (a small
#: compressed member declaring a tiny size that in fact decompresses to
#: gigabytes) (CodeRabbit review).
_MAX_METADATA_SIZE = 1_048_576


def parse_wheel_numpy_requirement(
    wheel_path: Path, environment: dict[str, str] | None = None
) -> str | None:
    """Extract the declared ``numpy`` version-specifier range from a wheel's
    ``*.dist-info/METADATA`` (``Requires-Dist: numpy...``).

    Returns the specifier text (e.g. ``">=1.23.5,<3"``, or ``""`` for a bare
    ``Requires-Dist: numpy`` with no version constraint) for the numpy
    requirement(s) active for *environment*, or ``None`` when the wheel is
    unreadable, carries no ``.dist-info/METADATA`` member, or declares no
    such numpy dependency at all — including when the only ``numpy`` entry
    is gated behind an optional extra (e.g. ``numpy; extra == "test"``, only
    installed via ``pip install pkg[test]``, not a real runtime requirement)
    or an ordinary marker that doesn't hold for *environment*.

    When *environment* is omitted, ``python_version``, ``python_full_version``,
    ``implementation_name``, ``platform_python_implementation``,
    ``platform_system``, ``sys_platform``, ``os_name``, and (for
    single-architecture Linux/macOS tags) ``platform_machine`` are derived
    from the wheel's *own* filename tags (e.g. ``cp39`` ->
    ``python_version="3.9"``/``python_full_version="3.9.0"``/
    ``implementation_name="cpython"``/
    ``platform_python_implementation="CPython"``, a ``macosx_11_0_arm64``
    platform tag -> ``platform_system="Darwin"``/``sys_platform="darwin"``/
    ``os_name="posix"``/``platform_machine="arm64"``) rather than defaulting
    to the interpreter running abicheck — evaluating a marker gated on any
    of these against the wrong interpreter/implementation/OS/architecture
    could hide a real under-declared floor on a wheel built for a different
    Python, implementation, platform, or CPU than the one running the scan
    (Codex review; both implementation-marker and all three OS-marker
    spellings are covered since real-world metadata uses any of them).
    Falls back to the interpreter's own environment for whichever of these the filename
    doesn't pin down (e.g. a bare directory-derived METADATA path, the
    pure-Python ``any`` platform tag, a fat/universal macOS wheel, or a
    Windows tag, whose ``platform_machine`` isn't derived at all).
    """
    try:
        with zipfile.ZipFile(wheel_path) as zf:
            metadata_info = next(
                (
                    info
                    for info in zf.infolist()
                    if info.filename.endswith(".dist-info/METADATA")
                ),
                None,
            )
            if metadata_info is None:
                return None
            # A METADATA file is ordinarily a few KB even with a long
            # dependency list; an attacker-controlled wheel could otherwise
            # declare a small compressed member that decompresses to
            # gigabytes (a zip bomb). Reject an oversized declared size
            # up front, then bound the actual read too rather than trusting
            # the declared size alone (CodeRabbit review).
            if metadata_info.file_size > _MAX_METADATA_SIZE:
                return None
            with zf.open(metadata_info) as f:
                raw = f.read(_MAX_METADATA_SIZE + 1)
            if len(raw) > _MAX_METADATA_SIZE:
                return None
            text = raw.decode("utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile):
        return None
    if environment is None:
        derivers = (
            ("python_version", _python_version_from_wheel_filename),
            ("python_full_version", _python_full_version_from_wheel_filename),
            ("implementation_name", _implementation_name_from_wheel_filename),
            (
                "platform_python_implementation",
                _platform_python_implementation_from_wheel_filename,
            ),
            ("platform_system", _platform_system_from_wheel_filename),
            ("sys_platform", _sys_platform_from_wheel_filename),
            ("os_name", _os_name_from_wheel_filename),
            ("platform_machine", _platform_machine_from_wheel_filename),
        )
        derived = {
            key: value
            for key, derive in derivers
            if (value := derive(wheel_path.name)) is not None
        }
        environment = derived or None
    return parse_numpy_requirement_from_metadata(text, environment)


def parse_numpy_requirement_from_metadata(
    metadata_text: str, environment: dict[str, str] | None = None
) -> str | None:
    """Extract the declared ``numpy`` specifier from raw METADATA text.

    Split out from :func:`parse_wheel_numpy_requirement` so callers who
    already have the METADATA content (e.g. from a directory-based compare,
    no wheel zip involved) don't need to fabricate one. See that function's
    docstring for the return-value contract.

    *environment* is a PEP 508 marker environment override (``python_version``,
    ``platform_system``, etc.) used to decide which ``Requires-Dist: numpy``
    line is actually active; keys omitted from it fall back to the real
    environment (the interpreter running abicheck) — the same merge behavior
    as :meth:`packaging.markers.Marker.evaluate`, which this delegates to
    directly, including its built-in default of evaluating with an empty
    ``extra`` (a plain, non-extras install). That default is why an
    optional-extra-only requirement (``numpy; extra == "test"``) correctly
    evaluates inactive without any special-casing here — but so does an
    ordinary requirement that merely *mentions* ``extra`` alongside other
    conditions (``numpy; extra != "docs"``, ``numpy; extra == "test" or
    python_version >= "3.12"``), which a blanket "marker text contains the
    word extra" skip would incorrectly discard even though it's actually a
    real base-install requirement (CodeRabbit / Codex review). A wheel can
    legitimately declare more than one base numpy requirement split by
    markers, and more than one can be simultaneously active for a given
    environment (e.g. ``numpy>=1.23; python_version >= "3.9"`` and the
    stricter ``numpy>=2; python_version >= "3.12"`` are both true on Python
    3.12) — an installer enforces the *intersection* of every active
    constraint, not just the first one found, so returning only the first
    active line could under-report the real floor (Codex review).
    """
    from email import message_from_string
    from email.policy import default as _email_default_policy

    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.specifiers import SpecifierSet

    # Core Metadata is an RFC 5322-style header block: a long Requires-Dist
    # value may be folded across physical lines with leading whitespace on
    # the continuation lines. A plain `line.startswith("Requires-Dist:")`
    # scan only sees the first physical line and mangles a folded specifier
    # or marker, so parse it as real headers instead. The default
    # (compat32) email policy preserves the raw fold (embedded newline +
    # leading whitespace) in the returned value; ``policy.default`` is the
    # one that actually joins a folded header into a single logical line
    # (Codex review).
    headers = message_from_string(metadata_text, policy=_email_default_policy)
    # Marker.evaluate() only auto-defaults "extra" to "" on packaging>=22;
    # this project's pinned floor is packaging>=21.0, whose evaluate() has
    # no such default and raises UndefinedEnvironmentName on a bare `extra
    # == "test"` marker instead of treating it as inactive. Seed it
    # ourselves so behavior is identical across the whole supported range;
    # a caller-supplied *environment* can still override it explicitly
    # (Codex review).
    eval_environment = {"extra": "", **(environment or {})}
    found = False
    combined = SpecifierSet()
    for raw in headers.get_all("Requires-Dist") or ():
        try:
            req = Requirement(str(raw))
        except InvalidRequirement:
            continue
        if req.name.lower() != "numpy":
            continue
        if req.marker is not None and not req.marker.evaluate(eval_environment):
            continue  # marker inactive for this environment (extras included)
        found = True
        combined &= req.specifier
    return str(combined) if found else None


class WheelExtractor:
    """Extract Python wheel (.whl) packages.

    Wheels are zip archives containing the package's files plus
    a .dist-info directory with metadata.
    """

    def detect(self, pkg_path: Path) -> bool:
        return pkg_path.name.lower().endswith(".whl")

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        _log.info("Extracting wheel: %s", pkg_path)
        _safe_zip_extract(pkg_path, target_dir)
        return ExtractResult(lib_dir=target_dir)


# ── Directory passthrough ────────────────────────────────────────────────────


class DirExtractor:
    """Passthrough extractor for directories (no extraction needed)."""

    def detect(self, pkg_path: Path) -> bool:
        return pkg_path.is_dir()

    def extract(self, pkg_path: Path, target_dir: Path) -> ExtractResult:
        return ExtractResult(lib_dir=pkg_path)


# ── Auto-detection ───────────────────────────────────────────────────────────

_EXTRACTORS: list[PackageExtractor] = [
    DirExtractor(),
    CondaExtractor(),
    WheelExtractor(),
    TarExtractor(),
    RpmExtractor(),
    DebExtractor(),
]


def detect_extractor(path: Path) -> PackageExtractor | None:
    """Auto-detect package format and return the appropriate extractor.

    Returns None if the path is not a recognized package format.
    """
    for ext in _EXTRACTORS:
        if ext.detect(path):
            return ext
    return None


def is_package(path: Path) -> bool:
    """Return True if path is a recognized package format (not a plain directory)."""
    if path.is_dir():
        return False
    name = path.name.lower()
    if name.endswith((
        ".rpm", ".deb", ".tar", ".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tgz",
        ".conda", ".whl",
    )):
        return True
    # Check magic bytes for RPM / Deb
    try:
        with open(path, "rb") as f:
            magic = f.read(8)
        if magic[:4] == _RPM_MAGIC:
            return True
        if magic[:8] == _DEB_MAGIC:
            return True
    except OSError:
        pass
    return False


# ── Binary discovery ─────────────────────────────────────────────────────────

# ELF magic bytes
_ELF_MAGIC = b"\x7fELF"
# ELF type ET_DYN (shared object)
_ET_DYN = 3
# Program header type PT_INTERP (interpreter segment — present in executables, absent in DSOs)
_PT_INTERP = 3
_SO_NAME_RE = re.compile(r"^(?P<stem>.+)\.so(?P<version>(?:\.[A-Za-z0-9]+)*)$", re.IGNORECASE)
_NUMERIC_SO_VERSION_RE = re.compile(r"(?:\.\d+)+$")
_ALNUM_SO_VERSION_RE = re.compile(r"(?:\.[A-Za-z0-9]+)+$")


def _has_shared_object_name(path: Path | str) -> bool:
    """Return True for .so or versioned SONAME-style filenames."""
    match = _SO_NAME_RE.match(Path(path).name)
    if match is None:
        return False
    version = match.group("version")
    if not version:
        return True
    if _NUMERIC_SO_VERSION_RE.fullmatch(version):
        return True
    return match.group("stem").lower().startswith("lib") and (
        _ALNUM_SO_VERSION_RE.fullmatch(version) is not None
    )


def _has_interp_segment(f: IO[bytes], ei_class: int, byte_order: str) -> bool | None:
    """Check if an ELF file has a PT_INTERP program header (i.e. is an executable)."""
    try:
        if ei_class == 1:  # 32-bit
            # e_phoff at offset 28 (4 bytes), e_phentsize at 42 (2 bytes), e_phnum at 44 (2 bytes)
            f.seek(28)
            e_phoff = struct.unpack(f"{byte_order}I", f.read(4))[0]
            f.seek(42)
            e_phentsize = struct.unpack(f"{byte_order}H", f.read(2))[0]
            e_phnum = struct.unpack(f"{byte_order}H", f.read(2))[0]
        else:  # 64-bit
            # e_phoff at offset 32 (8 bytes), e_phentsize at 54 (2 bytes), e_phnum at 56 (2 bytes)
            f.seek(32)
            e_phoff = struct.unpack(f"{byte_order}Q", f.read(8))[0]
            f.seek(54)
            e_phentsize = struct.unpack(f"{byte_order}H", f.read(2))[0]
            e_phnum = struct.unpack(f"{byte_order}H", f.read(2))[0]

        if e_phoff == 0 or e_phnum == 0:
            return False
        expected_phentsize = 32 if ei_class == 1 else 56
        # The program-header entry size is fixed by ELF class (32 bytes for
        # ELFCLASS32, 56 for ELFCLASS64). Any mismatch — undersized *or*
        # oversized — means the layout we read p_type from at fixed offsets is
        # not trustworthy, so treat it as inconclusive rather than risk
        # misclassifying the file.
        if e_phentsize != expected_phentsize:
            return None

        for i in range(e_phnum):
            f.seek(e_phoff + i * e_phentsize)
            p_type = struct.unpack(f"{byte_order}I", f.read(4))[0]
            if p_type == _PT_INTERP:
                return True
        return False
    except (OSError, struct.error):
        return None


def _is_elf_shared_object(path: Path) -> bool:
    """Check if a file is an ELF shared object (ET_DYN) and not a PIE executable."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic != _ELF_MAGIC:
                return False
            # Read EI_CLASS (byte 4), then EI_DATA (byte 5) for endianness
            ei_class = struct.unpack("B", f.read(1))[0]
            ei_data = struct.unpack("B", f.read(1))[0]

            # Seek to e_type at offset 16
            f.seek(16)
            byte_order = "<" if ei_data == 1 else ">"
            e_type = struct.unpack(f"{byte_order}H", f.read(2))[0]
            if e_type != _ET_DYN:
                return False

            # Distinguish PIE executables from true shared objects:
            # executables have a PT_INTERP segment, shared objects don't.
            has_interp = _has_interp_segment(f, ei_class, byte_order)
            if has_interp is None:
                return False
            if has_interp:
                # A few distro runtime DSOs are ET_DYN, named like libraries,
                # and intentionally carry PT_INTERP so they can be invoked
                # directly (for example Ubuntu's libcap.so.2.66).  Keep the
                # PIE-executable guard for app-like filenames, but do not drop
                # real versioned .so files from package discovery.
                return _has_shared_object_name(path)
            return True
    except (OSError, struct.error):
        return False


def discover_shared_libraries(
    extract_dir: Path,
    *,
    include_private: bool = False,
) -> list[Path]:
    """Find all shared libraries in an extracted package directory.

    Walks the directory tree, identifies ELF shared objects (ET_DYN),
    and returns their paths sorted by name.

    Args:
        extract_dir: Root directory to search.
        include_private: If True, include DSOs from non-standard paths
            (e.g. private plugin directories).
    """
    _PUBLIC_LIB_DIRS = {"lib", "lib64", "usr/lib", "usr/lib64", "usr/local/lib", "usr/local/lib64"}

    libraries: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(extract_dir, followlinks=False):
        for fn in filenames:
            fp = Path(dirpath) / fn
            elf_path = fp
            if fp.is_symlink():
                # Follow symlinks only to check the target, don't add symlinks themselves
                # unless the target is a real shared object
                try:
                    real = fp.resolve()
                    if not real.exists():
                        continue
                    elf_path = real
                except OSError:
                    continue

            if not _is_elf_shared_object(elf_path):
                continue

            # Filter by path convention unless --include-private-dso
            if not include_private:
                try:
                    rel = fp.relative_to(extract_dir)
                except ValueError:
                    continue
                rel_parts = "/".join(rel.parts[:-1])
                # Check if it's in a known library directory
                in_public = any(
                    rel_parts == d or rel_parts.startswith(d + "/")
                    for d in _PUBLIC_LIB_DIRS
                )
                # Also accept files with a .so suffix/versioned SONAME at any
                # depth as a fallback for flat directory layouts.
                has_so_ext = _has_shared_object_name(fn)
                if not in_public and not has_so_ext:
                    continue

            libraries.append(fp)

    return sorted(libraries, key=lambda p: p.name)


# ── Debug info resolution ────────────────────────────────────────────────────


def resolve_debug_info(
    binary_path: Path,
    debug_dir: Path,
) -> Path | None:
    """Resolve debug info file for a binary from an extracted debug package.

    Tries three strategies in order:

    1. **Build-id** — read ``NT_GNU_BUILD_ID`` from the binary and look up the
       canonical ``.build-id/ab/cdef1234.debug`` path.  This is the most
       reliable method and produces an unambiguous match.
    2. **Path mirror** — look for a ``.debug`` file whose path under the debug
       directory mirrors the binary's path (e.g. the binary at
       ``/usr/lib64/libfoo.so.1`` → ``<debug_dir>/usr/lib/debug/usr/lib64/libfoo.so.1.debug``).
    3. **Basename rglob with disambiguation** — search for ``<name>.debug``
       anywhere under the debug directory.  When multiple candidates exist,
       prefer one whose build-id matches the binary, then one whose path
       components overlap most with the binary's path.
    """
    name = binary_path.name

    # Strategy 1: build-id (most reliable, unambiguous)
    build_id = _read_build_id(binary_path)
    if build_id:
        # build-id layout: .build-id/ab/cdef1234.debug
        bid_dir = build_id[:2]
        bid_file = build_id[2:] + ".debug"
        for search_root in [debug_dir, debug_dir / "usr" / "lib" / "debug"]:
            candidate = search_root / ".build-id" / bid_dir / bid_file
            if candidate.exists():
                _log.debug("Debug info resolved via build-id: %s", candidate)
                return candidate

    # Strategy 2: path mirror — binary at usr/lib64/libfoo.so.1 has debug at
    # <debug_dir>/usr/lib/debug/usr/lib64/libfoo.so.1.debug
    binary_parts = binary_path.parts
    for search_root in [debug_dir, debug_dir / "usr" / "lib" / "debug"]:
        # Try to mirror the binary's absolute path under the search root
        # e.g. binary /tmp/extract/usr/lib64/libfoo.so → search for
        #      search_root/usr/lib64/libfoo.so.debug
        for i, part in enumerate(binary_parts):
            if part in ("usr", "lib", "lib64"):
                mirrored = search_root.joinpath(*binary_parts[i:])
                debug_candidate = mirrored.parent / f"{mirrored.name}.debug"
                if debug_candidate.exists():
                    _log.debug("Debug info resolved via path mirror: %s", debug_candidate)
                    return debug_candidate

    # Strategy 3: basename rglob with disambiguation
    # Collect all candidates and pick the best one
    candidates: list[Path] = []
    for search_root in [debug_dir, debug_dir / "usr" / "lib" / "debug"]:
        candidates.extend(search_root.rglob(f"{name}.debug"))

    if not candidates:
        return None

    if len(candidates) == 1:
        _log.debug("Debug info resolved via path convention: %s", candidates[0])
        return candidates[0]

    # Multiple candidates — disambiguate
    # Prefer a candidate whose build-id matches the binary
    if build_id:
        for candidate in candidates:
            cand_bid = _read_build_id(candidate)
            if cand_bid == build_id:
                _log.debug(
                    "Debug info resolved via build-id match among %d candidates: %s",
                    len(candidates), candidate,
                )
                return candidate

    # Fall back to path similarity: prefer the candidate whose path
    # components overlap most with the binary's path
    binary_part_set = set(binary_path.parts)
    best: Path | None = None
    best_overlap = -1
    for candidate in candidates:
        overlap = len(set(candidate.parts) & binary_part_set)
        if overlap > best_overlap:
            best_overlap = overlap
            best = candidate

    _log.debug(
        "Debug info resolved via path similarity among %d candidates: %s",
        len(candidates), best,
    )
    return best


def _read_build_id(binary_path: Path) -> str | None:
    """Read GNU build-id from an ELF binary.

    Returns the build-id as a hex string, or None if not found.
    """
    try:
        from elftools.elf.elffile import ELFFile
        with open(binary_path, "rb") as f:
            elf = ELFFile(f)
            for section in elf.iter_sections():
                if section.name == ".note.gnu.build-id":
                    for note in section.iter_notes():
                        if note["n_type"] == "NT_GNU_BUILD_ID":
                            return str(note["n_desc"])
    except Exception:
        _log.debug("Failed to read build-id from %s", binary_path, exc_info=True)
    return None

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

"""Baseline Registry — store and retrieve ABI baseline snapshots (ADR-022).

Provides a pluggable registry for ABI baseline management with multiple
backends:

- **Filesystem** (default): Plain directory structure on local/network FS
- **Git-native** (future): Dedicated branch in the repository
- **OCI** (future): OCI artifacts via ORAS conventions

Usage::

    from abicheck.baseline import BaselineKey, BaselineMetadata, FilesystemRegistry

    registry = FilesystemRegistry(Path("/shared/abi-baselines"))
    key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")

    # Push a baseline
    registry.push(key, snapshot, metadata)

    # Pull a baseline
    result = registry.pull(key)
    if result:
        snapshot, metadata = result

    # List baselines
    for key in registry.list():
        print(key.path)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from . import __version__ as _abicheck_version
from .errors import ValidationError
from .model import AbiSnapshot
from .serialization import snapshot_to_json

if TYPE_CHECKING:
    from .evidence.pack import EvidencePack

_logger = logging.getLogger(__name__)

#: Sub-directory of a baseline key directory that holds the optional evidence
#: pack stored alongside the snapshot (ADR-028 D1/Phase 5, ADR-033 baseline
#: storage). Kept separate from ``snapshot.json``/``metadata.json`` so an old
#: registry reader that knows nothing about evidence packs simply ignores it.
_EVIDENCE_SUBDIR = "evidence"

# Current baseline metadata schema version
_METADATA_SCHEMA_VERSION = 1

# Safe name pattern: alphanumeric, dots, hyphens, underscores, plus signs
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9._\-+]+$")


def _validate_key_field(field_name: str, value: str) -> None:
    """Validate a BaselineKey field against path traversal and injection."""
    if not value:
        raise ValidationError(f"BaselineKey {field_name} must not be empty")
    if ".." in value:
        raise ValidationError(
            f"BaselineKey {field_name} must not contain '..': {value!r}"
        )
    if not _SAFE_NAME_RE.match(value):
        raise ValidationError(
            f"BaselineKey {field_name} contains invalid characters: {value!r} "
            f"(allowed: alphanumeric, dots, hyphens, underscores, plus signs)"
        )


@dataclass(frozen=True)
class BaselineKey:
    """Unique identifier for a baseline snapshot (ADR-022)."""

    library: str
    version: str
    platform: str
    variant: str = ""

    def __post_init__(self) -> None:
        _validate_key_field("library", self.library)
        _validate_key_field("version", self.version)
        _validate_key_field("platform", self.platform)
        if self.variant:
            _validate_key_field("variant", self.variant)

    @property
    def path(self) -> str:
        """Registry path: library/version/platform[/variant]."""
        parts = [self.library, self.version, self.platform]
        if self.variant:
            parts.append(self.variant)
        return "/".join(parts)

    @classmethod
    def from_path(cls, path: str) -> BaselineKey:
        """Parse a registry path into a BaselineKey.

        Accepts ``library/version/platform`` or
        ``library/version/platform/variant``.
        """
        parts = path.strip("/").split("/")
        if len(parts) < 3:
            raise ValidationError(
                f"Invalid baseline path {path!r}: expected "
                "library/version/platform[/variant]"
            )
        if len(parts) > 4:
            raise ValidationError(
                f"Too many segments in baseline path {path!r}: expected "
                "library/version/platform[/variant]"
            )
        return cls(
            library=parts[0],
            version=parts[1],
            platform=parts[2],
            variant=parts[3] if len(parts) > 3 else "",
        )

    @classmethod
    def from_spec(cls, spec: str) -> BaselineKey:
        """Parse a colon-separated spec into a BaselineKey.

        Accepts ``library:version:platform`` or
        ``library:version:platform:variant``.
        """
        parts = spec.split(":")
        if len(parts) < 3:
            raise ValidationError(
                f"Invalid baseline spec {spec!r}: expected "
                "library:version:platform[:variant]"
            )
        if len(parts) > 4:
            raise ValidationError(
                f"Too many segments in baseline spec {spec!r}: expected "
                "library:version:platform[:variant]"
            )
        return cls(
            library=parts[0],
            version=parts[1],
            platform=parts[2],
            variant=parts[3] if len(parts) > 3 else "",
        )

    def __str__(self) -> str:
        return self.path


class BaselineIntegrityError(ValidationError):
    """Raised when a baseline snapshot fails integrity verification."""


@dataclass
class BaselineMetadata:
    """Provenance and integrity metadata for a baseline (ADR-022)."""

    abicheck_version: str = ""
    schema_version: int = _METADATA_SCHEMA_VERSION
    created_at: str = ""
    build_context_hash: str | None = None
    git_commit: str | None = None
    checksum: str | None = None
    signature: str | None = None
    #: ``sha256:<hex>`` content hash of the optional evidence pack stored with
    #: this baseline (``EvidencePack.content_hash()``), or ``None`` when no pack
    #: was pushed. Lets ``pull_evidence`` verify the stored pack has not drifted
    #: from what was recorded, the same integrity discipline ``checksum`` gives
    #: the snapshot (ADR-028 Phase 5).
    evidence_content_hash: str | None = None

    @classmethod
    def create(
        cls,
        snapshot_json: str,
        *,
        build_context_hash: str | None = None,
        git_commit: str | None = None,
        evidence_content_hash: str | None = None,
    ) -> BaselineMetadata:
        """Create metadata for a new baseline with computed checksum."""
        return cls(
            abicheck_version=_abicheck_version,
            schema_version=_METADATA_SCHEMA_VERSION,
            created_at=datetime.now(timezone.utc).isoformat(),
            build_context_hash=build_context_hash,
            git_commit=git_commit,
            checksum=hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
            evidence_content_hash=evidence_content_hash,
        )

    def verify_checksum(self, snapshot_json: str) -> bool:
        """Verify that the snapshot matches the stored checksum.

        Returns True if the checksum matches or if no checksum was set
        (legacy metadata without checksum field). Returns False on mismatch.
        """
        if self.checksum is None:
            # Legacy metadata without checksum — cannot verify
            return True
        if self.checksum == "":
            # Empty string is invalid — treat as mismatch (not legacy)
            return False
        return hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest() == self.checksum

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> BaselineMetadata:
        checksum_raw = data.get("checksum")
        if checksum_raw is None:
            checksum_val: str | None = None
        else:
            checksum_val = str(checksum_raw)

        return cls(
            abicheck_version=str(data.get("abicheck_version") or ""),
            schema_version=int(str(data.get("schema_version", _METADATA_SCHEMA_VERSION))),
            created_at=str(data.get("created_at") or ""),
            build_context_hash=str(data["build_context_hash"]) if data.get("build_context_hash") is not None else None,
            git_commit=str(data["git_commit"]) if data.get("git_commit") is not None else None,
            checksum=checksum_val,
            signature=str(data["signature"]) if data.get("signature") is not None else None,
            evidence_content_hash=(
                str(data["evidence_content_hash"])
                if data.get("evidence_content_hash") is not None
                else None
            ),
        )


class BaselineRegistry(Protocol):
    """Protocol for baseline storage backends (ADR-022)."""

    def push(
        self,
        key: BaselineKey,
        snapshot: AbiSnapshot,
        metadata: BaselineMetadata | None = None,
        evidence: EvidencePack | None = None,
    ) -> str:
        """Store a baseline snapshot (and an optional evidence pack). Returns a reference ID."""
        ...

    def pull(self, key: BaselineKey) -> tuple[AbiSnapshot, BaselineMetadata] | None:
        """Retrieve a baseline by key. Returns None if not found."""
        ...

    def pull_evidence(self, key: BaselineKey) -> EvidencePack | None:
        """Retrieve the evidence pack stored with a baseline, or None if absent."""
        ...

    def list(self, prefix: str | None = None) -> list[BaselineKey]:
        """List available baselines, optionally filtered by prefix."""
        ...

    def delete(self, key: BaselineKey) -> bool:
        """Delete a baseline. Returns True if deleted."""
        ...


class FilesystemRegistry:
    """Filesystem-based baseline registry (ADR-022).

    Stores baselines as plain files in a directory tree::

        <root>/
        ├── libfoo/
        │   ├── 1.0.0/
        │   │   └── linux-x86_64/
        │   │       ├── snapshot.json
        │   │       └── metadata.json
        │   └── main/
        │       └── linux-x86_64/
        │           ├── snapshot.json
        │           └── metadata.json
        └── libbar/
            └── 2.0.0/
                └── linux-x86_64/
                    ├── snapshot.json
                    └── metadata.json
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _key_dir(self, key: BaselineKey) -> Path:
        parts = [key.library, key.version, key.platform]
        if key.variant:
            parts.append(key.variant)
        result = self._root / Path(*parts)
        # Defense-in-depth: verify resolved path is under root
        try:
            result.resolve().relative_to(self._root.resolve())
        except ValueError:
            raise ValidationError(f"Key path escapes registry root: {key.path}")
        return result

    def push(
        self,
        key: BaselineKey,
        snapshot: AbiSnapshot,
        metadata: BaselineMetadata | None = None,
        evidence: EvidencePack | None = None,
    ) -> str:
        """Store a baseline snapshot to the filesystem (atomic writes).

        When ``evidence`` is given, its on-disk pack directory is copied into
        ``<key_dir>/evidence/`` and its content hash is recorded in the metadata
        so ``pull_evidence`` can verify integrity (ADR-028 Phase 5).
        """
        key_dir = self._key_dir(key)
        key_dir.mkdir(parents=True, exist_ok=True)

        snap_json = snapshot_to_json(snapshot)

        if metadata is None:
            metadata = BaselineMetadata.create(snap_json)

        # Copy the evidence pack (if any) before writing metadata so the recorded
        # content hash reflects exactly what is stored on disk.
        evidence_dir = key_dir / _EVIDENCE_SUBDIR
        if evidence is not None:
            metadata.evidence_content_hash = self._store_evidence(evidence, evidence_dir)
        elif evidence_dir.exists():
            # Re-pushing without evidence must not leave a stale pack behind.
            shutil.rmtree(evidence_dir)
            metadata.evidence_content_hash = None

        snap_path = key_dir / "snapshot.json"
        meta_path = key_dir / "metadata.json"

        # Atomic write: temp file then rename
        _atomic_write(snap_path, snap_json)
        _atomic_write(meta_path, json.dumps(metadata.to_dict(), indent=2))

        ref = f"fs://{key.path}"
        _logger.info("Baseline pushed: %s → %s", ref, key_dir)
        return ref

    @staticmethod
    def _store_evidence(evidence: EvidencePack, dest: Path) -> str:
        """Copy an evidence pack into ``dest`` and return its content hash.

        The pack must already be materialized on disk (a ``manifest.json`` under
        ``evidence.root``); ``collect-evidence`` and ``EvidencePack.write()``
        guarantee that. Copying the whole tree preserves both ``normalized/``
        facts and ``raw/`` provenance (ADR-028 D4).
        """
        manifest = evidence.root / "manifest.json"
        if not manifest.is_file():
            raise ValidationError(
                f"Evidence pack at {evidence.root} has no manifest.json; "
                "run `abicheck collect-evidence` (or EvidencePack.write()) first."
            )
        content_hash = evidence.content_hash()
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(evidence.root, dest)
        return content_hash

    def pull(self, key: BaselineKey) -> tuple[AbiSnapshot, BaselineMetadata] | None:
        """Retrieve a baseline snapshot from the filesystem.

        Raises BaselineIntegrityError if the checksum does not match.
        """
        key_dir = self._key_dir(key)
        snap_path = key_dir / "snapshot.json"
        meta_path = key_dir / "metadata.json"

        if not snap_path.exists():
            _logger.debug("Baseline not found: %s", key.path)
            return None

        # Load metadata
        meta = BaselineMetadata()
        if meta_path.exists():
            try:
                meta_raw = json.loads(meta_path.read_text(encoding="utf-8"))
                meta = BaselineMetadata.from_dict(meta_raw)
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                _logger.warning("Invalid metadata for %s: %s", key.path, exc)

        # Read snapshot once, verify checksum, then deserialize
        snap_json = snap_path.read_text(encoding="utf-8")
        if not meta.verify_checksum(snap_json):
            raise BaselineIntegrityError(
                f"Checksum mismatch for baseline {key.path} — "
                "the snapshot may have been modified since it was pushed. "
                "Re-push the baseline to update the checksum."
            )

        snapshot = _load_snapshot_from_string(snap_json)
        _logger.info("Baseline pulled: %s", key.path)
        return snapshot, meta

    def pull_evidence(self, key: BaselineKey) -> EvidencePack | None:
        """Load the evidence pack stored with a baseline (ADR-028 Phase 5).

        Returns ``None`` when the baseline has no pack. Raises
        ``BaselineIntegrityError`` when the stored pack's content hash does not
        match the hash recorded at push time — mirroring the snapshot checksum
        guard so a tampered/partial pack is not silently trusted.
        """
        from .evidence.pack import EvidencePack

        key_dir = self._key_dir(key)
        evidence_dir = key_dir / _EVIDENCE_SUBDIR
        if not (evidence_dir / "manifest.json").is_file():
            return None

        pack = EvidencePack.load(evidence_dir)

        # Two layers of integrity:
        #   1. the on-disk normalized payloads must still match the digests the
        #      manifest recorded (catches an edited normalized file, which
        #      content_hash alone would miss because it trusts those digests);
        #   2. the pack's content hash must match the value recorded in the
        #      baseline metadata at push time (catches a swapped pack/manifest).
        if not pack.verify_integrity():
            raise BaselineIntegrityError(
                f"Evidence-pack content hash mismatch for baseline {key.path} — "
                "a stored normalized payload no longer matches the pack manifest. "
                "Re-push the baseline to update the recorded hashes."
            )

        # Verify against the recorded hash when present (legacy metadata without
        # the field cannot be verified, so it is trusted — same rule as checksum).
        meta_path = key_dir / "metadata.json"
        if meta_path.is_file():
            try:
                meta = BaselineMetadata.from_dict(
                    json.loads(meta_path.read_text(encoding="utf-8"))
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                meta = BaselineMetadata()
            recorded = meta.evidence_content_hash
            if recorded is not None and recorded != pack.content_hash():
                raise BaselineIntegrityError(
                    f"Evidence-pack content hash mismatch for baseline {key.path} — "
                    "the stored pack may have been modified since it was pushed. "
                    "Re-push the baseline to update the recorded hash."
                )
        _logger.info("Baseline evidence pulled: %s", key.path)
        return pack

    def list(self, prefix: str | None = None) -> list[BaselineKey]:
        """List available baselines in the filesystem registry."""
        if not self._root.exists():
            return []

        keys: list[BaselineKey] = []
        for lib_dir in sorted(self._root.iterdir()):
            if not lib_dir.is_dir():
                continue
            library = lib_dir.name
            if prefix and not library.startswith(prefix):
                continue
            for ver_dir in sorted(lib_dir.iterdir()):
                if not ver_dir.is_dir():
                    continue
                version = ver_dir.name
                for plat_dir in sorted(ver_dir.iterdir()):
                    if not plat_dir.is_dir():
                        continue
                    platform = plat_dir.name
                    snap = plat_dir / "snapshot.json"
                    if snap.exists():
                        keys.append(BaselineKey(
                            library=library,
                            version=version,
                            platform=platform,
                        ))
                    # Also check for variant subdirectories
                    for var_dir in sorted(plat_dir.iterdir()):
                        if var_dir.is_dir() and (var_dir / "snapshot.json").exists():
                            keys.append(BaselineKey(
                                library=library,
                                version=version,
                                platform=platform,
                                variant=var_dir.name,
                            ))
        return keys

    def delete(self, key: BaselineKey) -> bool:
        """Delete a baseline from the filesystem registry."""
        key_dir = self._key_dir(key)
        snap_path = key_dir / "snapshot.json"
        if not snap_path.exists():
            return False

        import shutil
        shutil.rmtree(key_dir)

        # Walk up and remove empty parent directories up to (not including) root
        parent = key_dir.parent
        root_resolved = self._root.resolve()
        while parent.resolve() != root_resolved:
            try:
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
                else:
                    break
            except OSError:
                break
            parent = parent.parent

        _logger.info("Baseline deleted: %s", key.path)
        return True


def _atomic_write(path: Path, content: str) -> None:
    """Write content to a file atomically (write to temp, then rename).

    Uses os.replace() for unconditional atomic overwrite on all platforms
    (Path.rename raises FileExistsError on Windows if the target exists).
    """
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_snapshot_from_string(snap_json: str) -> AbiSnapshot:
    """Deserialize a snapshot from a JSON string (avoids re-reading from disk)."""
    from .serialization import snapshot_from_dict

    data = json.loads(snap_json)
    return snapshot_from_dict(data)


# We need os for _atomic_write
import os  # noqa: E402


def detect_platform_from_binary(binary_path: Path) -> str | None:
    """Detect platform string from a binary file.

    Returns a string like "linux-x86_64", "windows-x86_64", "macos-arm64".
    Returns ``None`` when architecture detection fails due to parse/import errors.
    """
    import sys

    from .binary_utils import detect_binary_format

    fmt = detect_binary_format(binary_path)
    if fmt is None:
        return f"{sys.platform}-unknown"

    arch = "unknown"
    if fmt == "elf":
        try:
            from elftools.elf.elffile import ELFFile
            with open(binary_path, "rb") as f:
                elf = ELFFile(f)  # type: ignore[no-untyped-call]
                machine = elf.header.e_machine
                arch_map = {
                    "EM_X86_64": "x86_64",
                    "EM_386": "x86",
                    "EM_AARCH64": "aarch64",
                    "EM_ARM": "arm",
                    "EM_RISCV": "riscv64",
                    "EM_PPC64": "ppc64",
                    "EM_S390": "s390x",
                }
                arch = arch_map.get(machine, str(machine))
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Failed to detect ELF architecture for %s: %s",
                binary_path,
                exc,
            )
            return None
        return f"linux-{arch}"

    if fmt == "pe":
        try:
            import pefile
            pe = pefile.PE(str(binary_path), fast_load=True)
            machine = pe.FILE_HEADER.Machine
            if machine == 0x8664:
                arch = "x86_64"
            elif machine == 0x14C:
                arch = "x86"
            elif machine == 0xAA64:
                arch = "aarch64"
            pe.close()
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Failed to detect PE architecture for %s: %s",
                binary_path,
                exc,
            )
            return None
        return f"windows-{arch}"

    if fmt == "macho":
        try:
            from macholib.MachO import MachO
            m = MachO(str(binary_path))
            for header in m.headers:
                cpu = header.header.cputype
                cpu_map = {1: "x86", 7: "x86", 12: "arm", 16777228: "aarch64",
                           16777223: "x86_64"}
                arch = cpu_map.get(cpu, str(cpu))
                break
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Failed to detect Mach-O architecture for %s: %s",
                binary_path,
                exc,
            )
            return None
        return f"macos-{arch}"

    return f"unknown-{arch}"

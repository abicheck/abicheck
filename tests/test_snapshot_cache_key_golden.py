"""Golden-key characterization for the whole-snapshot cache key.

``dump_cache_key._cache_key`` decides which warm entry a dump reads. Moving or
refactoring its derivation must not silently re-key every existing cache
entry, and must not change what identifies an input. These goldens pin the
exact digests of a few fixed input shapes, so any change to the derivation
fails here first, with the new digest in the message.

The inputs are made reproducible on purpose:

* paths are relative and the test runs under ``chdir(tmp_path)``, so the
  path strings hashed into the key do not depend on where pytest runs;
* every hashed file gets a fixed mtime, so the key does not depend on when
  the fixtures were written;
* the code fingerprint and the snapshot schema version are pinned, so the
  golden tracks the derivation, not unrelated code or schema bumps.

When a change is deliberate (for example a ``_SNAPSHOT_CACHE_VERSION`` bump,
which must change every key), update the golden in the same commit and say
why in the commit message. An unexplained golden change is a cache-key
regression: it would either orphan warm entries or, worse, let a changed
input keep hitting an old entry.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from abicheck.workflows.dump.cache import key as dump_cache_key

#: Fixed mtime (nanoseconds) applied to every hashed fixture file.
_FIXED_MTIME_NS = 1_700_000_000_000_000_000


@pytest.fixture
def pinned_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write the fixture tree under *tmp_path* and pin the non-input key parts."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        dump_cache_key, "abicheck_code_fingerprint", lambda: "code-pinned"
    )
    monkeypatch.setattr(dump_cache_key, "_snapshot_schema_version", lambda: 1)

    files = {
        Path("lib.so"): b"\x7fELF-golden-binary",
        Path("inc/api.h"): b"int api(void);\n",
        Path("inc/detail/impl.hpp"): b"// detail\n",
    }
    for rel, data in files.items():
        rel.parent.mkdir(parents=True, exist_ok=True)
        rel.write_bytes(data)
        os.utime(rel, ns=(_FIXED_MTIME_NS, _FIXED_MTIME_NS))
    return tmp_path


#: Binary content only: no headers, no includes, no version, default lang.
#: Re-pinned for ``_SNAPSHOT_CACHE_VERSION`` 35 -> 36 (eae134a0e), a deliberate
#: bump that changes every key; that commit did not update these goldens.
GOLDEN_BINARY_ONLY = "60ce8774a9be4ebf1dbd20ed3dc3abfe09dfaf07bd791681b422c1d127968c80"

#: Every named input at once, so a change to any one of them shows here.
GOLDEN_FULL = "e9905659f5f1dd336ceeca82f6ad75ab528b348d218b294edd2e97c831808197"


def test_binary_only_key_is_pinned(pinned_inputs: Path) -> None:
    key = dump_cache_key._cache_key(Path("lib.so"), [], [], "", "c++")
    assert key == GOLDEN_BINARY_ONLY, f"binary-only key changed: {key}"


@pytest.mark.skipif(
    os.sep != "/",
    reason="the key hashes header/include path strings as str(Path), so "
    "'inc/api.h' hashes as 'inc\\\\api.h' on Windows; the golden is POSIX-only",
)
def test_full_input_key_is_pinned(pinned_inputs: Path) -> None:
    key = dump_cache_key._cache_key(
        Path("lib.so"),
        [Path("inc/api.h")],
        [Path("inc")],
        "1.2",
        "c",
        extra="fmt=elf\x00backend=castxml",
    )
    assert key == GOLDEN_FULL, f"full-input key changed: {key}"

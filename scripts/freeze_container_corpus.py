#!/usr/bin/env python3
"""Freeze one sample of every storage-v2 container (``tests/fixtures/containers/``).

``tests/test_container_corpus.py`` holds each frozen sample to a freshly
produced one: it loads with no unexpected notice, compares clean, and any
byte or respelling drift between them is a declared migration. The
``container-corpus`` check (``scripts/container_corpus_gate.py``) fails a
container whose encoder version moved while the corpus still holds only
samples written under the old one.

Each sample's entries are numbered ``<sample>/<n>``, oldest first, under
``<dir>/<n>/``:

* the encoder versions this build declares for the sample still equal the
  newest entry's: that entry is rewritten in place (same versions, so the
  same bytes are expected; run it after a toolchain change).
* they differ (an encoder version was bumped): a new entry ``n + 1`` is
  added and every older one is kept. The older ones are what proves the
  bumped reader still decodes, and migrates, what was stored before.

Produced through the public CLI by ``tests/_container_corpus.py`` (the same
steps the test re-runs for the fresh side). The ELF samples need g++, gcc
and castxml, the clang one g++ and clang; the Mach-O and PE samples need
nothing. Run from the repository root::

    python scripts/freeze_container_corpus.py            # every sample
    python scripts/freeze_container_corpus.py --sample package
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests._container_corpus import (  # noqa: E402
    CORPUS_ROOT,
    MANIFEST_PATH,
    SAMPLES,
    Sample,
    encoder_versions,
    entries_of,
    file_digest,
    missing_tools,
    produce,
    sources_sha256,
    toolchain,
)


def frozen_relpath(sample: Sample, number: int) -> str:
    """``g38/release.json.gz`` -> ``g38/<n>/release.json.gz``; the package
    directory ``package`` -> ``package/<n>``."""
    path = Path(sample.path)
    if path.suffix:
        return (path.parent / str(number) / path.name).as_posix()
    return (path / str(number)).as_posix()


def _commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    return proc.stdout.strip()


def _generations() -> dict[str, int]:
    from abicheck.storage.versioning import EXTRACTOR_GENERATION, RESOLVER_GENERATION

    return {"extractor": EXTRACTOR_GENERATION, "resolver": RESOLVER_GENERATION}


def freeze(sample: Sample, manifest: dict, work: Path) -> str:
    missing = missing_tools(sample)
    if missing:
        raise SystemExit(f"{sample.name}: needs {', '.join(missing)} on PATH")
    versions = encoder_versions(sample.name)
    existing = entries_of(manifest, sample.name)
    if existing and existing[-1][1]["versions"] == versions:
        key, _entry = existing[-1]
        number = int(key.rsplit("/", 1)[1])
    else:
        number = int(existing[-1][0].rsplit("/", 1)[1]) + 1 if existing else 1
        key = f"{sample.name}/{number}"
    fresh = produce(sample, work / sample.name)
    rel = frozen_relpath(sample, number)
    target = CORPUS_ROOT / rel
    if target.is_dir():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if fresh.is_dir():
        shutil.copytree(fresh, target)
    else:
        shutil.copyfile(fresh, target)
    manifest["entries"][key] = {
        "sample": sample.name,
        "path": rel,
        "sha256": file_digest(target),
        "versions": versions,
        "generations": _generations(),
        "toolchain": toolchain(sample),
        "commit": _commit(),
    }
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sample",
        action="append",
        choices=sorted(SAMPLES),
        help="sample to (re)freeze; repeatable (default: every sample)",
    )
    args = parser.parse_args(argv)
    manifest = (
        json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if MANIFEST_PATH.is_file()
        else {"entries": {}}
    )
    names = args.sample or sorted(SAMPLES)
    with tempfile.TemporaryDirectory(prefix="abicheck-container-corpus-") as tmp:
        for name in names:
            key = freeze(SAMPLES[name], manifest, Path(tmp))
            print(f"froze {key}: {manifest['entries'][key]['path']}")
    # The digest says every entry was produced from these sources, so only a
    # run that refroze every sample may restamp it: after a shared-source
    # edit, a --sample run leaves it stale and the test keeps failing.
    if set(names) == set(SAMPLES):
        manifest["sources_sha256"] = sources_sha256()
    elif manifest.get("sources_sha256") != sources_sha256():
        print(
            "sources changed: refreeze every sample (no --sample) to restamp "
            "sources_sha256",
            file=sys.stderr,
        )
    manifest["entries"] = dict(sorted(manifest["entries"].items()))
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

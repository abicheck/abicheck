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

"""A release pairs a member by the library it is when its file name cannot.

Known gap "Release fan-out cannot pair a stored snapshot directory with a
live one": pairing used only ``build_match_map``'s file-name key, so a
stored ``libfoo.json`` (a dump of ``libfoo.so.1``) and the live
``libfoo.so.1`` never paired and a stored/live directory compare compared
nothing (exit 1, ``no_comparison_completed``).

Two invariants, over every storage spelling of a stored member:

* **pairs**: a member the file name left unmatched on both sides pairs with
  the counterpart recording the same library, in either direction, for
  stored files and stored ``BundleFacts`` documents alike;
* **conservative**: a pair the file name made never moves, and a library
  claimed by two leftovers on one side, or already held by a pair, pairs
  nothing new.

The oracle is a hand-written table of library -> release key, not the
canonicalizer under test. The end-to-end directory and package routes are
``tests/test_release_member_identity_integration.py``.
"""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

import pytest

from abicheck.binary_utils import build_match_map
from abicheck.extract.member_identity import recorded_library_name
from abicheck.model import AbiSnapshot
from abicheck.model.bundle_facts import BundleFacts
from abicheck.serialization import snapshot_to_json
from abicheck.storage.snapshot_codec import save_snapshot
from abicheck.workflows.release_member_pairing import (
    facts_paired_by_identity,
    pair_path_maps_by_identity,
    path_identity,
)

#: Recorded library -> the release key a live file of that name has.
#: Written out by hand: the oracle must not be the canonicalizer.
_LIBRARIES: dict[str, str] = {
    "libfoo.so": "libfoo.so",
    "libfoo.so.1": "libfoo.so",
    "libfoo.so.1.2.3": "libfoo.so",
    "Foo.dll": "foo.dll",
    "libfoo.1.dylib": "libfoo.dylib",
    "libfoo.dylib": "libfoo.dylib",
}

#: How a stored member's document may be named on disk.
_STORED_NAMES: dict[str, str] = {
    "unrelated_json": "dump-{digest}.json",
    "stem_json": "{stem}.json",
    "dump_default": "{lib}.abicheck.json",
    "gzip": "{lib}.abicheck.json.gz",
    "zstd": "{lib}.json.zst",
    "named_as_library": "{lib}",
}


def _stored(directory: Path, library: str, spelling: str) -> Path:
    stem = library.split(".", 1)[0]
    digest = hashlib.sha256(library.encode()).hexdigest()[:8]
    path = directory / _STORED_NAMES[spelling].format(
        lib=library, stem=stem, digest=digest
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    save_snapshot(AbiSnapshot(library=library, version="1"), path)
    return path


def _live_stub(directory: Path, name: str) -> Path:
    """A live member: a native-looking file whose identity is its name."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"\x7fELF" + b"\0" * 60)
    return path


def _paired(old: dict[str, Path], new: dict[str, Path]) -> set[tuple[str, str]]:
    """The (OLD file, NEW file) pairs a key intersection makes after the
    identity pass."""
    new = pair_path_maps_by_identity(old, new)
    return {(old[k].name, new[k].name) for k in set(old) & set(new)}


@pytest.mark.parametrize(
    ("library", "spelling"), list(itertools.product(_LIBRARIES, _STORED_NAMES))
)
@pytest.mark.parametrize("stored_side", ["old", "new"])
def test_a_stored_member_pairs_with_the_live_library_it_records(
    tmp_path: Path, library: str, spelling: str, stored_side: str
) -> None:
    stored = _stored(tmp_path / "stored", library, spelling)
    live = _live_stub(tmp_path / "live", library)
    stored_map, _ = build_match_map([stored])
    live_map, _ = build_match_map([live])
    # The oracle table names the key a live file of that library has.
    assert set(live_map) == {_LIBRARIES[library]}

    old, new = (
        (stored_map, live_map) if stored_side == "old" else (live_map, stored_map)
    )
    expected = (
        {(stored.name, live.name)}
        if stored_side == "old"
        else {(live.name, stored.name)}
    )
    assert _paired(old, new) == expected


@pytest.mark.parametrize("spelling", list(_STORED_NAMES))
def test_a_stored_directory_pairs_every_member_of_the_live_one(
    tmp_path: Path, spelling: str
) -> None:
    libraries = ("libalpha.so.1", "libbeta.so.2.3", "libgamma.so")
    live = {lib: _live_stub(tmp_path / "live", lib) for lib in libraries}
    stored = {
        lib: _stored(tmp_path / "stored" / lib, lib, spelling) for lib in libraries
    }

    pairs = _paired(
        build_match_map(list(stored.values()))[0],
        build_match_map(list(live.values()))[0],
    )

    assert pairs == {(stored[lib].name, live[lib].name) for lib in libraries}


def test_a_file_name_pair_never_moves(tmp_path: Path) -> None:
    """Every document records ``libfoo.so``: the name-matched pair stays,
    and the leftovers pair nothing, because the library is already held."""
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    for directory, names in (
        (old_dir, ("libfoo.json", "libremoved.json")),
        (new_dir, ("libfoo.json", "libadded.json")),
    ):
        directory.mkdir()
        for name in names:
            (directory / name).write_text(
                snapshot_to_json(AbiSnapshot(library="libfoo.so", version="1"))
            )
    old_map, _ = build_match_map(sorted(old_dir.iterdir()))
    new_map, _ = build_match_map(sorted(new_dir.iterdir()))

    assert _paired(old_map, new_map) == {("libfoo.json", "libfoo.json")}


def test_a_library_claimed_twice_on_one_side_pairs_nothing(tmp_path: Path) -> None:
    old = {
        name: _stored(tmp_path / "old" / name, "libfoo.so.1", "unrelated_json")
        for name in ("a", "b")
    }
    old_map = {f"{name}.json": path for name, path in old.items()}
    new_map, _ = build_match_map([_live_stub(tmp_path / "new", "libfoo.so.1")])

    assert _paired(old_map, new_map) == set()


def test_bundle_facts_documents_pair_by_recorded_library() -> None:
    """The stored/stored route: a document keyed by a dump's file name pairs
    with one keyed by the live name; a name-matched member is untouched."""

    def facts(members: dict[str, str]) -> BundleFacts:
        return BundleFacts(
            variant_fingerprint="v",
            per_library_snapshots={
                key: AbiSnapshot(library=lib, version="1")
                for key, lib in members.items()
            },
        )

    old = facts({"libalpha.json": "libalpha.so.1", "libgamma.so": "libgamma.so"})
    new = facts(
        {
            "libalpha.so": "libalpha.so.1",
            "libgamma.so": "libgamma.so",
            "libnew.so": "libnew.so",
        }
    )

    paired = facts_paired_by_identity(old, new)

    assert set(paired.per_library_snapshots) == {
        "libalpha.json",
        "libgamma.so",
        "libnew.so",
    }
    assert paired.per_library_snapshots["libalpha.json"].library == "libalpha.so.1"
    assert paired.library_filenames["libalpha.json"] == "libalpha.so"


def test_a_snapshot_recording_no_library_keys_by_its_file_name(tmp_path: Path) -> None:
    path = tmp_path / "plugin.json"
    path.write_text(snapshot_to_json(AbiSnapshot(library="", version="1")))

    assert recorded_library_name(path) is None
    assert path_identity("plugin.json", path) == "plugin.json"


@pytest.mark.parametrize(
    "content",
    [b"\x7fELF" + b"\0" * 60, b"INPUT(libfoo.so.1)\n", b"plain text\n", b"{not json"],
    ids=["native", "linker_script", "text", "broken_json"],
)
def test_a_non_snapshot_member_is_its_own_key(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "libbar.so.3"
    path.write_bytes(content)

    assert recorded_library_name(path) is None
    assert path_identity("libbar.so", path) == "libbar.so"


def test_a_recorded_path_identifies_by_its_last_component(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(
        snapshot_to_json(AbiSnapshot(library="/usr/lib/libfoo.so.1", version="1"))
    )

    assert recorded_library_name(path) == "libfoo.so.1"
    assert path_identity("x.json", path) == "libfoo.so"

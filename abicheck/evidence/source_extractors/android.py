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

"""Android header-checker adapter (ADR-030 D6 table, D9; phase 6).

A reference adapter that reuses Android's VNDK header-checker output
(``header-abi-dumper`` ``.sdump`` / ``header-abi-linker`` ``.lsdump``) as an L4
source ABI backend. Per ADR-030 D9 the Android intermediate formats are
documented implementation details, so they are **never** the stable contract:
this adapter normalizes them into abicheck's own :class:`SourceAbiTu`, and the
raw dump is preserved only as provenance (``raw/android-header-abi/``).

Default behaviour is **non-executing** (ADR-028 D6): the adapter consumes a
*pre-captured* dump file produced by an existing Android build. Actually running
``header-abi-dumper`` is opt-in (:meth:`AndroidHeaderAbiAdapter.run_dumper`),
since it compiles a header.

The dump JSON shape follows AOSP ``vndk/tools/header-checker`` (records / enums /
functions / global vars keyed by ``linker_set_key`` mangled names). Parsing is
defensive ``.get()`` access so a newer/hand-edited dump never aborts a load.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..model import EvidenceConfidence
from ..source_abi import SourceAbiTu, SourceEntity, SourceLocation
from .base import SourceExtractionError

#: Adapter schema/behaviour version, recorded in the dump provenance.
ANDROID_EXTRACTOR_VERSION = "0.1"


def _hash(*parts: str) -> str:
    blob = "\x00".join(parts).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _location(path: str) -> SourceLocation:
    # header-abi dumps the public ABI surface (filtered by export/version script),
    # so every entity is treated as public-header origin.
    return SourceLocation(path=path, origin="PUBLIC_HEADER")


def _record_entity(rec: dict[str, Any]) -> SourceEntity:
    name = _str(rec.get("name") or rec.get("linker_set_key"))
    fields = rec.get("fields") or []
    field_repr = ";".join(
        f"{_str(f.get('field_name'))}:{_str(f.get('referenced_type'))}"
        f"@{f.get('field_offset', 0)}"
        for f in fields
        if isinstance(f, dict)
    )
    type_repr = f"record|size={rec.get('size', 0)}|{field_repr}"
    return SourceEntity(
        id=_hash("record", name, type_repr),
        kind="record",
        qualified_name=name,
        type_hash=_hash("type", type_repr),
        source_location=_location(_str(rec.get("source_file"))),
        visibility="public_header",
        api_relevant=True,
        confidence=EvidenceConfidence.HIGH,
    )


def _enum_entity(en: dict[str, Any]) -> SourceEntity:
    name = _str(en.get("name") or en.get("linker_set_key"))
    members = en.get("enum_fields") or []
    type_repr = f"{_str(en.get('underlying_type'))}|" + ",".join(
        f"{_str(m.get('name'))}={m.get('enum_field_value', 0)}"
        for m in members
        if isinstance(m, dict)
    )
    return SourceEntity(
        id=_hash("enum", name, type_repr),
        kind="enum",
        qualified_name=name,
        type_hash=_hash("type", type_repr),
        source_location=_location(_str(en.get("source_file"))),
        visibility="public_header",
        api_relevant=True,
        confidence=EvidenceConfidence.HIGH,
    )


def _function_entity(fn: dict[str, Any]) -> SourceEntity:
    name = _str(fn.get("function_name") or fn.get("linker_set_key"))
    mangled = _str(fn.get("linker_set_key"))
    params = fn.get("parameters") or []
    param_types = ",".join(
        _str(p.get("referenced_type")) for p in params if isinstance(p, dict)
    )
    sig = f"{_str(fn.get('return_type'))}({param_types})"
    return SourceEntity(
        id=_hash("function", mangled or name, sig),
        kind="function",
        qualified_name=name,
        mangled_name=mangled if mangled and mangled != name else "",
        signature_hash=_hash("sig", sig),
        source_location=_location(_str(fn.get("source_file"))),
        visibility="public_header",
        api_relevant=True,
        confidence=EvidenceConfidence.HIGH,
    )


def _global_var_entity(var: dict[str, Any]) -> SourceEntity:
    name = _str(var.get("name") or var.get("linker_set_key"))
    mangled = _str(var.get("linker_set_key"))
    return SourceEntity(
        id=_hash("variable", mangled or name, _str(var.get("referenced_type"))),
        kind="variable",
        qualified_name=name,
        mangled_name=mangled if mangled and mangled != name else "",
        type_hash=_hash("type", _str(var.get("referenced_type"))),
        source_location=_location(_str(var.get("source_file"))),
        visibility="public_header",
        api_relevant=True,
        confidence=EvidenceConfidence.HIGH,
    )


def parse_android_dump(
    data: dict[str, Any],
    *,
    source: str = "",
    target_id: str = "",
    tu_id: str = "",
    public_header_roots: list[str] | None = None,
) -> SourceAbiTu:
    """Normalize an Android ``.sdump``/``.lsdump`` JSON object into a SourceAbiTu (D9).

    Pure: any producer of the dump dict (a pre-captured file, or a test fixture)
    reuses this. Records/enums route to ``types``; functions to ``functions``;
    global vars to ``variables``. Android does not emit inline/template bodies or
    macros, so those buckets stay empty (the Clang backend, phase 5, owns them).
    """
    records = [r for r in (data.get("record_types") or []) if isinstance(r, dict)]
    enums = [e for e in (data.get("enum_types") or []) if isinstance(e, dict)]
    functions = [f for f in (data.get("functions") or []) if isinstance(f, dict)]
    global_vars = [g for g in (data.get("global_vars") or []) if isinstance(g, dict)]
    return SourceAbiTu(
        tu_id=tu_id or (f"cu://{source}" if source else "android-header-abi"),
        target_id=target_id,
        extractor={"name": "android-header-abi", "version": ANDROID_EXTRACTOR_VERSION},
        source=source,
        public_header_roots=list(public_header_roots or []),
        types=(
            [_record_entity(r) for r in records] + [_enum_entity(e) for e in enums]
        ),
        functions=[_function_entity(f) for f in functions],
        variables=[_global_var_entity(g) for g in global_vars],
    )


class AndroidHeaderAbiAdapter:
    """Reuse Android header-checker dumps as an L4 source ABI backend (D9, phase 6).

    Default mode consumes a *pre-captured* dump (non-executing, ADR-028 D6):

        >>> adapter = AndroidHeaderAbiAdapter()
        >>> tu = adapter.load(Path("libfoo.lsdump"))

    Running ``header-abi-dumper`` to produce a fresh dump is opt-in via
    :meth:`run_dumper`, since it compiles a header.
    """

    name = "android-header-abi"
    version = ANDROID_EXTRACTOR_VERSION

    def __init__(self, *, dumper_bin: str = "header-abi-dumper", timeout: int = 180) -> None:
        self.dumper_bin = dumper_bin
        self.timeout = timeout

    def available(self) -> bool:
        return shutil.which(self.dumper_bin) is not None

    def load(
        self,
        dump_path: Path | str,
        *,
        target_id: str = "",
        public_header_roots: list[str] | None = None,
    ) -> SourceAbiTu:
        """Load and normalize a pre-captured ``.sdump``/``.lsdump`` JSON dump."""
        path = Path(dump_path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise SourceExtractionError(f"cannot read Android dump {path}: {exc}") from exc
        except ValueError as exc:
            raise SourceExtractionError(
                f"Android dump {path} is not JSON (a raw protobuf .sdump must be "
                f"produced with `-output-format Json`): {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise SourceExtractionError(
                f"Android dump {path} must be a JSON object, got {type(data).__name__}"
            )
        return parse_android_dump(
            data,
            source=_str(data.get("source_file")) or path.stem,
            target_id=target_id,
            public_header_roots=public_header_roots,
        )

    def run_dumper(
        self,
        header: Path | str,
        *,
        output: Path | str,
        clang_argv: list[str] | None = None,
        target_id: str = "",
        public_header_roots: list[str] | None = None,
    ) -> SourceAbiTu:
        """Opt-in: run ``header-abi-dumper`` on a header, then normalize (ADR-032 D5).

        This compiles the header, so it is never invoked by default collection.
        Requires the Android tool on ``PATH``.
        """
        if not self.available():
            raise SourceExtractionError(
                f"{self.dumper_bin} not found in PATH; pass a pre-captured dump to "
                "load() instead, or install the Android header-checker tools."
            )
        out = Path(output)
        cmd = [self.dumper_bin, str(header), "-o", str(out), "-output-format", "Json"]
        if clang_argv:
            cmd += ["--", *clang_argv]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise SourceExtractionError(
                f"header-abi-dumper timed out after {self.timeout}s on {header}"
            ) from exc
        if result.returncode != 0 or not out.is_file():
            raise SourceExtractionError(
                f"header-abi-dumper failed on {header} (exit {result.returncode}): "
                f"{result.stderr[:1000]}"
            )
        return self.load(
            out, target_id=target_id, public_header_roots=public_header_roots
        )

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

"""ADR-050 D1 extraction-contract attachment, factored out of :mod:`dumper`.

Relocated out of ``dumper.py`` (PR #624 follow-up) to free line budget --
``dumper.py`` sits at the AI-readiness file-size hard cap, so any
net-positive addition there needs an equal-or-greater reduction elsewhere
first. Pure relocation: :func:`_attach_extraction_contract` is unchanged
except for its module home, re-exported from ``dumper.py`` so
``dumper._attach_extraction_contract`` (the bare name ``dumper.dump()``
calls, and the import ``service.py`` uses to reach the same path from its
PE/Mach-O header-scoped dump route) keeps working unchanged.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from ._compiler_options import language_standard_field
from .model import AbiSnapshot


def _attach_extraction_contract(
    snapshot: AbiSnapshot,
    *,
    headers: list[Path],
    extra_includes: list[Path] | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    lang: str | None,
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
) -> None:
    """Populate *snapshot*.contract in place from this dump's resolved
    extraction inputs (profile/scope fingerprints), so a later
    ``compare()`` can prove two snapshots were extracted comparably
    (``comparability.check_contracts_comparable``, wired into
    ``checker.compare`` since PR #624). ``compute_extraction_contract``
    itself returns ``None`` when there is nothing to fingerprint at all
    (see its docstring).

    Called both by ``dumper.dump()`` and by any other snapshot-producing
    path that runs a header-AST frontend directly rather than through
    ``dump()`` -- e.g. ``service.py``'s PE/Mach-O header-scoped dump path
    (``_try_header_scoped_dump``), which calls
    ``dumper._dump_pe``/``dumper._dump_macho`` directly and would otherwise
    always leave ``contract=None`` on an otherwise-genuine header-based
    dump (Codex review, PR #624 follow-up). The ELF equivalent
    (``service._dump_elf``) already routes through ``dumper.dump()``
    itself, so it needs no separate call.
    """
    from .comparability import IncludeDir, compute_extraction_contract
    from .dumper_toolchain import _compiler_family_from_toolchain
    from .header_conditionals import (
        ordered_macro_ops,
        pass_through_flags_from_tokens,
        resolve_pass_through_paths,
    )

    _flag_tokens = list(gcc_option_tokens)
    if gcc_options:
        try:
            _flag_tokens = (
                shlex.split(gcc_options, posix=os.name != "nt") + _flag_tokens
            )
        except ValueError:
            pass  # malformed --gcc-options must not abort the dump

    snapshot.contract = compute_extraction_contract(
        compiler_family=_compiler_family_from_toolchain(snapshot.ast_toolchain),
        compiler_version=(
            snapshot.ast_toolchain.get("compiler_version")
            or snapshot.ast_toolchain.get("version")
            or None
        ),
        abi_dialect=snapshot.ast_toolchain.get("abi_dialect"),
        language_standard=language_standard_field(lang, gcc_options, gcc_option_tokens),
        macro_ops=ordered_macro_ops(_flag_tokens),
        pass_through_flags=resolve_pass_through_paths(
            pass_through_flags_from_tokens(_flag_tokens), extra_includes or []
        ),
        # Gate declared_headers/declared_includes on from_headers, NOT on
        # `headers` being non-empty: headers can be supplied yet fully
        # ignored (dwarf_only=True / symbols_only=True force a DWARF/
        # symbols path even with headers given — see _dump_elf). Feeding an
        # ignored `headers` list in would falsely assert a header-parsed
        # scope for a DWARF-derived surface.
        declared_headers=list(headers) if snapshot.from_headers else [],
        declared_includes=(
            [IncludeDir(path=p) for p in extra_includes]
            if snapshot.from_headers and extra_includes
            else []
        ),
        l2_frontend_ran=snapshot.from_headers,
        public_header_paths=list(public_headers or []),
        public_header_dirs=list(public_header_dirs or []),
    )

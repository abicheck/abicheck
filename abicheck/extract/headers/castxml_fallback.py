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

"""The castxml-to-clang fallback decision for a failed header parse.

Moved out of ``dumper.py`` (pure move, behaviour unchanged). The logger keeps
the ``abicheck.dumper`` name so existing log routing is unchanged.
"""

from __future__ import annotations

import logging

from ...dumper_clang_errors import _is_direct_include_guard_failure
from ...errors import SnapshotError, UnsupportedCastxmlVersionError
from ..compiler_driver import resolve_clang_bin
from .backend_plan import BackendPlan
from .castxml.probe import _is_toolchain_version_failure

__all__ = ["_castxml_fallback_reason"]

log = logging.getLogger("abicheck.dumper")


def _castxml_fallback_reason(
    exc: SnapshotError,
    *,
    auto_selected: bool,
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    plan: BackendPlan,
) -> str | None:
    """Decide whether a failed castxml dump may fall back to the clang backend.

    Returns the ``fallback_reason`` to stamp on the clang parser, or ``None``
    when the caller must re-raise *exc* unchanged. Raises an annotated copy of
    *exc* when the failure *is* fallback-eligible but the opt-in is off, so the
    user is told why the fallback did not happen rather than just seeing the raw
    castxml error. Split out of :func:`_header_ast_parser`; the reasoning behind
    each eligible signature is in the comments below.
    """
    # A proactive UnsupportedCastxmlVersionError (raised before castxml
    # even runs) is exactly the same "this castxml can't be trusted"
    # signal as the two string-matched stderr signatures below — it's
    # just detected earlier and more precisely (an exact version
    # comparison instead of a diagnostic-text guess). The opt-in
    # fallback's whole purpose is letting a user accept the
    # castxml/clang discrepancy risk to keep scanning on a host whose
    # castxml can't be trusted; excluding this one reason a castxml is
    # untrusted defeated that opt-in for exactly the case this PR's own
    # new gate creates (Codex review).
    is_version_gate_failure = isinstance(exc, UnsupportedCastxmlVersionError)
    eligible = auto_selected and (
        is_version_gate_failure
        or _is_toolchain_version_failure(str(exc))
        or _is_direct_include_guard_failure(str(exc))
    )
    if not eligible:
        return None

    # Probe the driver _run_clang() would actually invoke (honors
    # --compiler/--compiler-prefix), not just a bare "clang" on PATH (Codex
    # review).
    def _clang_fallback_ready() -> bool:
        try:
            resolve_clang_bin(compiler, gcc_path, gcc_prefix)
            return True
        except SnapshotError:
            return False

    if not plan.ast_fallback_enabled or not _clang_fallback_ready():
        message = (
            f"{exc}\n\nAutomatic CastXML-to-Clang fallback is disabled because "
            "the two frontends can produce materially different findings. "
            "Install a compatible CastXML, select the clang backend explicitly "
            "(.abicheck.yml compile.frontend: clang, or ABICHECK_AST_FRONTEND=clang), "
            "or opt in to the fallback (compile.ast_frontend_fallback: true, or "
            "ABICHECK_ALLOW_AST_FALLBACK=1)."
        )
        raise type(exc)(message) from exc
    log.warning(
        "castxml could not parse the header(s) (toolchain mismatch, an "
        "unsupported castxml version, or a header that refuses direct "
        "inclusion); falling back to the clang header backend, which "
        "parses against the host toolchain and can exclude direct-include "
        "#error guard headers. Set compile.frontend: castxml in .abicheck.yml "
        "(or ABICHECK_AST_FRONTEND=castxml) to force castxml and see the "
        "original error."
    )
    if is_version_gate_failure:
        return "castxml-unsupported-version"
    if _is_toolchain_version_failure(str(exc)):
        return "castxml-toolchain-version-mismatch"
    return "castxml-direct-include-guard"

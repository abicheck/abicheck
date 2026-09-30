"""A stand-in for the one L5 clang AST pass (``l5_ast_pass.run_ast_passes``).

The clang-backed graph folds read one shared run instead of each owning a
``Clang*GraphExtractor``, so a test that used to replace one extractor class
now describes that family's outcome here: a function of the *scoped* target
the pass was run on (what an extractor's ``extract_from_build(build)``
received), plus optional per-family diagnostics. A family left out behaves
like a clean run that found nothing -- its merge of zero TUs, no diagnostics.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from abicheck.buildsource import l5_ast_pass
from abicheck.buildsource.build_evidence import BuildEvidence
from abicheck.buildsource.l5_ast_run import AstPassOutcome


def install_fake_l5(
    monkeypatch: Any,
    *,
    available: bool = True,
    results: dict[str, Callable[[BuildEvidence], Any]] | None = None,
    diagnostics: dict[str, Callable[[BuildEvidence], list[str]]] | None = None,
) -> list[BuildEvidence]:
    """Patch the L5 AST pass; return the list of targets it was run on."""
    results = results or {}
    diagnostics = diagnostics or {}
    unknown = (set(results) | set(diagnostics)) - {
        p.name for p in l5_ast_pass.L5_AST_PASSES
    }
    assert not unknown, f"no such L5 family: {sorted(unknown)}"
    seen: list[BuildEvidence] = []

    def fake_run(target: BuildEvidence, clang_bin: str, passes: Any = None) -> dict:
        seen.append(target)
        return {
            p.name: AstPassOutcome(
                result=results[p.name](target) if p.name in results else p.merge([]),
                diagnostics=list(diagnostics[p.name](target))
                if p.name in diagnostics
                else [],
                last_jobs=1,
            )
            for p in l5_ast_pass.L5_AST_PASSES
        }

    monkeypatch.setattr(l5_ast_pass, "_clang_available", lambda _binary: available)
    monkeypatch.setattr(l5_ast_pass, "run_ast_passes", fake_run)
    return seen

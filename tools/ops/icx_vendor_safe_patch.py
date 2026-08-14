#!/usr/bin/env python3
"""Create temporary ICX plugin variants for compatibility probing."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label}, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path, mode: str) -> None:
    text = path.read_text()

    text = replace_once(
        text,
        """                                           Version, computeContextHash(ci),
                                           macros, inferredRootCount);""",
        """                                           Version, std::string("icx-experimental-context"),
                                           macros, inferredRootCount);""",
        "computeContextHash call",
    )

    text = replace_once(
        text,
        "    auto macros = std::make_shared<std::map<std::string, MacroRecord>>();",
        """    llvm::errs() << "ABICHECK_ICX_STEP: macros\\n";
    auto macros = std::make_shared<std::map<std::string, MacroRecord>>();""",
        "macro allocation",
    )
    text = replace_once(
        text,
        "    Preprocessor &pp = ci.getPreprocessor();",
        """    llvm::errs() << "ABICHECK_ICX_STEP: preprocessor\\n";
    Preprocessor &pp = ci.getPreprocessor();""",
        "preprocessor access",
    )
    text = replace_once(
        text,
        "    std::vector<std::string> roots = Roots;",
        """    llvm::errs() << "ABICHECK_ICX_STEP: roots\\n";
    std::vector<std::string> roots = Roots;""",
        "roots copy",
    )
    text = replace_once(
        text,
        "    return std::make_unique<FactsConsumer>(OutDir, std::move(roots), Library,",
        """    llvm::errs() << "ABICHECK_ICX_STEP: create-consumer\\n";
    return std::make_unique<FactsConsumer>(OutDir, std::move(roots), Library,""",
        "FactsConsumer return",
    )

    if mode in {"nocontext-nopp", "noop"}:
        text = replace_once(
            text,
            """    llvm::errs() << "ABICHECK_ICX_STEP: preprocessor\\n";
    Preprocessor &pp = ci.getPreprocessor();
    pp.addPPCallbacks(std::make_unique<MacroCollector>(pp, macros));""",
            """    llvm::errs() << "ABICHECK_ICX_STEP: preprocessor-skipped\\n";
    (void)ci;""",
            "PP callback block",
        )
        text = replace_once(
            text,
            """    if (roots.empty()) {
      roots = deriveRootsFromIncludes(ci.getHeaderSearchOpts());
      inferredRootCount = roots.size();
    }""",
            """    if (roots.empty())
      llvm::errs() << "ABICHECK_ICX_STEP: roots-empty-no-derivation\\n";""",
            "root derivation block",
        )

    if mode == "noop":
        pattern = re.compile(
            r'''    llvm::errs\(\) << "ABICHECK_ICX_STEP: create-consumer\\n";\n'''
            r'''    return std::make_unique<FactsConsumer>\(OutDir, std::move\(roots\), Library,\n'''
            r'''\s+Version, std::string\("icx-experimental-context"\),\n'''
            r'''\s+macros, inferredRootCount\);'''
        )
        replacement = (
            '    llvm::errs() << "ABICHECK_ICX_STEP: noop-consumer\\n";\n'
            '    return std::make_unique<ASTConsumer>();'
        )
        text, count = pattern.subn(replacement, text, count=1)
        if count != 1:
            raise RuntimeError(f"expected one FactsConsumer return, replaced {count}")

    path.write_text(text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("nocontext", "nocontext-nopp", "noop"))
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    patch(args.source, args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

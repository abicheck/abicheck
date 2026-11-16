"""Binary facts an independent tool reads off two built libraries (not abicheck).

The rebuild operators' ground truth can depend on a fact of the built
artifact, which the expectation cannot know from the configuration alone: an
``-O2`` rebuild drops ``__stack_chk_fail`` only when no frame in the library
needs a canary. These facts come from ``nm`` (GNU binutils) on each side's
dynamic symbol table and are the execution-oracle counterpart of a
``BinaryLicence`` (``model/expect.py``): a finding the verdict is allowed to
carry only when this oracle confirms the fact behind it.

abiforge does not import abicheck here: the facts are read by binutils, so
the checker under test cannot influence the oracle that judges it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: The protector's runtime entry point: an ``-fstack-protector`` frame calls it.
STACK_CHK_FAIL = "__stack_chk_fail"


def undefined_symbols(lib: Path) -> frozenset[str]:
    """The dynamic undefined (imported) symbol names of *lib*, versions stripped."""
    out = subprocess.run(
        ["nm", "-D", "--undefined-only", "-P", str(lib)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    names = set()
    for line in out.splitlines():
        fields = line.split()
        if fields:
            names.add(fields[0].split("@", 1)[0])
    return frozenset(names)


def binary_facts(old: Path, new: Path) -> dict[str, bool]:
    """The named facts one rebuild pair shows, each True when the oracle sees it."""
    old_undef = undefined_symbols(old)
    new_undef = undefined_symbols(new)
    return {
        # The old build calls the canary check and the new one no longer does.
        "stack_chk_fail_dropped": STACK_CHK_FAIL in old_undef
        and STACK_CHK_FAIL not in new_undef,
    }

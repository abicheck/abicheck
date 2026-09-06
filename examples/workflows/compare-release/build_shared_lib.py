#!/usr/bin/env python3
"""Portable substitute for `gcc -shared ...`.

Apple's system compiler (a clang alias, whatever name it's invoked under)
has no -shared flag at all and requires -dynamiclib instead; every other
platform this walkthrough targets uses real GNU gcc's -shared. Pass this
script every argument gcc would otherwise take *after* that one
link-mode flag -- e.g. `gcc -shared -fPIC -g x.c -o libx.so` becomes
`python3 build_shared_lib.py -fPIC -g x.c -o libx.so`.
"""

import subprocess
import sys

flag = "-dynamiclib" if sys.platform == "darwin" else "-shared"
sys.exit(subprocess.run(["gcc", flag, *sys.argv[1:]]).returncode)

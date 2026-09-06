#!/usr/bin/env python3
"""Write compile_commands.json for one side's source directory.

A compile database's `directory`/`file` entries must be absolute (or at
least resolvable from wherever a tool consuming it happens to run) --
plain JSON can't embed "whatever directory this scratch copy landed in"
itself, so this one-line generator fills that in with a real absolute
path at run time instead of shipping a JSON file with a path baked in
that would only be correct on one machine.
"""
import json
import os
import sys

side_dir = sys.argv[1]
abs_dir = os.path.abspath(side_dir)
db = [
    {
        "directory": abs_dir,
        "command": f"gcc -I{abs_dir} -c widget.c -o widget.o",
        "file": os.path.join(abs_dir, "widget.c"),
    }
]
with open(os.path.join(side_dir, "compile_commands.json"), "w") as f:
    json.dump(db, f)

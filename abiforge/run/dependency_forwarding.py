"""Exercise prerequisite forwarding across an unchanged C call boundary."""

import os
import subprocess
from pathlib import Path

HEADER = """typedef void (*task)(int *);
void submit(task prerequisite, task work, int *value);
"""
CLIENT = """#include "task.h"
#include <stdio.h>
static void prepare(int *v) { *v = 20; }
static void consume(int *v) { *v = *v * 2 + 2; }
int main(void) {
    int value = 0;
    submit(prepare, consume, &value);
    printf("%d\\n", value);
}
"""
# An explicit prerequisite makes ordering measurable without timing or a GPU.
BODIES = {
    "baseline": "prerequisite(value); work(value);",
    "forward_copy": "task dependency = prerequisite; dependency(value); work(value);",
    "drop_dependency": "work(value);",
    "reverse_order": "work(value); prerequisite(value);",
}


def run(root: Path) -> dict[str, int]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "task.h").write_text(HEADER)
    client = root / "client"
    client_source = root / "client.c"
    client_source.write_text(CLIENT)
    observations = {}
    for case, body in BODIES.items():
        provider = root / case
        provider.mkdir(exist_ok=True)
        source = provider / "task.c"
        source.write_text(
            '#include "task.h"\nvoid submit(task prerequisite, task work, int *value) {'
            + body
            + "}\n"
        )
        subprocess.run(
            [
                "cc",
                "-shared",
                "-fPIC",
                "-I",
                root,
                source,
                "-o",
                provider / "libtask.so",
            ],
            check=True,
        )
        if case == "baseline":
            subprocess.run(
                ["cc", client_source, "-L", provider, "-ltask", "-o", client],
                check=True,
            )
        observations[case] = int(
            subprocess.check_output(
                [client], env={**os.environ, "LD_LIBRARY_PATH": str(provider)}
            )
        )
    return observations

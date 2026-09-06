# Evidence depth: what does the evidence you gave abicheck actually let it see?

The same release can ship two different ABI problems at once, and how much
of each one abicheck can see depends entirely on what you hand it — the
binaries alone, the binaries plus public headers, or the binaries plus
headers plus the real build (sources and compile commands). This walkthrough
runs the *same* comparison three times, each time with more evidence, and
shows a real problem appear at each step that the step before it missed
completely. See [Evidence & Detectability](../../../docs/learn/evidence-and-detectability.md)
for the full `L0`-`L5` model this walks through in miniature, and
[Source-scan depth](../../../docs/use/scan-levels.md) for the `--depth` dial
this uses (`compare`'s own evidence inputs — `--header`/`--sources`/
`--build-info` — drive the same depth ladder `scan --depth` names).

## The release

`widget.h` declares a public struct and a public macro:

```c
// v1/widget.h
#define BUFFER_LIMIT 16

struct Widget {
    int a;
    int b;
};

int widget_get_b(struct Widget *w);
int widget_get_limit(void);
```

Between v1 and v2, two things change — both real, both consumer-visible,
neither mentioned anywhere except the header/source diff itself:

```c
// v2/widget.h
#define BUFFER_LIMIT 32          // <- macro value changed

struct Widget {
    int a;
    int extra;                   // <- field inserted in the middle
    int b;
};

int widget_get_b(struct Widget *w);
int widget_get_limit(void);
```

Inserting `extra` shifts `b`'s offset — any consumer still using the old
`struct Widget` layout reads the wrong memory. Changing `BUFFER_LIMIT`'s
value means any consumer source that compiled the old value in (an array
size, a loop bound) now silently disagrees with what the new library was
built expecting. `widget.c`'s own logic is identical in both versions — only
the header changes.

## Step 1: binaries only — misses the break entirely

```bash
cd examples/workflows/evidence-depth

python3 build_shared_lib.py -fPIC -Iv1 v1/widget.c -o v1/libwidget.so
python3 build_shared_lib.py -fPIC -Iv2 v2/widget.c -o v2/libwidget.so

abicheck compare v1/libwidget.so v2/libwidget.so
```

```
| **Verdict** | ✅ `NO_CHANGE` |
```

With no debug info and no headers, abicheck has only the exported symbol
table to go on — `widget_get_b` and `widget_get_limit` are still exported,
with the same names, so nothing looks different. The report says so plainly:
`Coverage gap | Binary-only analysis without debug info; many ABI changes
cannot be detected (struct layout, enum values, type changes)`. This is a
real false negative, not a bug — L0 evidence is *structurally* blind to
layout and macro changes, and abicheck's own coverage-gap line is what tells
you not to trust this result as complete.

## Step 2: add public headers — catches the struct break

```bash
abicheck compare v1/libwidget.so v2/libwidget.so --header old=v1/widget.h --header new=v2/widget.h
```

```
| **Verdict** | ❌ `BREAKING` |

## ❌ Breaking Changes

- **type_size_changed**: Size changed: Widget (64 → 96 bits)
- **type_field_offset_changed**: Field offset changed: Widget::b (32 → 64 bits)
```

Now abicheck has the struct's real layout from the header AST, and the
inserted field is exactly the kind of break header evidence exists to
catch: exit code `4`. But look closely — `BUFFER_LIMIT` is nowhere in this
report. A `#define` is gone by the time a header is parsed into a
declaration tree; the AST backend never sees a macro *as* a macro, only
whatever value it already expanded to wherever it was used in a
declaration. Since `BUFFER_LIMIT` here is only ever used inside a function
*body* (not in any type or signature the header exposes), this evidence
level cannot see it changed at all.

## Step 3: add the real build — catches the macro too

```bash
python3 gen_build_info.py v1
python3 gen_build_info.py v2
abicheck compare v1/libwidget.so v2/libwidget.so --header old=v1/widget.h --header new=v2/widget.h --sources old=v1 --sources new=v2 --build-info old=v1/compile_commands.json --build-info new=v2/compile_commands.json --depth source
```

`gen_build_info.py` just writes a minimal `compile_commands.json` for its
argument directory with an absolute path baked in (a compile database has
to name real, resolvable paths, which can't be written down ahead of time
in a walkthrough anyone can copy to any machine) — see the script itself.

```
| **Verdict** | ❌ `BREAKING` |

## ❌ Breaking Changes

- **type_size_changed**: Size changed: Widget (64 → 96 bits)
- **type_field_offset_changed**: Field offset changed: Widget::b (32 → 64 bits)

## ⚠️ Source-Level Breaks

- **public_macro_value_changed**: Public macro 'BUFFER_LIMIT' value changed: '16' -> '32'.
```

With `--sources`/`--build-info` telling abicheck where the real source
lives and how it was compiled, `--depth source` replays each translation
unit the same way the real compiler would and can see the macro's own
definition change — a class of break (`public_macro_value_changed`) no
binary, no debug info, and no header AST can ever show, because a macro
never survives into any of those forms. The struct break from step 2 is
still reported (more evidence never hides a break a weaker tier already
caught); the macro break is new. The `BUFFER_LIMIT` value is baked into
`widget_get_limit`'s compiled body — a consumer that built against the old
header and links the new library, or vice versa, gets a limit that
silently disagrees with what it compiled against.

See [Build Info & Sources](../../../docs/learn/build-source-data.md) for the
full model of what `--sources`/`--build-info` add and how they combine with
the artifact tiers above them, and
[Build Evidence Setup](../../../docs/use/build-evidence-setup.md) for
producing this evidence from a real build system instead of a hand-written
compile database.

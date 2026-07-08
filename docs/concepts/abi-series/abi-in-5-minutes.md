# ABI in Five Minutes

> **New to the series?** This is the gentlest door. When you're ready for the
> full track, start at [Part 0 — Product Contract](00-product-contract.md) or
> [Part 1 — Foundations](01-foundations.md).

**What you'll learn on this page**

- What an **ABI** is, in one sentence, with no jargon.
- Why an app can crash after a library upgrade *even though nobody recompiled
  it*.
- The difference between a change that is **safe**, **risky**, or **breaking**.

No prior knowledge needed. This page is a five-minute on-ramp; every idea here is
developed properly later in the series.

!!! note "ELF/Linux first"
    Like the rest of this series, the examples use the **Linux** model
    (`libfoo.so.1`). Windows and macOS have the same problem with different
    spellings.

---

## A story: the upgrade that crashed

You ship an app. It uses a library called `libfoo`, so at install time your app
links against `libfoo.so.1`. Everything works.

Months later, the vendor ships `libfoo.so.2`. A package update drops the new file
onto the machine. You **did not rebuild your app** — you didn't even touch it.
The next time it runs, it crashes.

Why? Your app was *compiled* against the old `libfoo`. During compilation, the
compiler copied hard facts out of the library's headers and baked them into your
app's machine code: how big a `Foo` struct is, which slot a function lives in,
which CPU register holds which argument. Those numbers became constants inside
your binary. Nobody re-checks them at runtime.

When `libfoo.so.2` changed one of those facts — say, it added a field to `Foo`,
so `Foo` is now bigger — your app is still using the *old* size. It reads and
writes the wrong bytes. The result is a crash, or worse, quietly corrupted data.

**That mismatch is an ABI break.**

---

## API vs ABI, in one sentence each

- An **API** (Application Programming Interface) is the **source-level** contract
  — the function names and types you write against in the headers. Break it and
  the **compiler** complains: the build fails, points at a line, you fix it.
- An **ABI** (Application Binary Interface) is the **binary-level** contract
  between already-compiled programs — the exact sizes, layouts, symbol names, and
  calling conventions. Break it and **nobody** complains until it crashes at
  runtime, sometimes not even then.

The whole reason ABI is scary: an API break is loud and immediate; an ABI break
is silent and delayed.

---

## A taste: safe, risky, breaking

Three changes a library author might make, and what each one does to apps built
against the old version:

| Change | What happens to old apps | Verdict |
|--------|--------------------------|---------|
| **Add a brand-new function** | Nothing — they never called it. | 🟢 safe |
| **Make an existing function no longer promise "won't throw"** | Still links, but a thrown exception may now escape somewhere the old code didn't expect. | 🟡 risky |
| **Add a field to a struct they pass by value** | The old size is baked in; they read/write the wrong bytes → crash or corruption. | 🔴 breaking |

That's the entire job of an ABI checker: look at two versions of a library and
sort every change into one of those buckets — *before* you ship the new one.

---

## Where to go next

You now have the one idea the whole series builds on: **the compiler bakes the
library's promises into the caller and never re-checks them.**

- **[Part 0 — Compatibility as a Product Contract](00-product-contract.md)** —
  why "is this a break?" depends on what you *promised*, not just what changed.
  Start here if you like framing before mechanism.
- **[Part 1 — Foundations](01-foundations.md)** — the build/link/load pipeline,
  what a symbol is, and where each kind of break happens. Start here if you want
  the mechanics first.
- **[ABI Cheat Sheet](../abi-cheat-sheet.md)** — the one-screen summary of
  verdicts and common changes, to keep open while you read.

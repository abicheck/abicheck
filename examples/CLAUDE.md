# CLAUDE.md — `examples/`

The ABI-scenario catalog: 121 cases numbered contiguously (`01–120` +
`26b`), including 5 multi-library bundle cases. Each case is a minimal,
compilable C/C++ example demonstrating a specific ABI/API pitfall.

Read `README.md` in this directory first — it indexes every case and
explains the verdict taxonomy.

## Per-case layout

```
caseNN_<short_name>/
├── v1/           # baseline source + headers
├── v2/           # changed source + headers
├── app.c|cpp     # runtime consumer that demonstrates the actual failure
├── README.md     # what breaks and why
└── (optional) CMakeLists.txt
```

## Ground truth

The authoritative expected verdicts live in `ground_truth.json` at the
top of this directory. **If a per-case README disagrees with
`ground_truth.json`, `ground_truth.json` wins.**

`ground_truth.json` aligns with the 5-tier classification in
`abicheck/checker_policy.py`:
`BREAKING_KINDS` → `API_BREAK_KINDS` → `RISK_KINDS` → `QUALITY_KINDS`
→ `ADDITION_KINDS`.

## What NOT to do

- Don't modify a case's source or expected verdict without understanding
  what failure mode it encodes — these are calibration fixtures.
- Don't add a new case without:
  1. A per-case `README.md`.
  2. An entry in `ground_truth.json`.
  3. Regenerating `docs/examples/` via `scripts/gen_examples_docs.py`.
- Don't rely on `examples/<case>/README.md` alone — always cross-check
  against `ground_truth.json`.

## Adding a new case

1. Pick the next free `caseNN` number.
2. Write `v1/`, `v2/`, `app.c|cpp`, and a README.
3. Add the expected verdict to `ground_truth.json`.
4. Run `python scripts/gen_examples_docs.py` and commit the regenerated
   `docs/examples/caseNN_*.md`.
5. Validate with `pytest tests/test_abi_examples.py -k caseNN -m integration`.

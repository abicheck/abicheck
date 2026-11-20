# Tool comparison: pilot selection

## Coverage (generator registry vs this run)

| Count | Value |
|---|---|
| change classes declared (operators in the registry) | 364 |
| edit-model classes (language model, 10 constructs) | 168 |
| operator x seed pairs declared | 399 |
| operators selected / built | 15 / 15 |
| unique pairs selected (case x config) | 34 |
| pairs infeasible / build failed / built | 0 / 0 / 34 |
| pairs with independent expected decision | 34 |
| pairs with expected explanation details | 14 |

| Mode | pairs run | pairs answered | operators answered |
|---|---|---|---|
| ABI Dumper + ABICC | 34 | 17 | 15 |
| ABICC (XML descriptors) | 34 | 34 | 15 |
| abicheck compare | 34 | 34 | 15 |
| libabigail abidiff | 34 | 34 | 15 |

## Maintainer decision vs independent expectation

Denominator n = built pairs that mode was run on in that configuration.

| Config | Mode | n | correct | wrong | advisory | no signal | unsupported | error | timeout | controls | FP on controls | advisory on controls |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dwarf+headers | ABI Dumper + ABICC | 17 | 10 | 0 | 2 | 5 | 0 | 0 | 0 | 4 | 0 | 0 |
| dwarf+headers | ABICC (XML descriptors) | 17 | 10 | 0 | 3 | 4 | 0 | 0 | 0 | 4 | 0 | 0 |
| dwarf+headers | abicheck compare | 17 | 17 | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 |
| dwarf+headers | libabigail abidiff | 17 | 8 | 0 | 5 | 4 | 0 | 0 | 0 | 4 | 0 | 0 |
| stripped+headers | ABI Dumper + ABICC | 17 | 0 | 0 | 0 | 0 | 17 | 0 | 0 | 4 | 0 | 0 |
| stripped+headers | ABICC (XML descriptors) | 17 | 10 | 0 | 3 | 4 | 0 | 0 | 0 | 4 | 0 | 0 |
| stripped+headers | abicheck compare | 17 | 17 | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 |
| stripped+headers | libabigail abidiff | 17 | 8 | 0 | 0 | 9 | 0 | 0 | 0 | 4 | 0 | 0 |

## Explanation details (match / applicable; `unsup` = format cannot express)

| Config | Mode | entity | fields | size | offsets | functions |
|---|---|---|---|---|---|---|
| dwarf+headers | ABI Dumper + ABICC | 7/7 | 2/2 | 2/2 | unsup | 6/6 |
| dwarf+headers | ABICC (XML descriptors) | 7/7 | 2/2 | 2/2 | unsup | 6/6 |
| dwarf+headers | abicheck compare | 7/7 | 2/2 | 2/2 | 1/1 | 5/6 |
| dwarf+headers | libabigail abidiff | 5/7 | 2/2 | 2/2 | 1/1 | 5/6 |
| stripped+headers | ABI Dumper + ABICC | unsup | unsup | unsup | unsup | unsup |
| stripped+headers | ABICC (XML descriptors) | 7/7 | 2/2 | 2/2 | unsup | 6/6 |
| stripped+headers | abicheck compare | 7/7 | 2/2 | 2/2 | 1/1 | 5/6 |
| stripped+headers | libabigail abidiff | 1/7 | 0/2 | 0/2 | 0/1 | 1/6 |

## Pair-by-pair agreement between modes (equal totals are not equal answers)

| Config | Mode A | Mode B | pairs | same decision | same outcome | same details | both correct |
|---|---|---|---|---|---|---|---|
| dwarf+headers | ABI Dumper + ABICC | ABICC (XML descriptors) | 17 | 15 | 16 | 17 | 10 |
| dwarf+headers | ABI Dumper + ABICC | abicheck compare | 17 | 9 | 10 | 15 | 10 |
| dwarf+headers | ABI Dumper + ABICC | libabigail abidiff | 17 | 11 | 11 | 14 | 6 |
| dwarf+headers | ABICC (XML descriptors) | abicheck compare | 17 | 10 | 10 | 15 | 10 |
| dwarf+headers | ABICC (XML descriptors) | libabigail abidiff | 17 | 9 | 10 | 14 | 6 |
| dwarf+headers | abicheck compare | libabigail abidiff | 17 | 5 | 8 | 15 | 8 |
| stripped+headers | ABI Dumper + ABICC | ABICC (XML descriptors) | 17 | 0 | 0 | 10 | 0 |
| stripped+headers | ABI Dumper + ABICC | abicheck compare | 17 | 0 | 0 | 10 | 0 |
| stripped+headers | ABI Dumper + ABICC | libabigail abidiff | 17 | 0 | 0 | 10 | 0 |
| stripped+headers | ABICC (XML descriptors) | abicheck compare | 17 | 10 | 10 | 15 | 10 |
| stripped+headers | ABICC (XML descriptors) | libabigail abidiff | 17 | 7 | 8 | 11 | 6 |
| stripped+headers | abicheck compare | libabigail abidiff | 17 | 5 | 8 | 11 | 8 |

Environment: commit b0b8793989c150a5b05f48e4e999a7df4219365a, python 3.13.18, platform Linux x86_64, gcc 13.3.0, abidiff abidiff: 2.4.0, abi-dumper 1.2, abi-compliance-checker 2.3

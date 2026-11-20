# Tool comparison: all selection

## Coverage (generator registry vs this run)

| Count | Value |
|---|---|
| change classes declared (operators in the registry) | 364 |
| edit-model classes (language model, 10 constructs) | 168 |
| operator x seed pairs declared | 399 |
| operators selected / built | 364 / 323 |
| unique pairs selected (case x config) | 816 |
| pairs infeasible / build failed / built | 84 / 0 / 731 |
| pairs with independent expected decision | 731 |
| pairs with expected explanation details | 456 |

| Mode | pairs run | pairs answered | operators answered |
|---|---|---|---|
| ABI Dumper + ABICC | 731 | 365 | 322 |
| ABICC (XML descriptors) | 731 | 727 | 321 |
| abicheck compare | 731 | 730 | 323 |
| libabigail abidiff | 731 | 673 | 294 |

## Maintainer decision vs independent expectation

Denominator n = built pairs that mode was run on in that configuration.

| Config | Mode | n | correct | wrong | advisory | no signal | unsupported | error | timeout | controls | FP on controls | advisory on controls |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dwarf+headers | ABI Dumper + ABICC | 366 | 181 | 22 | 34 | 128 | 0 | 1 | 0 | 19 | 0 | 0 |
| dwarf+headers | ABICC (XML descriptors) | 366 | 187 | 23 | 39 | 115 | 0 | 2 | 0 | 19 | 0 | 0 |
| dwarf+headers | abicheck compare | 366 | 363 | 0 | 0 | 2 | 0 | 1 | 0 | 19 | 0 | 0 |
| dwarf+headers | libabigail abidiff | 366 | 126 | 12 | 93 | 106 | 29 | 0 | 0 | 19 | 0 | 0 |
| stripped+headers | ABI Dumper + ABICC | 365 | 0 | 0 | 0 | 0 | 365 | 0 | 0 | 19 | 0 | 0 |
| stripped+headers | ABICC (XML descriptors) | 365 | 186 | 23 | 37 | 117 | 0 | 2 | 0 | 19 | 0 | 0 |
| stripped+headers | abicheck compare | 365 | 351 | 1 | 0 | 13 | 0 | 0 | 0 | 19 | 0 | 0 |
| stripped+headers | libabigail abidiff | 365 | 110 | 8 | 10 | 208 | 29 | 0 | 0 | 19 | 0 | 0 |

## Explanation details (match / applicable; `unsup` = format cannot express)

| Config | Mode | entity | fields | size | offsets | functions |
|---|---|---|---|---|---|---|
| dwarf+headers | ABI Dumper + ABICC | 115/228 | 37/58 | 18/20 | unsup | 53/179 |
| dwarf+headers | ABICC (XML descriptors) | 119/228 | 40/58 | 18/20 | unsup | 53/179 |
| dwarf+headers | abicheck compare | 148/228 | 44/58 | 20/20 | 11/13 | 78/179 |
| dwarf+headers | libabigail abidiff | 142/228 | 34/58 | 19/20 | 11/13 | 122/179 |
| stripped+headers | ABI Dumper + ABICC | unsup | unsup | unsup | unsup | unsup |
| stripped+headers | ABICC (XML descriptors) | 119/228 | 40/58 | 18/20 | unsup | 53/179 |
| stripped+headers | abicheck compare | 148/228 | 44/58 | 20/20 | 11/13 | 77/179 |
| stripped+headers | libabigail abidiff | 15/228 | 0/58 | 0/20 | 0/13 | 12/179 |

## Pair-by-pair agreement between modes (equal totals are not equal answers)

| Config | Mode A | Mode B | pairs | same decision | same outcome | same details | both correct |
|---|---|---|---|---|---|---|---|
| dwarf+headers | ABI Dumper + ABICC | ABICC (XML descriptors) | 366 | 327 | 331 | 356 | 173 |
| dwarf+headers | ABI Dumper + ABICC | abicheck compare | 366 | 168 | 183 | 292 | 181 |
| dwarf+headers | ABI Dumper + ABICC | libabigail abidiff | 366 | 235 | 221 | 247 | 105 |
| dwarf+headers | ABICC (XML descriptors) | abicheck compare | 366 | 173 | 189 | 295 | 187 |
| dwarf+headers | ABICC (XML descriptors) | libabigail abidiff | 366 | 229 | 223 | 242 | 107 |
| dwarf+headers | abicheck compare | libabigail abidiff | 366 | 110 | 128 | 242 | 126 |
| stripped+headers | ABI Dumper + ABICC | ABICC (XML descriptors) | 365 | 2 | 0 | 137 | 0 |
| stripped+headers | ABI Dumper + ABICC | abicheck compare | 365 | 0 | 0 | 137 | 0 |
| stripped+headers | ABI Dumper + ABICC | libabigail abidiff | 365 | 0 | 29 | 137 | 0 |
| stripped+headers | ABICC (XML descriptors) | abicheck compare | 365 | 183 | 198 | 295 | 185 |
| stripped+headers | ABICC (XML descriptors) | libabigail abidiff | 365 | 200 | 196 | 258 | 95 |
| stripped+headers | abicheck compare | libabigail abidiff | 365 | 108 | 124 | 230 | 110 |

Environment: commit 273a526278a327da35f8daaae9cc1f7d22ae4f90, python 3.13.18, platform Linux x86_64, gcc 13.3.0, abidiff abidiff: 2.4.0, abi-dumper 1.2, abi-compliance-checker 2.3

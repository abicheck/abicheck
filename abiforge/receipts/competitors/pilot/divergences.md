# Divergences from the independent expectation

One entry per (pair, mode) whose decision outcome is not `correct`, or whose
explanation missed an expected detail. Excerpts are the first lines of the raw report.

## `c_core.struct_append_field@dwarf+headers` · ABI Dumper + ABICC
- expected: BREAKING..BREAKING (abi_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: ADVISORY → **advisory**; sides {'binary': 'ADVISORY', 'source': 'ADVISORY'}
```text
binary/low: rec Added_Field_And_Size extra
binary/low: rec DataType_Size 
source/low: rec Added_Field extra
```

## `c_core.struct_append_field@dwarf+headers` · ABICC (XML descriptors)
- expected: BREAKING..BREAKING (abi_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: ADVISORY → **advisory**; sides {'binary': 'ADVISORY', 'source': 'ADVISORY'}
```text
binary/low: rec Added_Field_And_Size extra
binary/low: rec DataType_Size 
source/low: rec Added_Field extra
```

## `c_core.struct_append_field@dwarf+headers` · libabigail abidiff
- expected: BREAKING..BREAKING (abi_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: ADVISORY → **advisory**; exit bits ['abi_change']
```text
Functions changes summary: 0 Removed, 2 Changed, 0 Added functions
Variables changes summary: 0 Removed, 0 Changed, 0 Added variable
2 functions with some indirect sub-type change:
  [C] 'function void fill_rec(rec*, int)' at edit_seed.c:10:1 has some indirect sub-type changes:
    parameter 1 of type 'rec*' has sub-type changes:
      in pointed to type 'struct rec' at edit_seed.h:5:1:
        type size changed from 192 to 256 (in bits)
        1 data member insertion:
```

## `c_core.struct_append_field@stripped+headers` · ABI Dumper + ABICC
- expected: BREAKING..BREAKING (abi_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_core.struct_append_field@stripped+headers` · ABICC (XML descriptors)
- expected: BREAKING..BREAKING (abi_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: ADVISORY → **advisory**; sides {'binary': 'ADVISORY', 'source': 'ADVISORY'}
```text
binary/low: rec Added_Field_And_Size extra
binary/low: rec DataType_Size 
source/low: rec Added_Field extra
```

## `c_core.struct_append_field@stripped+headers` · libabigail abidiff
- expected: BREAKING..BREAKING (abi_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; exit bits []
- missed details: ['entity', 'fields', 'functions', 'size'] (expected {'entity': ['rec'], 'fields': ['extra'], 'functions': ['fill_rec', 'make_rec'], 'size': [24, 32]})

## `c_core.struct_remove_trailing_field@dwarf+headers` · libabigail abidiff
- expected: BREAKING..BREAKING (abi_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: ADVISORY → **advisory**; exit bits ['abi_change']
```text
Functions changes summary: 0 Removed, 2 Changed, 0 Added functions
Variables changes summary: 0 Removed, 0 Changed, 0 Added variable
2 functions with some indirect sub-type change:
  [C] 'function void fill_rec(rec*, int)' at edit_seed.c:10:1 has some indirect sub-type changes:
    parameter 1 of type 'rec*' has sub-type changes:
      in pointed to type 'struct rec' at edit_seed.h:5:1:
        type size changed from 192 to 128 (in bits)
        1 data member deletion:
```

## `c_core.struct_remove_trailing_field@stripped+headers` · ABI Dumper + ABICC
- expected: BREAKING..BREAKING (abi_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_core.struct_remove_trailing_field@stripped+headers` · libabigail abidiff
- expected: BREAKING..BREAKING (abi_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; exit bits []
- missed details: ['entity', 'fields', 'functions', 'size'] (expected {'entity': ['rec'], 'fields': ['tag'], 'functions': ['fill_rec', 'make_rec'], 'size': [24, 16]})

## `c_core.struct_swap_fields@dwarf+headers` · libabigail abidiff
- expected: BREAKING..BREAKING (abi_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: ADVISORY → **advisory**; exit bits ['abi_change']
```text
Functions changes summary: 0 Removed, 1 Changed, 0 Added function
Variables changes summary: 0 Removed, 0 Changed, 0 Added variable
1 function with some indirect sub-type change:
  [C] 'function double scale(point, double)' at edit_seed.c:5:1 has some indirect sub-type changes:
    parameter 1 of type 'struct point' has sub-type changes:
      type size hasn't changed
      2 data member changes:
        'int x' offset changed from 0 to 32 (in bits) (by +32 bits)
```

## `c_core.struct_swap_fields@stripped+headers` · ABI Dumper + ABICC
- expected: BREAKING..BREAKING (abi_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_core.struct_swap_fields@stripped+headers` · libabigail abidiff
- expected: BREAKING..BREAKING (abi_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; exit bits []
- missed details: ['entity', 'functions', 'offsets'] (expected {'entity': ['point'], 'functions': ['scale'], 'offsets': ['x', 'y']})

## `c_core.typedef_retarget@dwarf+headers` · ABI Dumper + ABICC
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: ADVISORY → **advisory**; sides {'binary': 'ADVISORY', 'source': 'ADVISORY'}
```text
binary/low: handle_t DataType_Size 
binary/low: handle_t Typedef_BaseType 
source/low: handle_t Typedef_BaseType 
```

## `c_core.typedef_retarget@dwarf+headers` · ABICC (XML descriptors)
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: ADVISORY → **advisory**; sides {'binary': 'ADVISORY', 'source': 'ADVISORY'}
```text
binary/low: handle_t DataType_Size 
binary/low: handle_t Typedef_BaseType 
source/low: handle_t Typedef_BaseType 
```

## `c_core.typedef_retarget@dwarf+headers` · libabigail abidiff
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: ADVISORY → **advisory**; exit bits ['abi_change']
- missed details: ['entity'] (expected {'entity': ['handle_t']})
```text
Functions changes summary: 0 Removed, 1 Changed, 0 Added function
Variables changes summary: 0 Removed, 0 Changed, 0 Added variable
1 function with some indirect sub-type change:
  [C] 'function handle_t handle_of(int)' at edit_seed.c:9:1 has some indirect sub-type changes:
    return type changed:
      underlying type 'int' changed:
        type name changed from 'int' to 'long long int'
        type size changed from 32 to 64 (in bits)
```

## `c_core.typedef_retarget@stripped+headers` · ABI Dumper + ABICC
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_core.typedef_retarget@stripped+headers` · ABICC (XML descriptors)
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: ADVISORY → **advisory**; sides {'binary': 'ADVISORY', 'source': 'ADVISORY'}
```text
binary/low: handle_t DataType_Size 
binary/low: handle_t Typedef_BaseType 
source/low: handle_t Typedef_BaseType 
```

## `c_core.typedef_retarget@stripped+headers` · libabigail abidiff
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; exit bits []
- missed details: ['entity', 'functions'] (expected {'entity': ['handle_t'], 'functions': ['handle_of']})

## `c_elf.object_became_const@dwarf+headers` · libabigail abidiff
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: ADVISORY → **advisory**; exit bits ['abi_change']
```text
Functions changes summary: 0 Removed, 0 Changed, 0 Added function
Variables changes summary: 0 Removed, 1 Changed, 0 Added variable
1 Changed variable:
  [C] 'int g_obj' was changed to 'const int g_obj' at edit_seed.h:31:1:
    type of variable changed:
      entity changed from 'int' to 'const int'
      type size hasn't changed
```

## `c_elf.object_became_const@stripped+headers` · ABI Dumper + ABICC
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_elf.object_became_const@stripped+headers` · libabigail abidiff
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; exit bits []
- missed details: ['entity'] (expected {'entity': ['g_obj']})

## `c_elf.header_constant_changed@dwarf+headers` · ABI Dumper + ABICC
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; sides {'binary': 'NO_CHANGE', 'source': 'NO_CHANGE'}

## `c_elf.header_constant_changed@dwarf+headers` · ABICC (XML descriptors)
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: ADVISORY → **advisory**; sides {'binary': 'ADVISORY', 'source': 'COMPATIBLE'}
```text
binary/low: EDIT_LIMIT Global_Data_Value_Changed
source/safe: EDIT_LIMIT Global_Data_Value_Changed
```

## `c_elf.header_constant_changed@dwarf+headers` · libabigail abidiff
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; exit bits []

## `c_elf.header_constant_changed@stripped+headers` · ABI Dumper + ABICC
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_elf.header_constant_changed@stripped+headers` · ABICC (XML descriptors)
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: ADVISORY → **advisory**; sides {'binary': 'ADVISORY', 'source': 'COMPATIBLE'}
```text
binary/low: EDIT_LIMIT Global_Data_Value_Changed
source/safe: EDIT_LIMIT Global_Data_Value_Changed
```

## `c_elf.header_constant_changed@stripped+headers` · libabigail abidiff
- expected: API_BREAK..BREAKING (source_break; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; exit bits []

## `c_core.enum_append_member@dwarf+headers` · abicheck compare
- expected: COMPATIBLE..COMPATIBLE_WITH_RISK (compatible; operator language-rule expectation (EditOp.base + rules))
- tool decision: COMPATIBLE → **correct**
- missed details: ['functions'] (expected {'functions': ['color_code']})
```text
compatible: enum_member_added color::ALPHA: Enum member added: color::ALPHA
```

## `c_core.enum_append_member@dwarf+headers` · libabigail abidiff
- expected: COMPATIBLE..COMPATIBLE_WITH_RISK (compatible; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; exit bits []
- missed details: ['entity', 'functions'] (expected {'entity': ['color'], 'functions': ['color_code']})
```text
Functions changes summary: 0 Removed, 0 Changed (1 filtered out), 0 Added function
Variables changes summary: 0 Removed, 0 Changed, 0 Added variable
```

## `c_core.enum_append_member@stripped+headers` · ABI Dumper + ABICC
- expected: COMPATIBLE..COMPATIBLE_WITH_RISK (compatible; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_core.enum_append_member@stripped+headers` · abicheck compare
- expected: COMPATIBLE..COMPATIBLE_WITH_RISK (compatible; operator language-rule expectation (EditOp.base + rules))
- tool decision: COMPATIBLE → **correct**
- missed details: ['functions'] (expected {'functions': ['color_code']})
```text
compatible: enum_member_added color::ALPHA: Enum member added: color::ALPHA
```

## `c_core.enum_append_member@stripped+headers` · libabigail abidiff
- expected: COMPATIBLE..COMPATIBLE_WITH_RISK (compatible; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; exit bits []
- missed details: ['entity', 'functions'] (expected {'entity': ['color'], 'functions': ['color_code']})

## `c_core.func_add@stripped+headers` · ABI Dumper + ABICC
- expected: COMPATIBLE..COMPATIBLE (compatible; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_core.param_rename_header_only@stripped+headers` · ABI Dumper + ABICC
- expected: NO_CHANGE..COMPATIBLE_WITH_RISK (compatible; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_elf.soname_changed@dwarf+headers` · ABI Dumper + ABICC
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; sides {'binary': 'NO_CHANGE', 'source': 'NO_CHANGE'}

## `c_elf.soname_changed@dwarf+headers` · ABICC (XML descriptors)
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; sides {'binary': 'NO_CHANGE', 'source': 'NO_CHANGE'}

## `c_elf.soname_changed@stripped+headers` · ABI Dumper + ABICC
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_elf.soname_changed@stripped+headers` · ABICC (XML descriptors)
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; sides {'binary': 'NO_CHANGE', 'source': 'NO_CHANGE'}

## `c_elf.soname_dropped@dwarf+headers` · ABI Dumper + ABICC
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; sides {'binary': 'NO_CHANGE', 'source': 'NO_CHANGE'}

## `c_elf.soname_dropped@dwarf+headers` · ABICC (XML descriptors)
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; sides {'binary': 'NO_CHANGE', 'source': 'NO_CHANGE'}

## `c_elf.soname_dropped@stripped+headers` · ABI Dumper + ABICC
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_elf.soname_dropped@stripped+headers` · ABICC (XML descriptors)
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; sides {'binary': 'NO_CHANGE', 'source': 'NO_CHANGE'}

## `c_elf.symbolic_binding@dwarf+headers` · ABI Dumper + ABICC
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; sides {'binary': 'NO_CHANGE', 'source': 'NO_CHANGE'}

## `c_elf.symbolic_binding@dwarf+headers` · ABICC (XML descriptors)
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; sides {'binary': 'NO_CHANGE', 'source': 'NO_CHANGE'}

## `c_elf.symbolic_binding@dwarf+headers` · libabigail abidiff
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; exit bits []

## `c_elf.symbolic_binding@stripped+headers` · ABI Dumper + ABICC
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_elf.symbolic_binding@stripped+headers` · ABICC (XML descriptors)
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; sides {'binary': 'NO_CHANGE', 'source': 'NO_CHANGE'}

## `c_elf.symbolic_binding@stripped+headers` · libabigail abidiff
- expected: COMPATIBLE_WITH_RISK..BREAKING (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; exit bits []

## `c_core.comment_only@stripped+headers` · ABI Dumper + ABICC
- expected: NO_CHANGE..NO_CHANGE (unchanged; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_core.reorder_declarations@stripped+headers` · ABI Dumper + ABICC
- expected: NO_CHANGE..NO_CHANGE (unchanged; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_elf.glibc_floor_raised@dwarf+headers` · ABI Dumper + ABICC
- expected: COMPATIBLE_WITH_RISK..COMPATIBLE_WITH_RISK (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; sides {'binary': 'NO_CHANGE', 'source': 'NO_CHANGE'}

## `c_elf.glibc_floor_raised@dwarf+headers` · ABICC (XML descriptors)
- expected: COMPATIBLE_WITH_RISK..COMPATIBLE_WITH_RISK (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; sides {'binary': 'NO_CHANGE', 'source': 'NO_CHANGE'}

## `c_elf.glibc_floor_raised@dwarf+headers` · libabigail abidiff
- expected: COMPATIBLE_WITH_RISK..COMPATIBLE_WITH_RISK (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; exit bits []

## `c_elf.glibc_floor_raised@stripped+headers` · ABI Dumper + ABICC
- expected: COMPATIBLE_WITH_RISK..COMPATIBLE_WITH_RISK (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_elf.glibc_floor_raised@stripped+headers` · ABICC (XML descriptors)
- expected: COMPATIBLE_WITH_RISK..COMPATIBLE_WITH_RISK (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; sides {'binary': 'NO_CHANGE', 'source': 'NO_CHANGE'}

## `c_elf.glibc_floor_raised@stripped+headers` · libabigail abidiff
- expected: COMPATIBLE_WITH_RISK..COMPATIBLE_WITH_RISK (risk; operator language-rule expectation (EditOp.base + rules))
- tool decision: NO_CHANGE → **no_signal**; exit bits []

## `c_core.control_identity@stripped+headers` · ABI Dumper + ABICC
- expected: NO_CHANGE..NO_CHANGE (unchanged; identity rebuild)
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))

## `c_elf.control_identity@stripped+headers` · ABI Dumper + ABICC
- expected: NO_CHANGE..NO_CHANGE (unchanged; identity rebuild)
- tool decision: None → **unsupported**
- reason: abi-dumper needs DWARF; stripped library (exit 10: ERROR: can't find debug info in object(s))


#pragma once
/* ThirdPartyHandle v2: two fields, sizeof = 8 bytes — LAYOUT CHANGED */
typedef struct {
    int x;
    int y;  /* NEW: struct grew by 4 bytes */
} ThirdPartyHandle;

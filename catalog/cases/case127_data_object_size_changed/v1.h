#ifndef CONFIG_TABLE_H
#define CONFIG_TABLE_H

/* Number of configuration slots exported by the library (v1). */
#define CONFIG_SLOTS 16

/* Exported data object. Downstream executables that reference it get a
 * copy relocation sized for CONFIG_SLOTS at link time. */
extern int config_table[CONFIG_SLOTS];

int config_get(int index);

#endif /* CONFIG_TABLE_H */

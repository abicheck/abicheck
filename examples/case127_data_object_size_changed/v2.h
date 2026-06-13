#ifndef CONFIG_TABLE_H
#define CONFIG_TABLE_H

/* v2 doubles the number of configuration slots. The exported symbol
 * `config_table` grows from 64 to 128 bytes — its st_size changes. */
#define CONFIG_SLOTS 32

extern int config_table[CONFIG_SLOTS];

int config_get(int index);

#endif /* CONFIG_TABLE_H */

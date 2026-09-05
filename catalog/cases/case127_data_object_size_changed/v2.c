#include "v2.h"

int config_table[CONFIG_SLOTS] = {0};

int config_get(int index)
{
    if (index < 0 || index >= CONFIG_SLOTS)
        return -1;
    return config_table[index];
}

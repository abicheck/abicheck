#pragma once

/* counter_reset() was dropped here -- the break this workflow's GitHub
 * Actions step would catch automatically on the pull request that removed
 * it. */
int counter_increment(int value);

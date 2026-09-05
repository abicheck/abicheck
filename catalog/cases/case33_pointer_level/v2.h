/* case33 v2: Pointer level increased */
#ifndef V2_H
#define V2_H

#ifdef __cplusplus
extern "C" {
#endif

void process(int **data);     /* was int* → now int** */
int **get_buffer(void);       /* was int* → now int** */

#ifdef __cplusplus
}
#endif
#endif

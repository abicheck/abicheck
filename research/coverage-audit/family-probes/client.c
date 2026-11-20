#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
extern int entry(void);
int main(void){void *h=dlopen("./libtest.so", RTLD_NOW); if(!h)return 1;
int (*p)(void)=dlsym(h,"entry"), (*q)(void)=dlvsym(h,"entry","V1"); if(!p||!q)return 2;
printf("linked=%d default=%d explicit=%d\n", entry(),p(),q());}

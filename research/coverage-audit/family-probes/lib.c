int old(void){return 11;}
int newer(void){return 22;}
#ifdef FLIP
__asm__(".symver old,entry@V1");
__asm__(".symver newer,entry@@V2");
#else
__asm__(".symver old,entry@@V1");
__asm__(".symver newer,entry@V2");
#endif

#ifndef BINDING_H
#define BINDING_H

int compute(int x);

/* In v1 this is a weak symbol (see v1.c): a stronger definition in any other
 * object or library silently overrides it at link/load time. */
int helper(int x);

#endif /* BINDING_H */

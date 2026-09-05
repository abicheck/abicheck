/* DEMO: the v1 API surface keeps working — this case is a latent-UB
   (COMPATIBLE_WITH_RISK) finding, not a runtime break of existing code.
   The new v2 Exporter is polymorphic but has a non-virtual destructor;
   any future subclass handed out by make_exporter() will be destroyed
   through the base type, silently skipping the derived destructor. */
#include "v1.h"
#include <cstdio>

int main() {
    Renderer* r = make_renderer();
    r->draw(1);
    r->draw(2);
    std::printf("frames drawn = %d (expected 2)\n", r->frames_drawn);
    int broken = r->frames_drawn != 2;
    delete r; /* safe: Renderer's destructor is virtual in v1 and v2 */
    return broken;
}

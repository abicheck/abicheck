/* case34 v2: Access levels changed */
#ifndef V2_HPP
#define V2_HPP

class Widget {
public:
    void render();
    void internal_init();   // promoted from protected (widened access)

private:
    void helper();          // was public, now private (narrowed access)
    int cache;              // was public, now private (narrowed access)
};

#endif

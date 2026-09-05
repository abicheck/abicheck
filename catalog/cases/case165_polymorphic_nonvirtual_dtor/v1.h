#ifndef RENDER_H
#define RENDER_H

/* v1: the library's only polymorphic type has a virtual destructor —
   deleting the factory-returned object through its pointer is safe. */
class Renderer {
public:
    Renderer();
    virtual ~Renderer();
    virtual void draw(int frame);
    int frames_drawn;
};

/* Factory: the caller owns the returned object and deletes it. */
Renderer* make_renderer();

#endif

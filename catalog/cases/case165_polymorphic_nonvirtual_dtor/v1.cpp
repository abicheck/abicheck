#include "v1.h"

Renderer::Renderer() : frames_drawn(0) {}
Renderer::~Renderer() {}
void Renderer::draw(int) { ++frames_drawn; }

Renderer* make_renderer() { return new Renderer(); }

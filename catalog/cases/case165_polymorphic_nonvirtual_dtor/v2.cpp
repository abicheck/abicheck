#include "v2.h"

Renderer::Renderer() : frames_drawn(0) {}
Renderer::~Renderer() {}
void Renderer::draw(int) { ++frames_drawn; }

Renderer* make_renderer() { return new Renderer(); }

Exporter::Exporter() : bytes_written(0) {}
Exporter::~Exporter() {}
void Exporter::write(const char*) { bytes_written += 512; }

Exporter* make_exporter() { return new Exporter(); }

### Fixed

- **A function returning a Clang Blocks-extension block pointer
  (`int (^f(int))(int);`) no longer loses its returned block's own
  parameter list in `return_type`.** The spiral-declarator detection in
  the clang header backend's return-type resolver only recognized a
  pointer/reference (`*`/`&`/`&&`) or pointer-to-member (`<class>::*`)
  declarator prefix, missing the block-pointer sigil (`^`) clang uses for
  the identical declarator shape under `-fblocks` — falling through to the
  generic fallback and discarding the returned block's own parameter
  list, hiding a real return-type difference between two functions
  returning differently-shaped blocks.

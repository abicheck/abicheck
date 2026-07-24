### Fixed

- **The constrained-template-parameter detector now recognizes a default
  argument or a parameter pack** (`template <std::integral T = int>` /
  `template <std::integral... Ts>`), which the previous bare `\w+\s*[,>]`
  tail check missed since the concept name isn't directly followed by an
  identifier then `,`/`>` in either form.

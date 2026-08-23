### Fixed

- **`strip_anonymous_type_location`'s `:line:col` discriminator alone could
  still collide two genuinely distinct anonymous/lambda declarations.**
  Keeping only `:line:col` (dropping the checkout-dependent path entirely)
  fixed the same-header collision this helper was built for, but two
  DIFFERENT headers can each legitimately declare their own anonymous
  struct or lambda at the identical line and column — e.g.
  `guard<(lambda at /src/one.hpp:4:3)>` and
  `guard<(lambda at /src/two.hpp:4:3)>` both reduced to the same
  `guard<(lambda:4:3)>` identity, silently overwriting one entry in
  `diff_helpers.TypeMap` the way the original same-header bug did. Fixed
  by also keeping the declaring header's own basename (checkout-
  independent, unlike the full path) as part of the identity —
  `guard<(lambda:one.hpp:4:3)>` / `guard<(lambda:two.hpp:4:3)>`.

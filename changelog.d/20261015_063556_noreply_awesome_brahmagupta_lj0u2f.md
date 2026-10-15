### Fixed

- **Static archives are no longer routed to the Debian extractor** — `.a` and Rust `.rlib` files share the `!<arch>` magic with `.deb`, so they failed with "No data.tar.* found in Deb package" instead of the explicit static-archive rejection. A `.deb` is now recognised by its first member, `debian-binary`, in the Python detector and in the Action's release-operand check.
- **Function-typed template arguments no longer inflate template arity** — commas in a function type's parameter list and the `>` of its `->` return arrow were counted as template syntax, which raised a false `mandatory_template_param_added` on, for example, `std::function<void(int, int)>` or a Rust `Option<extern "C" fn(i32, i32) -> i32>` callback.

### Documentation

- **Rust libraries: declared minimal support** — new `docs/use/rust-libraries.md` page. Supported: a `cdylib` exporting a C ABI, compared with cbindgen headers (or DWARF with `debug = 2`). Not supported: the Rust-native API (not exported by a cdylib) and `.rlib`/`.a` inputs. Lists the verified verdicts and the caveats (debug-level sensitivity, crate-rename identity, callback signatures on the header path).

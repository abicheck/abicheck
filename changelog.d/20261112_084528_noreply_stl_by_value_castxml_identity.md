### Fixed

- **`public_api_exposes_stl_by_value` fires on castxml header dumps** — castxml spells a standard-library type in a signature by its bare leaf (`string`, `vector<int>`), and the check looked only for `std::` in the spelling. As a result, a public function newly returning `std::string` by value went unflagged with the castxml backend. The check now also reads the slot's resolved type identity. The check itself moved to `policy/std_by_value.py`.

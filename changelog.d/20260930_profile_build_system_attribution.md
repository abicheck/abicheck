### Added

- Run-plan cells now carry their profile's `build-output.json`
  `profile.build_system` as `build_system`/`build_generator`, and
  `check-project.yml` forwards them to `check-target`'s new
  `build-system`/`build-generator` inputs. The check-target report envelope
  records them as `profile_build_system: {name, generator}` (report schema
  5.10) in every mode, so each cell's findings name the build lane that
  produced them. Omitted when the profile declares no build system.

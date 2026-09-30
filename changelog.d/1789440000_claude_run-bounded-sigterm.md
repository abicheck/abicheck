### Fixed

- **Timed-out external tools now get a graceful SIGTERM** — `run_bounded` protected its spawn-then-register window by blocking SIGTERM with `pthread_sigmask`, and that mask was inherited by every child it started. The timeout path's SIGTERM therefore never reached castxml, clang, or a compiler driver: each timeout waited out the full 5-second grace period and then SIGKILLed the process group. The window is now protected by deferring abicheck's own SIGTERM cleanup handler instead, which children never see, so a timed-out tool terminates promptly on SIGTERM.

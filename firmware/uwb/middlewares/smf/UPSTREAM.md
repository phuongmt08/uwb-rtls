# SMF middleware provenance

This directory contains the state-transition core adapted from Zephyr's State
Machine Framework. It does not include or require the Zephyr kernel.

- Upstream: https://github.com/zephyrproject-rtos/zephyr
- Upstream release: `v4.4.0`
- Upstream release commit: `684c9e8`
- Original files:
  - `include/zephyr/smf.h`
  - `lib/smf/smf.c`
- License: Apache-2.0

Local adaptations:

- Replaced Zephyr kernel, utility, Kconfig, and logging dependencies with
  standard C headers and compile-time project configuration.
- Kept the public API naming close to Zephyr SMF for easier review and
  documentation reuse.
- Kept ancestor states and initial child transitions enabled.
- Made instrumentation optional through `SMF_INSTRUMENTATION`.
- Kept event transport, scheduling, timeouts, and locking outside the framework.

Do not update this code from Zephyr `main` implicitly. Any update must pin a
released tag and rerun the host semantics tests before firmware integration.

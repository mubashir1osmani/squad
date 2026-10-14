# Simulation status

Initial component verification: 2026-10-11. Latest integrated results: [verification](../docs/verification.md).

## Completed

- C++17 CMake project `quad-sim` with library `quadsim_core`.
- Deterministic fixture generation for commands, P5 sequences, manifests, and ground-truth flow.
- Scenarios: `nominal`, `translation_cardinal`, `low_texture`, `image_noise`, `dropped_frame`, `frozen_frames`, `command_stale_gap`, `corrupt_suite`.
- Each run writes `scenario_report.txt` with the seed, classification, shifts, noise amplitude, and texture id `hash_v1`.
- Non-empty output directories are refused unless `--overwrite` is set. Symlinks are refused.
- Flow evaluator with valid coverage, Euclidean displacement error, false-valid counts, and missing or unmatched timestamps.
- Zero comparable samples leave mean and max error as `unavailable`.
- Schema checks for malformed commands, flow, manifests, and truncated PGM.
- Host tests for determinism, exact integer translations and sign, timing chains, low texture, noise, dropped frames, frozen frames, stale gaps, corrupt inputs, and evaluator accounting.
- PX4 SITL setup notes in `docs/px4-sitl.md`, taken from the PX4 Guide (`main`) on 2026-10-11.

## Checks

Host: macOS, AppleClang 21.0.0.21000101, CMake, C++17. These are host tests. They are not HLS, FPGA, flight, or PX4 SITL runs.

| Check | Result |
| --- | --- |
| `cmake -S simulation -B simulation/build && cmake --build simulation/build` | Built `quad-sim` and `quadsim_tests` |
| `ctest --test-dir simulation/build --output-on-failure` | 1/1 passed (`quadsim_tests`, about 0.69 s) |
| `./simulation/build/quadsim_tests` | `passed=413 failed=0` |
| `generate --scenario translation_cardinal --seed 1` | `classification=valid_nominal` |
| `evaluate` of that `flow.csv` against itself | `comparable_samples=6`, `valid_coverage=1.000000`, `mean_displacement_error_px=0.000000`, `overall_success=1`, exit 0 |
| `check-schema --commands` on that command log | `ok`, exit 0 |
| `generate --scenario low_texture --seed 1` and `evaluate` against itself | `comparable_samples=0`, displacement `unavailable`, `rejection_success=1`, `overall_success=1`, exit 0 |

The low-texture report says overall success is agreement on invalid truth. It does not present a zero pixel error.

Demo files from those two commands are in `simulation/build/demo-cardinal` and `simulation/build/demo-flat`. `simulation/build` is gitignored.

## Demo commands

```sh
cmake -S simulation -B simulation/build
cmake --build simulation/build
ctest --test-dir simulation/build --output-on-failure
./simulation/build/quad-sim list-scenarios
./simulation/build/quad-sim generate --scenario nominal --seed 1 --output simulation/build/demo
./simulation/build/quad-sim evaluate \
  --truth simulation/build/demo/flow.csv \
  --estimate simulation/build/demo/flow.csv \
  --output simulation/build/demo/self-eval.txt
```

## Cross-component integration

Completed after the initial component checks; see [verification results](../docs/verification.md). The root check script reproduces the integrated run. Vision's current `kSearchRadius` is 8 px, the same bound as these fixtures. `nominal` stays inside ±3 px. `translation_cardinal` includes the ±8 px edge.

## Limitations

- Actual PX4 / Gazebo / SIH status: not run. No container was pulled and no PX4 tree was cloned.
- `quad-sim` has no flight-dynamics model and no kinematic vehicle model.
- Command axes are not converted into pixel motion.
- Image displacement is not position or velocity.
- Frozen-frame truth measures repeated image content. It does not prove a hover.
- `--overwrite` replaces this tool's filenames and leaves unrelated files in the directory.
- The linker printed `ld: warning: search path '/usr/local/opt/zstd/lib' not found` from the host environment. Both executables still linked and the tests passed.

## Follow-ups

- Address the noisy-motion search-edge limitation while retaining strict evaluator coverage and malformed-input checks.
- An operator can follow `docs/px4-sitl.md` on Ubuntu 24.04 or 22.04, or on a macOS machine after the official toolchain install. Record the PX4 revision and the exact command before calling that run a SITL result.
- Keep arming and takeoff as operator actions in the PX4 shell or QGroundControl.

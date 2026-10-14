# Verification record

Initial verification: 2026-10-11. The root check script reproduces the software checks and writes fresh integration evidence.

Repository preparation check: 2026-10-14, macOS arm64, AppleClang 21, CMake 3.31, Rust 1.98.1. `python3 scripts/check.py --gamepad` passed both C++ suites, Rust formatting and Clippy in both feature configurations, 43 default / 44 gamepad library tests, and 7 CLI tests per configuration. All seven integration scenarios reproduced the outcomes below, including the explicitly recorded noisy-motion failure. This is local evidence; GitHub Actions reports its own platform results.

## Result

The three desktop software components are implemented and their component tests pass. Cross-component integration works for the nominal fixtures, but the noisy-image scenario exposes a conservative search-boundary rejection limitation. This is a desktop prototype, not a flight-ready system.

## Independently rerun component checks

| Component | Command | Observed result |
|---|---|---|
| Ground station | `cargo test --manifest-path groundstation/Cargo.toml --features gamepad --quiet` | 44 library and 7 CLI integration tests passed |
| Vision | CMake build and CTest, reproduced by `scripts/check.py` | CTest 1/1 passed with ASan/UBSan enabled |
| Simulation | CMake build and CTest, reproduced by `scripts/check.py` | CTest 1/1 passed |

The vision test binary contains 312 checks and the simulation test binary contains 413 assertions/checks. These counts are not separate CTest cases. A pre-existing linker search-path warning for `/usr/local/opt/zstd/lib` did not prevent either build or test suite from passing.

## Cross-component checks

The integration check generated each scenario with `quad-sim`, ran the generated manifest through `quad-vision`, evaluated its output against independent generated truth, and replayed generated commands through `quad-groundstation`. Seed was 1 throughout.

| Scenario | Vision result | Evaluation |
|---|---|---|
| nominal | 5/5 valid pairs; zero displacement error | Pass |
| translation_cardinal | 6/6 valid pairs, including exact ±8 px shifts; zero displacement error | Pass |
| low_texture | All 3 pairs invalid, as required | Pass: correct rejection, not a zero-error motion measurement |
| image_noise | 2/6 valid pairs; four noisy ±8 px movements rejected | **Fail for full valid coverage**; the two accepted pairs had zero displacement error |
| dropped_frame | 4/4 valid pairs with proper time intervals; zero displacement error | Pass |
| frozen_frames | 4/4 valid image-displacement pairs; zero displacement error | Pass for image content only; does not detect a stuck camera or prove physical hovering |
| command_stale_gap | No image sequence | Replay passed and reported one 680,000 µs gap above the 250,000 µs timeout |

All seven generated command logs passed offline replay and subsequent schema validation. Offline replay does not demonstrate physical control or closed-loop flight.

## Reproduce the checks

From the repository root:

```sh
python3 scripts/check.py --gamepad
```

This runs the component builds/tests, Rust formatting and Clippy, and the seven scenarios above. Each invocation creates a new `build/integration/run-*` directory containing original fixtures, actual estimates, command replay, gap reports, evaluation reports, and `summary.json`. Outputs are ignored by Git; GitHub Actions uploads them as per-platform artifacts.

The `image_noise` evaluator still exits 3 and reports failure. The script accepts only its exact documented result (2 covered of 6 valid motions, 4 missed, no false-valid rows or timestamp mismatch, zero error on accepted rows), prints a visible warning, and records `vision=known_limitation`. Unexpected results fail the check. `--strict-noise` also returns failure for the known limitation. CI therefore checks for regressions against the current prototype, not full valid coverage across every scenario.

## Outstanding vision limitation

Both `vision/src/kernel.cpp` and `vision/src/reference.cpp` accept a best match on the ±8 pixel search edge only when the matching score is exactly zero. Noise makes that exact equality fail, so the four cardinal edge shifts in `image_noise` are conservatively marked invalid. Valid coverage is 0.333333. The evaluator correctly returns exit 3 and `overall_success=0`.

This was not hidden by changing fixtures, dropping failing rows, or weakening the evaluation. The source algorithm was not changed during this status check. Before camera integration, decide whether to expand the search with a guard region, improve edge confidence, or specify a narrower supported motion envelope; retain out-of-range and ambiguity rejection tests.

## Not verified

- Actual PX4/Gazebo/SIH operation or closed-loop simulation.
- Physical DualShock 4 stick signs and end-to-end gamepad behavior.
- Camera capture, calibration, gyro/range alignment, or conversion into PX4 optical-flow measurements.
- HLS synthesis, FPGA timing/resources, bitstream deployment, aircraft assembly, or flight.

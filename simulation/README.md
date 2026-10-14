# Simulation fixtures and flow evaluation

`quad-sim` is a C++17 tool that writes deterministic fixtures for the shared CSV and P5 contracts in `docs/interfaces.md`, and scores an estimated `flow.csv` against timestamp-aligned ground truth. It opens no sockets and sends no arm or takeoff commands.

Build outputs stay in `simulation/build`. The tool does not need the ground station or the vision package to build.

## Build and test

Requirements: CMake 3.16 or newer, and a C++17 compiler. No extra libraries.

```sh
cmake -S simulation -B simulation/build
cmake --build simulation/build
ctest --test-dir simulation/build --output-on-failure
```

The test binary prints `passed=` and `failed=` counts:

```sh
./simulation/build/quadsim_tests
```

## Commands

```text
quad-sim generate --scenario NAME --output DIR [--seed N] [--frames N] [--overwrite]
quad-sim evaluate --truth FLOW.csv --estimate FLOW.csv --output REPORT [--tolerance-px N]
quad-sim check-schema (--commands PATH | --flow PATH | --manifest PATH)
quad-sim list-scenarios
```

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Usage error or runtime error |
| 2 | Malformed input |
| 3 | Evaluation finished and `overall_success=0` |
| 4 | Output directory is not empty and `--overwrite` was omitted |

`generate` creates the output directory when it is missing. A non-empty directory is left unchanged unless `--overwrite` is set. With `--overwrite`, files this scenario writes are replaced and unrelated names in that directory stay in place. Symlinks and non-directories are refused.

`--frames` is accepted only by `nominal`, in the range 2 through 32. The default is 6 frames. Every other scenario has a fixed script.

`evaluate` always writes the report file. `--tolerance-px` defaults to 0. A comparable sample is a shared `t_us` where truth and estimate both have `valid=1`. When that count is 0, mean and max displacement are the token `unavailable`. A perfect rejection of an all-invalid truth file can still set `overall_success=1`, and the notes say that success is invalid-flow agreement.

`check-schema --commands` prints `ok` for a schema-valid log. A gap above 250000 µs adds a `warning:` line and still exits 0. That gap is the ground-station input timeout from `docs/interfaces.md`. The schema itself allows it.

## Fixture geometry

Images are 64×48 P5 PGM, maxval 255, with one comment line. The nominal interval is 20000 µs. The search window is ±8 px. `scenario_report.txt` records the seed, the shift list, the classification, and the noise amplitude.

Texture id `hash_v1` mixes the seed with integer coordinates and a 4-pixel checker. Noise, when enabled, is xorshift32 with amplitude 12. Seed 0 selects the xorshift state `0x6D2B79F5`. The same scenario and seed rewrite the same bytes.

Apparent displacement follows the shared contract. Positive `dx_px` is image right. Positive `dy_px` is image down. For an exact shift `(dx, dy)`, every in-bounds pixel satisfies `current(x, y) = previous(x - dx, y - dy)`. These numbers are pixels of image content. They are not metres, radians, or aircraft velocity. Stick axes in `commands.csv` are normalized values in [-1, 1]. Zero is centered. `enabled=0` is the operator deadman. This generator does not couple sticks to pixel motion with a dynamics model.

## Scenarios

| Scenario | Classification | What it writes |
| --- | --- | --- |
| `nominal` | `valid_nominal` | Textured frames and shifts inside ±3 px, plus a command log |
| `translation_cardinal` | `valid_nominal` | Exact shifts `(8,0)`, `(0,8)`, `(-8,0)`, `(0,-8)`, `(4,-3)`, `(0,0)` |
| `low_texture` | `valid_unmeasurable_flow` | Constant gray 128. Truth flow is `valid=0`, displacement 0, quality 0 |
| `image_noise` | `valid_degraded_pixels` | Cardinal geometry plus amplitude-12 noise. Truth flow stays exact |
| `dropped_frame` | `valid_incomplete_sequence` | Omits t=40000 µs. The spanning flow row sums the hidden shift and uses `dt_us=40000` |
| `frozen_frames` | `valid_physically_ambiguous` | Repeats a textured frame. Apparent displacement is 0 while `suppressed_shifts` records the scripted motion |
| `command_stale_gap` | `valid_operationally_stale` | Commands only. The gap at t=700000 µs is 680000 µs |
| `corrupt_suite` | `invalid_data` | Malformed command, flow, manifest, and PGM files, plus a valid header-only flow |

`low_texture` is schema-valid and unmeasurable. Treating it as high-quality zero flow would claim a confident stationary measurement. `frozen_frames` is schema-valid and physically ambiguous: the repeated pixels have a known image displacement of zero, which is also what a stuck camera would produce. `command_stale_gap` is schema-valid and operationally stale. `corrupt_suite` is invalid data: out-of-range axes, `nan` and `inf`, bad `enabled`, duplicate and reordered timestamps, CR newlines, quotes, a non-positive `dt_us`, a `dt_us` that breaks the timestamp chain, invalid rows with non-zero displacement or non-zero quality, absolute and `..` manifest paths, a truncated P5 raster, and an empty file.

## Evaluator

The report counts matched timestamps, valid coverage, false-valid rows (estimate `valid=1` on truth `valid=0`), missed valid rows, missing truth timestamps, and unmatched estimate timestamps. Displacement error is the Euclidean pixel error over comparable rows. `overall_success=1` requires a well-formed pair of files and either:

- at least one comparable row, zero false-valid and missed-valid rows, no missing or extra timestamps, and max error within `--tolerance-px`, or
- a non-empty truth file whose rows are all invalid, with the estimate matching those rows as invalid and adding none.

An empty file pair, a timestamp mismatch, or a schema error does not report a zero displacement error.

## Cross-component demo

Build all three components first, or run `python3 scripts/check.py` from the repository root to automate the tests and integration demo. The CLI interfaces are: `quad-vision --manifest PATH --output PATH`, and `quad-groundstation replay --input PATH --output PATH`.

`nominal` keeps every component of the shift inside ±3 px. `translation_cardinal` uses the full ±8 px window. Vision's host constants currently set the block-match radius to 8 px and reject a non-zero score that lands on that edge, so the ±8 rows are the boundary case.

```sh
cmake -S simulation -B simulation/build
cmake --build simulation/build
./simulation/build/quad-sim generate \
  --scenario nominal --seed 1 \
  --output simulation/build/cross

# After building the vision component
./vision/build/quad-vision \
  --manifest simulation/build/cross/manifest.csv \
  --output simulation/build/cross/flow-vision.csv

./simulation/build/quad-sim evaluate \
  --truth simulation/build/cross/flow.csv \
  --estimate simulation/build/cross/flow-vision.csv \
  --output simulation/build/cross/vision-eval.txt

# After building the ground-station component
./groundstation/target/debug/quad-groundstation replay \
  --input simulation/build/cross/commands.csv \
  --output simulation/build/cross/commands-replay.csv
```

For the unmeasurable case, repeat `generate` with `--scenario low_texture` and expect the vision estimate to stay `valid=0`. A valid zero-flow estimate on that truth is a false-valid result.

Cross-component checks have been run. See [verification results](../docs/verification.md), including the documented `image_noise` search-edge limitation.

## PX4

Synthetic fixtures, PX4 SIH, and Gazebo SITL are different things. The setup guide is [docs/px4-sitl.md](docs/px4-sitl.md). This package does not install or launch them.

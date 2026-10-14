# quad-vision

Host C++17 optical-flow prototype for the FPGA-assisted quadcopter. It reads a manifest of 8-bit binary PGM frames and writes `flow.csv` as specified in `docs/interfaces.md`.

`quad-vision` calls an allocation-free block-matching kernel. A separate reference implementation uses the same numeric rules and may allocate. Host compilation, host tests, and host timing are not HLS synthesis and are not FPGA measurements. No FPGA board or vendor toolchain has been selected.

Pixel results are image-plane displacements. They are not aircraft velocity, position, or a command.

## Build and test

Requirements: CMake 3.16 or newer, and a C++17 compiler. No third-party libraries. This tree was built with Apple clang 21.

```bash
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
./build/quad-vision-bench
```

Run those commands from `vision/`. AddressSanitizer and UndefinedBehaviorSanitizer are on by default. They make the bench slower. Turn them off for a plain host timing run:

```bash
cmake -S . -B build-nosan -DCMAKE_BUILD_TYPE=Release -DQUAD_VISION_SANITIZERS=OFF
cmake --build build-nosan
./build-nosan/quad-vision-bench
```

`quad-vision-bench` prints `measurement=host_timing` and `label=host timing`. The numbers are host CPU time only (`not_fpga=1`, `not_hls_synthesis=1`).

## Demo

`examples/translate_demo/` is a 64×64 sequence: a textured frame, the same texture shifted 3 pixels right, then a flat frame. The test binary rewrites it.

```bash
./build/quad-vision \
  --manifest examples/translate_demo/manifest.csv \
  --output /tmp/quad-vision-flow.csv
```

Stdout on success:

```text
pairs=2 valid=1 invalid=1
```

`/tmp/quad-vision-flow.csv`:

```csv
t_us,dt_us,dx_px,dy_px,quality,valid
20000,20000,3,0,255,1
50000,30000,0,0,0,0
```

The flat pair is invalid. It is not a high-quality claim that the scene was stationary.

This manifest is rejected because the names walk out of the manifest directory:

```bash
./build/quad-vision \
  --manifest examples/unsafe/manifest.csv \
  --output /tmp/quad-vision-unsafe.csv
```

The process exits 1 and does not write the output file.

## CLI

```text
quad-vision --manifest PATH --output PATH
quad-vision --help
```

Exit 0 on success or help. Exit 1 on any error. Errors go to stderr with the prefix `quad-vision:`. The CSV file is the contract. The `pairs=` line is a short summary.

The output is replaced only after every pair has been estimated. A failure leaves an existing output file untouched.

## CSV and image contract

Manifest:

```csv
t_us,filename
0,frame_000.pgm
20000,frame_001.pgm
```

- Header row is required.
- Timestamps are nonnegative integer microseconds from the start of one run.
- Timestamps must be strictly increasing. `dt_us` is the positive difference, and `t_us` on a flow row is the later frame.
- Names resolve relative to the manifest directory.
- Absolute paths, backslashes, drive-style colons, spaces, and any `..` segment are rejected. The check is lexical. Symlinks are not resolved.
- At least two frames and at most 128.
- One flow row is written per consecutive pair. The estimator is causal: pair *n* uses only frames *n-1* and *n*.

Images are P5 binary grayscale, `maxval=255`, one byte per pixel. Header comments and normal PGM whitespace are accepted, including a CRLF delimiter after `maxval`. The raster must be exactly `width * height` bytes. `P2`, `P6`, any other maxval, truncated rasters, trailing bytes, and dimensions outside 1..128 are rejected. Flow is computed only for frames whose width and height are both in 32..128. Every frame in one manifest must have the same dimensions.

Flow CSV uses LF, a period is not needed because every field is an integer, and there are no spaces or thousands separators:

```csv
t_us,dt_us,dx_px,dy_px,quality,valid
```

`dx_px` and `dy_px` are the apparent displacement of image content from the previous frame to the current frame. `+x` is image right. `+y` is image down. A feature that was in column 10 and is now in column 13 produces `dx_px = +3`.

`quality` is an integer in `[0, 255]`. It is an algorithm score, not a probability. `valid` is 0 or 1. Invalid rows are written as `dx_px=0`, `dy_px=0`, `quality=0`, `valid=0`.

## Numeric choices

The kernel and the reference use `include/quad_vision/constants.hpp`.

| Choice | Value |
|---|---|
| Block size | 16×16 |
| Search offsets | integers in [-8, +8] |
| Block grid | origins at 8, 24, 40, … while the block and the search margin stay inside the frame |
| Texture gate | mean absolute deviation from the block mean ≥ 8 gray levels |
| Match score | integer zero-mean sum of absolute differences |
| Ambiguity | a second minimum at Chebyshev distance ≥ 2 must be strictly worse, and `10 * second >= 11 * best` |
| Search edge | an edge offset votes only when its score is exactly 0 |
| Consensus | one unique vote, a strict majority of votes, and at least half of the placed blocks |
| Quality | `(255 * blocks_in_the_mode) / placed_blocks` when valid, else 0 |
| Tie break | the first minimum in scan order (offset y, then x, from -8 upward) |

Zero-mean matching ignores a single brightness offset added to the whole frame. It does not model shadows, vignetting, or exposure change that varies across the image.

There is no floating point in the kernel. Means stay exact because the score uses `area * pixel - block_sum` and never divides the pixels.

Consequences:

- A textured pair with no motion is valid, with `dx_px=0`, `dy_px=0`, and high quality. That is a real zero-displacement match.
- A flat or nearly flat pair does not pass the texture gate. The row is invalid. It must not be read as “confidently hovering.”
- A periodic pattern, or two image regions that move in opposite directions, is invalid when the blocks do not agree.
- Integer shifts from -8 to +8 are the measurable window. A perfect match on the boundary (±8) is kept. A shift of 9 or more is rejected instead of being clamped to 8.
- This is one global translation, not a dense flow field and not a rotation model.

## Memory

| Buffer | Bound |
|---|---|
| Frame width and height | 32..128 for flow, reader rejects above 128 |
| One frame | at most 128×128 = 16384 bytes |
| Frames kept while streaming | 2 |
| PGM file | at most 20480 bytes |
| Manifest | at most 256 KiB and 128 frames |
| Kernel score table | 17×17 `int64` values, about 2.3 KB, one block at a time |
| Kernel vote table | at most 64 fixed entries |

`estimate_kernel` does not call the heap, does not use standard containers, and does not use fractional types. The caller owns the two image planes. Parsing, the reference matcher, and the CLI do allocate. That allocation stays outside the processing core.

The kernel source is `src/kernel.cpp` and the public entry is `include/quad_vision/kernel.hpp`.

## Host timing from this machine

Apple clang 21, Release, arm64. Recorded 2026-10-11. These are host timings, not FPGA timings.

Without sanitizers, shift `(+4, -2)`:

| Frame | Iterations | Total | Per pair |
|---|---:|---:|---:|
| 64×64 | 100 | 21.715 ms | 217 µs |
| 128×128 | 20 | 23.503 ms | 1175 µs |

The default sanitized build is several times slower (about 3.5 ms and 19 ms per pair on the same two sizes). Use the unsanitized bench when quoting host speed. Neither number is a clock period, initiation interval, or device utilization.

## Before a flight controller can use this

`dx_px / dt_us` is still not a velocity.

- Camera intrinsics, including focal length in pixels, turn an angle into pixels.
- Gyro samples over the same exposure interval remove rotation. Translation and rotation both move pixels.
- A range or altitude measurement scales the remaining pixels into metres.
- The camera mounting rotation has to be known. This component’s `+x` is image right and `+y` is image down, which is not automatically the aircraft body frame.
- Camera, gyro, and range timestamps have to be synchronized. `t_us` here is only the manifest clock of the two frames.

Until those exist, keep this CSV in logs. Do not feed it to PX4 as optical flow, velocity, or position. Optical flow also does not provide drift-free position or obstacle avoidance.

## What is left before HLS

No device, clock, camera PHY, or toolchain is selected, so this repository does not claim a bitstream, LUT count, BRAM count, or timing closure.

The kernel is only a bounded starting point:

- Loops already have fixed trip counts (`±8`, 16×16 blocks, at most 7×7 blocks on a 128×128 frame).
- A later HLS port would add interface and pipeline directives, line buffers or a window buffer, and a streaming or AXI frame path.
- The full 17×17 search recomputes each window. A sliding-window SAD is the obvious throughput change once a clock and resolution are known.
- Compare that port to `estimate_kernel` and `estimate_reference` on the same fixtures. Host agreement is not co-simulation.
- Camera protocol, exposure, dropped frames, and ping-pong buffering are not in this milestone.

Changing a threshold requires the same edit in `src/kernel.cpp` and `src/reference.cpp`. The tests require those two functions to agree, and they require known integer shifts.

## Layout

```text
include/quad_vision/   public headers and numeric constants
src/kernel.cpp         allocation-free matcher (CLI path)
src/reference.cpp      allocating reference matcher
src/process.cpp        manifest, PGM, CSV
tests/test_main.cpp    parser, flow, and CLI checks
bench/bench.cpp        host timing
examples/              demo sequence and one rejected manifest
```

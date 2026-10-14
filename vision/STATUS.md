# Vision status

Date: 2026-10-11
Component: `vision/**`
Role: C++ optical flow and future FPGA kernel

Host C++ only. No FPGA board is selected. Nothing below is an HLS synthesis result, a bitstream, a camera capture, a PX4 run, or a flight test.

## Completed features

- CMake C++17 project. Executable name: `quad-vision`.
- CLI: `quad-vision --manifest PATH --output PATH`.
- P5 binary PGM reader and writer. `maxval` must be 255. Comments and a single header delimiter, including CRLF, are accepted. Truncation, trailing raster bytes, other magic numbers, and other maxvals are rejected.
- Manifest parser for `t_us,filename`. Strictly increasing nonnegative microsecond timestamps. Lexical rejection of absolute paths, `..`, backslashes, colons, and spaces. At least 2 and at most 128 frames.
- Consecutive causal pairs. One flow row each. `t_us` is the later frame and `dt_us` is the positive difference.
- Invalid estimates are stored as `dx_px=0,dy_px=0,quality=0,valid=0`.
- Global integer block matcher, 16×16 blocks, search [-8, +8].
- Texture gate at mean absolute deviation ≥ 8. Flat frames are invalid, not high-quality zero flow.
- Zero-mean SAD so a global brightness offset does not by itself move the match.
- Ambiguity rejection and search-edge rejection. Shifts outside ±8 are invalid rather than clamped.
- Disagreeing regions do not produce a confident average.
- Allocation-free kernel in `src/kernel.cpp`, used by the CLI. No heap, no standard containers, no fractional types in that core.
- Separate reference in `src/reference.cpp` that may allocate. Tests require the same `dx`, `dy`, `quality`, and `valid` on every fixture.
- Fixed memory ceiling: frames 32..128, two frames resident, kernel score table about 2.3 KB.
- Demo sequence `examples/translate_demo/` and rejected manifest `examples/unsafe/manifest.csv`.
- Host bench `quad-vision-bench`, labeled `host timing`, with `not_fpga=1` and `not_hls_synthesis=1`.

## Checks and outcomes

Compiler: Apple clang 21.0.0, arm64, `-Wall -Wextra -Wpedantic -Wshadow -Werror`, C++17, Release.

Default build (`vision/build`, AddressSanitizer and UndefinedBehaviorSanitizer on):

```text
ctest --test-dir vision/build --output-on-failure
1/1 Test #1: quad-vision-tests ................   Passed    3.05 sec
100% tests passed, 0 tests failed out of 1
```

Direct run of that test binary: `checks=312 failures=0`.

Second build (`vision/build-nosan`, sanitizers off):

```text
1/1 Test #1: quad-vision-tests ................   Passed    1.13 sec
100% tests passed, 0 tests failed out of 1
```

The 312 checks include:

- Positive, negative, and zero translations on both axes, including combined signs.
- Exact edge shifts ±8 and out-of-range shifts ±9, which stay invalid.
- Sizes 32×32, 64×64, 80×64, 96×80, and 128×128.
- Global brightness +30, deterministic noise of about ±2, and both together.
- Flat pairs, a single-pixel spike, a low-contrast gradient, a period-8 pattern, and left/right regions moving in opposite directions.
- Partial texture: eight of nine blocks agree, quality `(255 * 8) / 9` = 226, still valid.
- Padded strides. The estimator does not treat padding as pixels.
- Null pointers, stride shorter than width, and sizes outside 32..128.
- PGM magic, maxval, comments, CRLF, truncated raster, trailing bytes, huge claimed dimensions, and an oversized file.
- Manifest header, non-integer and overflowing timestamps, equal and decreasing times, absolute paths, parent traversal, and a missing image.
- Dimension mismatch and a well-formed 16×16 frame, which is rejected as too small to measure.
- CLI success, help, unknown arguments, and a malformed PGM. Failed runs do not create the output file.
- Source guard: `src/kernel.cpp` and `kernel.hpp` do not contain heap or container use. `src/reference.cpp` does use `std::vector`.

Demo command actually run:

```text
./vision/build/quad-vision \
  --manifest vision/examples/translate_demo/manifest.csv \
  --output /tmp/quad-vision-flow.csv
```

Stdout: `pairs=2 valid=1 invalid=1`

CSV:

```csv
t_us,dt_us,dx_px,dy_px,quality,valid
20000,20000,3,0,255,1
50000,30000,0,0,0,0
```

Unsafe manifest command exited 1 with `manifest: unsafe filename (parent traversal)`.

Host timing, sanitizers off, shift `(+4, -2)`, quality 255, valid 1. This is host CPU time on this Mac, not an FPGA result.

| Frame | Iterations | elapsed_ms | per_pair_us |
|---|---:|---:|---:|
| 64×64 | 100 | 21.715 | 217.149 |
| 128×128 | 20 | 23.503 | 1175.152 |

Same bench with AddressSanitizer and UndefinedBehaviorSanitizer: 3465 µs/pair at 64×64 (100 iterations, 346.530 ms) and 18971 µs/pair at 128×128 (20 iterations, 379.426 ms).

The linker printed `ld: warning: search path '/usr/local/opt/zstd/lib' not found` from the local toolchain search path. Both executables still linked, and the tests passed.

## Demo commands

From `vision/`:

```bash
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
./build/quad-vision-bench
./build/quad-vision \
  --manifest examples/translate_demo/manifest.csv \
  --output /tmp/quad-vision-flow.csv
```

Plain host timing:

```bash
cmake -S . -B build-nosan -DCMAKE_BUILD_TYPE=Release -DQUAD_VISION_SANITIZERS=OFF
cmake --build build-nosan
./build-nosan/quad-vision-bench
```

## Limitations

- No FPGA family, camera, power budget, or HLS tool has been selected.
- `src/kernel.cpp` has not been synthesized. Host object code is not a bitstream.
- Bench numbers are not clock frequency, initiation interval, LUT, DSP, or BRAM figures.
- No physical camera frames and no aircraft or simulator closed loop.
- One global translation. Not a dense flow field, not rotation, not subpixel.
- Measurable shifts are the integers -8..+8. Larger motion becomes an invalid row.
- Illumination support is a single global offset, plus small noise on the fixtures used here.
- Frames must be 32..128 pixels on each side, 8-bit gray, and paired at equal size.
- Path checks do not resolve symlinks.
- `dx_px` and `dy_px` still need intrinsics, gyro, range, mounting rotation, and synchronized timestamps before any flight-controller use. They are not velocity.

## Follow-ups

- Choose the FPGA board, camera interface, and toolchain together. Then schedule HLS and compare it to this kernel on the same fixtures.
- Add line buffers, a sliding-window score, and explicit stream or memory interfaces after the device and frame rate are known.
- Keep pixel displacement in logs until calibration and time alignment exist. Do not publish these rows as PX4 optical flow.
- If the numeric contract changes, update `constants.hpp`, both matchers, and the known-shift tests together.

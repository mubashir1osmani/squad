# IDSIA native flight recordings

Downloaded with user approval from the [Nano-Quadrotor System Identification Benchmark](https://github.com/idsia-robotics/nanodrone-sysid-benchmark).

The selected subset contains 13 native HDF5 flight recordings: square runs 1–5, chirp runs 1–4, and random runs 1–4. The files total **598,960,595 bytes** (598.96 MB / 571.21 MiB), excluding the small manifest.

- Local recordings: `data/idsia-native/source/` relative to this directory.
- Provenance and checksums: `data/idsia-native/source/download_manifest.json`.
- Pinned upstream Git tree: `f17f6b30aa656abdcc1765ea0a416ca9edee8977`.
- Each file was checked against its upstream byte size and Git blob SHA-1; SHA-256 is also recorded.

Dataset download scripts and CLI commands were removed at the user's request after the download was verified. The native recordings and their manifest remain. Source URLs, the pinned tree, upstream blob hashes and local SHA-256 checksums are retained in the manifest for reproducibility.

## First real-flight experiment: completed

These are flights from a **Crazyflie 2.1 Brushless**, not our quadcopter. All 13 native recordings have been imported with causal alignment. A fresh neural network was trained on five complete square recordings (11,163 valid windows); four chirp recordings were used for validation (22,627 windows). Four random recordings were reserved for the final test (15,415 windows). Entire manoeuvre families are separated, but all logs are from the same aircraft and date. Overlapping windows are correlated; they are not 15,415 independent flights.

The model predicts **the received body-rate estimate 50 ms ahead**, using eight past/current snapshots at nominal 100 Hz. Architecture: 56 inputs → 32 ReLU units → 3 residual outputs, **1,923 parameters** (7,692 bytes as float32 before normalization and file/interface overhead). Early stopping retained epoch 1 after 31 epochs ran. No synthetic samples or earlier weights were included.

### Measured result

Errors below are mean absolute error, in rad/s, averaged over all three axes and valid windows. Lower is better.

| Predictor | Chirp validation | Reserved random-flight test |
| --- | ---: | ---: |
| Persistence: keep the current rate | 0.102096 | **0.087377** |
| Ridge regression | 0.153793 | 0.152414 |
| Neural network | 0.113751 | 0.100827 |

**This first network does not beat persistence on overall MAE.** Its test MAE is about 15.4% higher. Persistence also has lower MAE in each of the four test recordings. Some axis/RMSE measures improve, but that does not change the failed overall-MAE result. The validation-based recommendation is to retain persistence as the baseline and keep this network experimental.

This demonstrates a reproducible real-data training/evaluation pipeline; it does not demonstrate a useful neural-network accuracy gain, an aircraft failure detector or autonomous navigation. The path planner is a separate component. No onboard or FPGA execution has been performed.

### Inspect the evidence

- [Experiment and split settings](configs/idsia-v1.json)
- Native-to-canonical data and per-run exclusions: `data/idsia-native/causal-v1/`, especially `import_audit.json` and `dataset.json`.
- Weights, normalization, code/data hashes, training history and validation metrics: `models/idsia-v1/`.
- Pre-test freeze record and final per-axis/per-flight scores: `results/idsia-v1/freeze.json` and `test.json`.
- Interactive plots: `results/idsia-v1/inspection/report.html` (standalone preview), or `inspection.html` in the conversation renderer. Select a test flight and axis. Predictions are plotted at their **target time**, 50 ms after their input anchor; there is no visual time shift to make them fit.
- Full-resolution measured/NN/ridge/persistence values: `results/idsia-v1/inspection/predictions.csv`.
- Streaming/offline parity and host latency: `results/idsia-v1/inspection/summary.json`.

All 15,415 scored NN predictions exactly matched the streaming `Predictor.push()` results in this run. Streaming can emit other predictions whose future measurement is unavailable/invalid; those are not silently scored. Host push latency was p50 23.3 µs, p95 24.9 µs and max 4,307.7 µs, excluding acquisition and hardware transfers. This is not an onboard deadline or FPGA measurement.

Plots retain a limited sample for responsiveness, gap boundaries, rate extrema and the largest NN error on each axis. Plot sampling never changes metrics or the full-resolution CSV. Missing/invalid spans remain visible as gaps.

## Timing, frame and actuator interpretation

The importer reads **numeric HDF5 blocks only**, skipping object blocks without reading or unpickling them. It verifies every native file against the saved SHA-256 and byte size. It contains no network/download code.

- Use the shared **ROS recorder receipt timestamp**, with integer nanosecond comparisons before microsecond conversion. Do not infer alignment from whole-flight cross-correlations or unreliable motor hardware stamps.
- Select only the most recent gyro/rate, motor and supervisor packet received at or before each 10 ms grid time. No centered interpolation, backward filling, zero-phase filtering or future alignment is used.
- Keep invalid grid rows so missing intervals cannot be joined into artificial contiguous windows. Gyro and motor source ages must be ≤15 ms; flying-status age must be ≤100 ms. Require the flying bit and exclude tumble/lock flags. Finite/range/status checks do not establish sensor health.
- `/cf/image_odom` supplies an **onboard angular-rate estimate**, not independently verified raw gyro ground truth. The canonical gyro column names are aliases. ROS body-rate units are rad/s; use the documented Crazyflie FLU convention and rotate to FRD via `diag(1,-1,-1)`. The exact logger mounting/version was not supplied, so its physical frame is not independently confirmed.
- Reorder logged commands M4, M1, M2, M3 to front-left, front-right, rear-right, rear-left. Divide `thrust.m*` by 65,535, matching the source extractor. These are normalized logged thrust commands, **not measured RPM, linear physical thrust or verified applied ESC duty**.

The forecast concerns the **observed receipt-clock process**. Network delays and onboard estimate latency can affect it. A 50 ms receipt-clock forecast is not yet a verified 50 ms physical acquisition-time forecast. The selected native subset does not include Melon, so this experiment does not reproduce the paper's benchmark split or reported scores.

Mapping evidence: [source extraction and status masks](https://github.com/idsia-robotics/nanodrone-sysid-benchmark/blob/f17f6b30aa656abdcc1765ea0a416ca9edee8977/utils/topic_utils.py), [source timing workflow](https://github.com/idsia-robotics/nanodrone-sysid-benchmark/blob/f17f6b30aa656abdcc1765ea0a416ca9edee8977/processing/bag_to_csv.py), [Bitcraze body convention](https://www.bitcraze.io/documentation/system/platform/cf2-coordinate-system/), and [Brushless motor layout](https://www.bitcraze.io/documentation/tutorials/getting-started-with-crazyflie-brushless/). The source timing workflow uses offline operations that this importer deliberately does not reproduce.

## Reproduce

From the project root, using Python 3.10+ and `requirements-neural.txt` (including h5py):

```sh
python -m unittest discover -s nn/tests -v
python -m nn import-idsia --source nn/data/idsia-native/source \
  --config nn/configs/idsia-v1.json --output nn/data/idsia-reproduction
python -m nn train --dataset nn/data/idsia-reproduction/dataset.json \
  --config nn/configs/idsia-v1.json --output nn/models/idsia-reproduction
# Freeze settings and model before exposing test scores.
python -m nn evaluate --dataset nn/data/idsia-reproduction/dataset.json \
  --model nn/models/idsia-reproduction --split test --acknowledge-test \
  --output nn/results/idsia-reproduction-test.json
python -m nn inspect --dataset nn/data/idsia-reproduction/dataset.json \
  --model nn/models/idsia-reproduction --evaluation nn/results/idsia-reproduction-test.json \
  --output nn/results/idsia-reproduction-inspection
```

Commands refuse to overwrite existing experiments. `inspect` exports the inline plot fragment, CSV and summary; the standalone preview is a wrapper generated for the first run. Development training does not parse held-out test signal values; it checks their manifest identity/checksums only. Its own loading test verifies that isolation. Final testing requires an explicit CLI acknowledgement.

## Next experiment

The random-flight test is now exposed and cannot remain an untouched test for future tuning. The early validation optimum and poor cross-family accuracy suggest investigating training coverage and timing before increasing network size; they do not by themselves identify the cause. Use more varied development manoeuvres, establish acquisition timestamps/applied-actuator semantics, and reserve genuinely new flights for the next final evaluation. Aircraft-specific data and held-out testing are required before transfer to our quadcopter. Additional dataset downloads require agreement on source and size.

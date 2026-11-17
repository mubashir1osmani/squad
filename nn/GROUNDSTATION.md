# Neural predictions in the Rust ground station

Run commands from `~/squad` with the repository's `.venv` activated. Install
`requirements-neural.txt` into that virtual environment if necessary. The
[shared prediction contract](../docs/predictions.md) defines the JSONL packets.
Neither the exporter nor the Rust prediction display sends aircraft commands.

## Reproduce the synthetic demonstration

```sh
python -m nn groundstation-demo --output nn/results/my-trajectory-demo
cargo run --manifest-path groundstation/Cargo.toml -- predictions \
  --input nn/results/my-trajectory-demo/predictions.jsonl \
  --capture nn/results/my-trajectory-demo/capture.jsonl \
  --dashboard nn/results/my-trajectory-demo/dashboard.html
```

The Python command actually trains and saves a Keras trajectory model with the
PyTorch backend, then reloads it to produce forecasts. It writes:

- `model/`: `model.keras`, scaling, metadata, validation scores and training history.
- `data/`: six deterministic fixture flights and their whole-flight split manifest.
- `input.csv` and `input.metadata.json`: independent synthetic route 99 and its provenance.
- `predictions.jsonl`: 600 anchor packets by default, including seven invalid history warmups.
- `config.json` and `demo.json`: settings, source label and result summary.

Existing output directories are never overwritten. `--samples` defaults to 600
and must be at least 100; `--epochs` defaults to 12. The four training flights
fit all scaling; validation selects an epoch. The reserved synthetic test flight
is not loaded or evaluated. The independent displayed route is not used for
training or model selection. The demo refuses a run that retained epoch zero.
The default run retained epoch 12 and produced 593 valid ten-horizon packets.
Those are model outputs, including learned corrections to constant velocity.

**Every demo packet is labelled `software_fixture`.** The generator is a simple
kinematic servo following a predeclared analytic mission schedule. It is not a
rigid-body quadcopter simulator, PX4 SITL, or evidence of real-flight trajectory
accuracy. Plans are computed from that predeclared schedule independently of
future measured states. The position/quaternion/rate signals are test inputs,
not a calibrated aircraft sensor model. Training a real trajectory model still
requires causally available plans and validated NED/FRD state logs.

The planned route is an input; the neural trajectory forecasts motion conditional
on that input. Neither system selects an obstacle-free route. Body-rate-only
models do not imply a position path.

## Export an existing Keras model

```sh
python -m nn groundstation-export \
  --model nn/models/keras-rates-v1 \
  --input nn/data/idsia-native/causal-v1/chirp_20251017_run1.csv \
  --source-kind real_flight --output nn/results/idsia-rates-groundstation.jsonl
```

This uses the existing saved IDSIA rate model without retraining or evaluating
its test recordings. Its output has null observed/predicted positions and
velocities and an empty planned route. It cannot show a flight path. Its prior
validation MAE did not beat persistence; see [FORECASTING.md](FORECASTING.md).

For trajectory output, supply the demo model and its source metadata:

```sh
python -m nn groundstation-export \
  --model nn/results/my-trajectory-demo/model \
  --input nn/results/my-trajectory-demo/input.csv \
  --metadata nn/results/my-trajectory-demo/input.metadata.json \
  --model-id software-fixture-trajectory-v1 \
  --source-kind software_fixture --output nn/results/my-second-export.jsonl
```

Each input row produces a packet at its original source-clock timestamp. Rates
use body FRD radians/second; positions, velocities and plans use NED metres and
metres/second. Predictions target `anchor + model offset`, preserving integer
microseconds even beyond float64's exact integer range. No receipt-time rewrite
or rate-to-position integration occurs. Nonfinite measurements, stale/future
sensor or plan timestamps, invalid flags, bad attitude quaternions and cadence
gaps clear the affected predictions. Malformed schemas and non-increasing input
timestamps reject the export. Invalid packets keep their anchor with a reason,
null observed position and empty predictions/plan. The model's training-range
flag is a warning, not a probability or confidence estimate.

## Source metadata and clocks

The default metadata path is `dataset.json` beside the input. The input must
match exactly one flight entry, its SHA-256 must match, and its `source_kind`
must agree with the explicit CLI argument. Frame, units, motor order, actuator
kind and any required gyro timestamps must agree with the model's training
contract. Trajectory logs must declare the exact trajectory contract and offsets
in [FORECASTING.md](FORECASTING.md).

For standalone CSVs, `--metadata` accepts an object with `schema_version: 1`,
`source_kind`, `input_sha256` and `contract`. `contract` uses the same object as
a dataset manifest. Describe the synchronized source clock and establish how
plan availability is known in its provenance. The export cannot establish the
truth of a caller's provenance; a timestamp alone cannot make a plan causal.
It never converts coordinate systems or invents calibration.

`--source-now-us N` optionally checks anchor freshness against a supplied current
clock in the **same source timebase**. Anchors in the future or older than the
model's source-age limit become invalid. Without this argument, export is an
offline replay and checks sensor/plan age relative to each anchor. It cannot
know that a file is old in wall time. These modes must not be confused.

## Pipe paced replay into live capture

```sh
python -m nn groundstation-export \
  --model nn/results/my-trajectory-demo/model \
  --input nn/results/my-trajectory-demo/input.csv \
  --metadata nn/results/my-trajectory-demo/input.metadata.json \
  --source-kind software_fixture --output - --pace | \
cargo run --manifest-path groundstation/Cargo.toml -- predictions \
  --input - --capture nn/results/live-capture.jsonl \
  --dashboard nn/results/live-dashboard.html \
  --serve 127.0.0.1:8080 --stale-ms 1000
```

`--output -` writes only JSONL to stdout; the summary goes to stderr. `--pace`
uses monotonic elapsed time and source timestamp differences; `--speed 2` plays
source time twice as fast. A paced file remains a replay of its labelled source,
not live aircraft data. Prediction generation currently reads and processes the
input CSV before emission; this is not a sensor acquisition driver. The Rust
receiver owns receipt-time staleness/disconnection handling and clears its
current path when the source stops. The optional listener is loopback-only.

## Verification

```sh
python -m unittest discover -s nn/tests -v
python3 scripts/check.py --neural
```

Tests exercise both Keras modes, metadata/checksums, the saved IDSIA rate-only
artifact, causal planned inputs, malformed/stale/future inputs, chronology,
integer precision, finite JSON, stdout/pacing, train-only scaling and an actually
trained/saved synthetic demo. Legacy neural pipeline tests remain present.
The saved IDSIA regression runs when its local weights and flight log are
available; fresh checkouts explicitly skip that one test. Both model modes and
the trained trajectory demo are still tested with generated fixtures.
No FPGA/HLS execution, PX4 connection, physical controller, aircraft sensor
acquisition or real-flight trajectory validation is implied.

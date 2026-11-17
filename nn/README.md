# Neural flight-response experiments

This component was consolidated from the local neural-network project into
`squad/nn`. Original source files and artifacts remain intact in their original
location. Python remains responsible for training and inference; the ground
station remains Rust. Copied datasets/models/results are local ignored artifacts,
not downloads or new real-flight experiments.

For a trained synthetic trajectory and the Rust dashboard, start with
[the prediction export guide](GROUNDSTATION.md). Current verification and
limitations are recorded in [STATUS.md](STATUS.md).

## Keras / PyTorch forecasting

The new [flight-forecasting pipeline](FORECASTING.md) uses Keras with the PyTorch
backend. It supports the existing **56-input → 3 future body-rate** model and a
route-conditioned model with separate future position, velocity and body-rate
sequences. The latter needs additional current-state and causally available
planned-route logs; the existing rate-only CSVs cannot train it. See the linked
commands, input/output shapes, data contract and verification status.

## Earlier NumPy experiments

Status: the first IDSIA real-flight model has been trained and evaluated, with full prediction exports and streaming parity checks. It does **not** beat persistence on overall validation/test MAE. See [the real-data experiment and evidence](IDSIA_DATASET.md). FPGA deployment has not been performed. The synthetic fixture experiment remains available for software checks.

A subsequent [whole-recording development comparison](IMPROVEMENTS.md) retrains on mixed manoeuvres and compares a revised residual NN with the original variant and stronger baselines. Revised pooled MAE is 14.2% below persistence, with most gains in square manoeuvres; random-flight MAE remains slightly worse. These are exposed-data development scores, requiring fresh flights for a final claim. No new datasets were downloaded.

## Run it

From the project root, use Python 3.10+ with NumPy and pandas; h5py reads native IDSIA numeric blocks. Dependency versions used for training are saved in its `model.json`.

```sh
cd ~/squad
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-neural.txt
python -m unittest discover -s nn/tests -v
```

Tests generate their own fixture data and models. One optional saved-IDSIA
regression is explicitly skipped when local real-flight data or trained weights
are absent. Those artifacts are not distributed with the source.

The original local build has `nn/data/software-fixture-v1`,
`nn/models/software-fixture-v1` and `nn/results/software-fixture-v1-*` artifacts.
If those artifacts are available, inspect or replay the model without retraining:

```sh
python -m nn audit --dataset nn/data/software-fixture-v1/dataset.json
python -m nn replay --model nn/models/software-fixture-v1 \
  --input nn/data/software-fixture-v1/fixture_10.csv \
  --output nn/results/my-replay.csv
```

For a separate reproducible experiment, use new paths; commands refuse to overwrite an existing dataset, experiment or report:

```sh
python -m nn fixture --output nn/data/fixture-demo
python -m nn train --dataset nn/data/fixture-demo/dataset.json \
  --config nn/configs/default.json --output nn/models/fixture-demo
```

Training uses training data and validation-based early stopping. It saves weights, train-only normalization, baselines, dataset checksums, code hashes, feature order, configuration, validation metrics and training history. It does not calculate final test scores. After freezing the model and settings, request the final test explicitly:

```sh
python -m nn evaluate --dataset nn/data/fixture-demo/dataset.json \
  --model nn/models/fixture-demo --split test --acknowledge-test \
  --output nn/results/fixture-demo-test.json
```

Once exposed, that test set must not guide model tuning. Use new held-out flights for a later model-selection cycle. Generated data/models/results are kept locally under `nn/` and ignored by Git.

## First task

Predict the aircraft's short-term body angular-rate response from recent onboard measurements and actuator history. Start with a small model trained and evaluated through Python, then deploy FPGA inference through a supported conversion and interface workflow. A bare-metal C inference implementation is optional.

This supports motion prediction and later investigation of abnormal response. It does not identify a landmark, navigate, detect obstacles or replace PX4's stabilizing controller.

## Contract to freeze before training

| Item | Proposed approach |
| --- | --- |
| Inputs | Recent timestamped body gyro samples and four correctly mapped motor outputs; additional signals only if causally available and demonstrably useful |
| Target | Body angular rates at a future timestamp, or their change from the current rate |
| Frame and units | Document the actual source; normalize explicitly to the selected body convention and rad/s |
| Cadence and horizon | Choose after inspecting log timing and the intended onboard use; do not inherit the old toy model's settings automatically |
| Actuators | Distinguish requested thrust, applied PWM, normalized outputs and measured RPM; record ordering, scaling, saturation and lag |
| Baselines | Persistence and a linear or simple physics model |
| Candidate network | Small MLP; choose width, history length and activation on validation data |
| Deployment | Train on computer; start with Python-based processor inference, then FPGA inference controlled from Python |

Python is the training/integration language. A Python model call may execute optimized native operations on the processor; it does not automatically execute on the FPGA. The FPGA route requires conversion to synthesizable hardware, a built bitstream and a verified data-transfer interface. hls4ml is a candidate; its generated HLS C++ is distinct from our Python application and does not require a hand-written C inference runtime.

## Dataset status and sequence

The approved **IDSIA subset** has been imported: five square recordings train the NN, four chirp recordings select the epoch, and four random recordings provide the frozen final test. Source files total 598.96 MB. See [results, assumptions and commands](IDSIA_DATASET.md).

A deterministic 12-run **software fixture** is available at `data/software-fixture-v1/dataset.json`: eight training runs, two validation runs and two final test runs. It is an analytic rotational-response fixture with actuator lag, damping and noise, used to verify code. It is not a full flight simulator and is not PX4-generated data.

- **Next collection source:** versioned PX4 simulation runs with synchronized gyro and applied actuator outputs, collected after the Phase 1 logging contract is established. Simulated data supports pipeline development, not a real-flight accuracy claim.
- **Public candidate:** [MIT Blackbird](https://github.com/mit-aera/Blackbird-Dataset), which contains real-flight IMU, motor PWM/RPM and motion-capture data. Audit timestamps, availability, body frames, motor order and actuator scaling before using any subset. Its aircraft dynamics may differ from ours; it is not a ready-made drop-in dataset.
- **Deployment evidence:** held-out logs from our actual aircraft, spanning intended manoeuvres and conditions. Use separate real-aircraft training/validation runs for adaptation and reserve untouched flights for final evaluation.

This is a new model trained from scratch. No old weights or accuracy numbers were reused. Blackbird download was attempted for one selected flight, but the hostname did not resolve. See `data/blackbird-download-attempt/download_report.json`. No Blackbird files were downloaded, imported or used for training.

Dataset download scripts and commands have been removed at the user's request. Retained manifests record source URLs and checksums. No Blackbird-specific column mapping is claimed verified.

Future motor commands are not valid input features unless the deployed system actually knows them at prediction time. Ground-truth future motion may be a training label; simulator-only or noncausally filtered signals must not silently become onboard inputs.

## Data and evaluation

1. Log complete simulator runs with configuration, timestamps, validity and provenance.
2. Split by flight/run and relevant condition before building time windows. Avoid overlapping or near-duplicate trajectories across splits.
3. Fit all normalization on the training split only.
4. Select the model using validation results, then freeze the pipeline and evaluate the reserved test runs.
5. Report per-axis MAE/RMSE, high-percentile error, results by condition, short rollout drift, inference cost and invalid-input handling. Record the units and prediction horizon with every result.
6. Add logs from the actual aircraft and repeat held-out evaluation before any real-flight accuracy claim. Public datasets can help, but their frames, actuators and aircraft dynamics must be reconciled.

The chosen model must improve meaningfully over useful baselines within a predefined application error budget. A simpler baseline remains acceptable if the neural network adds no demonstrated benefit.

Initially log predictions alongside measured motion. A large prediction residual can result from wind, sensor error or operating outside the training distribution; it does not by itself identify a failed motor.

See [the project phases](../PLAN.md) for mission integration and hardware gates.

## Initial model and measured fixture results

`configs/default.json` specifies four snapshots at nominal 100 Hz, producing 28 input features, a 24-unit ReLU hidden layer and three residual outputs. Add the predicted change to the current body rates to obtain the rates nominally 50 ms ahead. Feature windows use only current/past gyro and actuator values; future motor commands are not inputs.

These settings are an initial experiment, not established aircraft requirements. The model has **771 parameters**, or 3,084 bytes of float32 weights/biases before normalization, baselines, interfaces and other runtime overhead. Dataset size does not determine model storage size. Training runs on the computer; the FPGA does not store the training dataset.

The frozen first experiment retained epoch 68 using validation loss. Its separate final test evaluated 3,184 windows from two synthetic runs:

| Model | Mean absolute error, rad/s, averaged over axes |
| --- | ---: |
| Persistence | 0.026049 |
| Ridge regression | 0.005889 |
| Neural network | 0.004673 |

These are **software-fixture results only**. Full per-axis, per-flight and high-percentile errors are in `results/software-fixture-v1-test.json`. Do not interpret them as real-flight performance, failure-detection accuracy or a flight-release criterion. No rollout-accuracy study has been performed yet.

Offline Python replay produced 1,597 predictions from 1,600 snapshots; the first three samples warm up the history. Measured host `push()` latency was p50 20.7 microseconds, p95 68.0 microseconds and maximum 381.5 microseconds in that run. This excludes live acquisition and transfers and is neither an onboard timing guarantee nor an FPGA measurement.

## Bring in simulator or real-flight logs

See [DATA_CONTRACT.md](DATA_CONTRACT.md) for the canonical schema. The importer takes separately exported IMU and motor CSVs. It requires an explicit mapping and shared synchronized clock; it does not decode ULog/ROS bags or infer motor order/calibration.

Copy `configs/import-example.json` and edit it for the source's actual column names, timestamp/gyro units, sensor-to-body rotation and actuator bounds. The example describes an already canonical source, not a verified Blackbird or PX4 raw format. Run one import per flight:

```sh
python -m nn import-csv --imu /absolute/path/imu.csv \
  --motors /absolute/path/motors.csv --mapping /absolute/path/mapping.json \
  --flight-id flight01 --group-id session01 --split train \
  --source-kind px4_sitl --output nn/data/imported-flight01
```

Repeat for separate validation/test flight groups, then assemble them:

```sh
python -m nn assemble --runs nn/data/imported-flight01/run.json \
  nn/data/imported-flight02/run.json nn/data/imported-flight03/run.json \
  --output nn/data/flight-dataset
python -m nn audit --dataset nn/data/flight-dataset/dataset.json
```

Flights from the same recording/session or near-duplicate trajectory should share a `group_id` and stay in one split. Exact duplicate files, checksum mismatches and groups crossing splits are rejected. Choose cadence/horizon from actual timestamps in a new config; the importer does not silently resample a high-rate source to 100 Hz.

## Use the Python predictor

```python
from nn.inference import Predictor

predictor = Predictor("nn/models/software-fixture-v1")
result = predictor.push(snapshot)  # Dict following DATA_CONTRACT.md
if result["prediction_valid"]:
    log_prediction(result)       # Your application's observation/logging hook
```

`log_prediction` is application code, not a supplied live dashboard connection. Timing gaps, invalid/stale actuator inputs and out-of-order samples invalidate/reset history. A feature outside the training min/max range receives a warning flag, not a confidence probability. A live integration must also call `check_timeout(source_now_us)` during silence and stop using old outputs. Use the same clock as input timestamps.

This runtime sends no vehicle commands, reads no physical sensors and performs no automatic arming. hls4ml/vendor FPGA dependencies are intentionally not installed by the basic requirements file. The NumPy artifact is not a direct hls4ml input: a supported model reconstruction/export, bitstream, driver and board verification remain future work.

For IDSIA, the model contract additionally requires `gyro_timestamp_us` in every live snapshot. The trained `idsia-v1` NN remains experimental; its lower-complexity persistence baseline has better overall MAE. Inspect the measured comparison before considering deployment.

# Neural component status

Updated 2026-11-16. Observation and software verification only.

## Consolidated locally

The original neural project was copied from the user's Desktop project into
`squad/nn`, preserving Python modules, documentation, configurations, tests,
source/imported datasets, saved models and results. The original project was
left intact. `__pycache__`, `.DS_Store` and its virtual environment were excluded.
Its dependency list is now `requirements-neural.txt` in the repository root.
Datasets, models and generated results stay local and ignored by Git. The Rust
ground station remains a separate Rust component. No old C++ placeholders or
path-planner project were copied into the neural component.

A checksum dry-run comparison confirmed that the copied source datasets and
pre-existing saved models are identical to their originals. Historical reports
retain the evidence and limitations of the earlier experiments. Their absolute
paths may describe the original experiment location; they are not new runs.

## Implemented

- `python -m nn groundstation-export`: checked Keras artifact and source provenance,
  causal eight-snapshot rates/trajectory inference, finite JSONL, original integer
  source clocks, explicit invalid/warmup packets, train-range warning, stdout and
  optional source-clock pacing.
- Source manifest/standalone metadata checksum and source-kind validation; FRD
  rates and NED trajectory contracts; source-age and causal plan/state checks.
- Actual loaded Keras input/output tensor names/shapes and raw prediction shapes
  are checked before residual arithmetic. A single-step network cannot silently
  broadcast into multiple horizons despite a checksum-consistent sidecar.
- `python -m nn groundstation-demo`: deterministic causal route fixture,
  whole-flight split, train-only scaling, real training/save/reload, and a
  separately generated display route. No reserved test is evaluated.
- Documentation and commands: [GROUNDSTATION.md](GROUNDSTATION.md),
  [FORECASTING.md](FORECASTING.md), [README.md](README.md).

## Verified on this host

The standalone project environment at `squad/.venv` has Keras 3.15.1, PyTorch
2.15.0, NumPy 2.5.5, pandas 2.3.3 and h5py 3.16.0. No global packages were
installed for the work.

`python -m unittest discover -s nn/tests -v` passed all 42 tests in 15.184 seconds
in the root integration run. The ten focused exporter/demo tests also passed.
Tests include both forecast modes, real saved IDSIA rate inference,
causal future plans, malformed/nonfinite/stale/future inputs, exact timestamps
beyond float64's integer precision, output-shape rejection, stdout and pacing,
train-only scaling, and an actually trained/reloaded synthetic trajectory model.
Keras/PyTorch emitted NumPy deprecation warnings; no warning represented a
failed assertion or nonfinite packet.

Saved local demonstration `nn/results/groundstation-demo-v1` retained epoch 12.
Its 600 JSONL anchors comprise seven explicit invalid history warmups and 593
valid ten-horizon trajectory forecasts, all labelled `software_fixture`.
`model/model.keras` contains learned nonzero residual heads. Input plans come
from an analytic mission schedule declared before the fixture rollout; they do
not copy future measured positions.

The existing `nn/models/keras-rates-v1` was also exported without retraining:
`nn/results/idsia-rates-groundstation-v1.jsonl` contains 8,068 packets, of which
6,369 are valid rate forecasts. The first valid source anchor is row index 1279;
initial invalid source measurements remain invalid. Every packet has null
observed position, every prediction has null position/velocity, and planned paths
are empty. Its prior validation MAE remains worse than persistence; no new real
flight test scores were calculated.

The complete root `scripts/check.py --neural` passed, including the existing
C++/Rust checks and new fixture training/export/capture. Rust preserved all 600
packets, including invalid warmups. An actual Chromium live check also verified
paced export, stale/resume/EOF handling and byte-identical capture. Evidence and
limits are recorded in [the root verification report](../docs/verification.md).

The 2026-11-17 publication check reran the complete `--neural` flow successfully.
A separate source-only copy without ignored data/models/results passed 41 tests
and explicitly skipped only the saved-IDSIA regression, which requires local
real-flight artifacts. Fixture training and both model modes still ran. The
full local suite, including the saved-IDSIA regression, passed all 42 tests.

## Limits

The trajectory demo is a simple synthetic kinematic servo, not a calibrated
quadcopter simulator or evidence of real-flight trajectory accuracy. Its state,
attitude and rates are software inputs, not verified aircraft sensor outputs.
No real-data trajectory model has been trained, no new data was downloaded,
and no real test recording was used for model tuning or new evaluation.

The exporter reads a CSV before emission and can pace replay; it is not a live
sensor acquisition driver. `--source-now-us` requires a caller-supplied clock in
the same source timebase. The Rust receiver handles live receipt-time staleness
and disconnection. Metadata checks cannot establish the truth of a caller's
provenance or make a future-derived plan causal.

There is no route selection, autonomous control, aircraft connection, physical
controller validation, PX4 SITL run, flight validation, HLS synthesis, FPGA
execution or hardware timing claim in this neural work.

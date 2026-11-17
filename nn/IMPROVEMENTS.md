# IDSIA development comparison and dataset research

Updated 2026-11-11. No new datasets, pretrained weights, vendor tools or packages were downloaded for this work. Research retrieved only web documentation and bounded GitHub file-list metadata. Existing approved HDF5 files were read through the numeric-only adapter; bundled dataset code was not executed.

## What changed

The original frozen IDSIA experiment trained only on square manoeuvres. This comparison trains on square, chirp and random recordings, while keeping whole recordings separate for training, epoch selection and scoring. There are still only **13 distinct recordings from one aircraft/date**. Reusing more of those recordings for development does not create new real-flight data.

Four predetermined folds use `(run_number - 1) % 4`. Fold `k` is scored, fold `(k + 1) % 4` selects the epoch, and the other two train the model. Each role contains all three manoeuvre families. Every recording is scored exactly once by a model that did not train or early-stop on that recording. The training partitions contain **22,340–26,865 valid windows**, compared with 11,163 in v1. Overlapping windows within one recording stay together.

Both NN variants retain the 100 Hz causal grid, eight snapshots, 50 ms horizon, 56 → 32 → 3 architecture, 1,923 parameters, seed and training hyperparameters. The original variant is retrained on the same folds as the revised variant, making their comparison meaningful within this protocol.

The revised NN changes three settings together:

- One shared target scale preserves relative physical-unit errors across axes. Per-axis normalization had weighted low-variance axes more heavily in the loss.
- The residual output starts at zero, so its initial prediction is persistence. Epoch zero may be retained if training produces worse validation predictions.
- Epoch selection uses validation MAE, proportional to physical-unit MAE under the shared scale. Gradient updates still minimize squared error plus L2 regularization.

This is a comparison of the combined revision, not evidence of each setting's individual contribution. Input normalization and target scales use training windows only. There was no score-driven hyperparameter search in this experiment.

## Measured results

All **49,205** valid windows are scored once. MAE is averaged over the three rate axes and valid windows, in rad/s.

| Method | Pooled MAE | Equal-recording mean MAE |
| --- | ---: | ---: |
| Persistence | 0.133142 | 0.159539 |
| Past-rate trend / constant angular acceleration | 0.152019 | 0.176550 |
| Ridge regression | 0.132416 | 0.148209 |
| Original NN, retrained on these folds | 0.121230 | 0.136499 |
| Revised NN | **0.114229** | **0.128465** |

The revised NN has **14.2% lower pooled MAE than persistence** and **5.8% lower than the original variant retrained on identical folds**. It beats persistence on 9/13 recordings and in each of the four pooled scoring folds.

| Manoeuvre | Persistence MAE | Revised NN MAE | Interpretation |
| --- | ---: | ---: | --- |
| Square | 0.259269 | 0.180874 | Main source of the improvement |
| Chirp | 0.102096 | 0.099245 | Small pooled improvement; run 4 worsens |
| Random | 0.087377 | 0.087962 | Slightly worse overall; runs 1, 2 and 4 worsen |

**Do not compare 0.114229 directly to v1's final-test 0.100827:** the scoring populations differ. The table above compares methods on the same development windows. Random-manoeuvre performance still does not establish an advantage over persistence.

All recordings, including v1's test recordings, had already been examined before this work. These are **development cross-validation scores**, not a fresh untouched final test, confidence guarantee or flight-readiness result. Recorded windows are correlated; 49,205 windows are not 49,205 independent experiments. Fresh flight days/aircraft conditions and a predeclared error budget are needed for an accuracy claim relevant to our aircraft.

Both NN variants passed exact offline-versus-streaming comparison on all 49,205 scored windows each. That validates feature/replay consistency. It does not measure FPGA timing, physical acquisition-clock accuracy, autonomous flight or failure detection. No new flight-control integration was added. The original frozen real-data and fixture weights remain unchanged.

Verification: all 27 unit/integration tests passed, and a second complete training run reproduced every pooled score. All four chart payloads passed static validation; control changes, saved-state restoration and invalid-state handling passed local checks with a mocked chart runtime. Browser rendering was not checked for this comparison.

## Inspect and reproduce

The experiment lives under `results/idsia-development-v2/`:

- `protocol.json`: planned recording assignments and settings, written before fitting.
- `summary.json`: per-axis errors, per-recording errors, fold results, model hashes and streaming parity.
- `predictions.csv`: every prediction, measured target, recording, fold and anchor/target time.
- `fold-0/` … `fold-3/`: both NN variants' weights, normalization, metadata, validation results and training histories.
- `inspection/comparison.html`: interactive per-recording comparison. Chart values are checked against the full-resolution CSV before export.

There are four distinct fold models per variant. Use the model assigned to a recording's scoring fold to reproduce its comparison; do not pick the best fold after inspecting its scoring result. No single production candidate has been selected from this comparison.

From the project root, choose unused output directories; existing results are protected against overwrites:

```sh
.venv/bin/python -m unittest discover -s nn/tests -v
.venv/bin/python -m nn benchmark \
  --dataset nn/data/idsia-native/causal-v1/dataset.json \
  --config nn/configs/idsia-development-v2.json \
  --output nn/results/my-development-comparison
.venv/bin/python -m nn inspect-development \
  --results nn/results/my-development-comparison \
  --output nn/results/my-development-comparison/inspection
```

Existing `nn replay` can load each saved fold model. This remains computer inference; FPGA conversion, numerical equivalence after quantization and measured board latency remain separate work.

## What the current native files can add later

An audit aligned the native `/cf/motors` electrical-speed fields to the same receipt-time grid and checked rows with fresh, flying gyro/motor status. Across all 13 recordings, none of those eligible rows had zero electrical speed; a small number contained the invalid **65535** sentinel in at least one motor channel (4–23 eligible rows per recording). Most zeros in the original native files were outside the eligible flying portion.

These fields are **electrical speed**, not independently verified mechanical RPM. Upstream converts raw non-sentinel values by a factor of 100; mechanical speed requires the motor pole-pair count. Motor hardware timestamp fields contain repeats/zeros. The existing state hardware timestamp also contains duplicates. Neither has been substituted for the receipt clock or treated as synchronized physical time.

Motor speed and battery voltage are sensible next input candidates, but this comparison does **not** use them. They require explicit units, sentinel handling, past-only alignment, freshness rules, matching live inputs and a fair comparison on common scoring windows. Invalid samples must not be silently filled from future packets.

## Additional data candidates reviewed

Sizes below describe publisher metadata, not newly downloaded payloads. Familiar hosting and plain numeric formats reduce risk but are not a security certification.

| Source | Publisher and format | Advertised/listed size | Decision |
| --- | --- | --- | --- |
| [NanoBench](https://github.com/syediu/nanobench), linked by its [paper](https://arxiv.org/abs/2603.09908) | Authors Syed Izzat Ullah and Jose Baca; CSV with YAML metadata; repository explicitly licenses data and code under BSD-3-Clause | Main `datasets/dataset/` directory: **107 CSV files, 290,797,892 bytes**, plus 69,239 bytes of other metadata in the inspected Git tree | Useful candidate for a separate offline benchmark. Published signals are interpolated after whole-flight time alignment; original causal telemetry is not present in that directory. Do not treat these CSVs as direct proof of real-time prediction. No download. |
| [NeuroBEM university index](https://download.ifi.uzh.ch/rpg/NeuroBEM/) and [dataset documentation](https://rpg.ifi.uzh.ch/neuro_bem/Readme.html) | University of Zurich; raw Betaflight logs and ROS bags, or processed CSV | Index lists raw archive **434M** and processed archive **627M** (server's rounded units) | Raw logs are promising for a larger brushless aircraft. Licence, decoding path and clock mapping need checking first. Processed rates use filtering/fusion, so preprocessing must be audited. No download. |
| [Pelican](https://github.com/wavelab/pelican_dataset) | University of Waterloo WAVELab; MATLAB data, 54 flights | README states **238.1 MB** | Relevant motor measurements, but published body rates are derived from motion capture. Raw onboard timing/IMU availability and explicit dataset licence need checking. No download. |

NanoBench's README/paper describe 172 recordings. The inspected main dataset directory contained 107 CSVs; other benchmark directories also contain CSVs. Do not assume the main directory alone is the complete paper release or pool other files without checking duplicates and roles. Its brushed Crazyflie differs from the brushless IDSIA aircraft, so combining their windows as one undifferentiated aircraft is inappropriate.

Research reference: NanoBench Git tree `934a1ab92c458cad99c9278a5cb63bf68af6e56c`, untruncated GitHub file-list response inspected on 2026-11-11. This is a **tree hash**, not a commit identifier. No payload checksums are claimed because no new payloads were downloaded.

## Next evidence needed

1. Acquire original timestamped telemetry with gyro, actual motor outputs/speed and battery voltage, from an approved publisher or our own PX4 logs. Avoid future interpolation in model inputs.
2. Establish source clocks and actuator semantics before adding features. Preserve missing data and source-age validity.
3. Collect multiple manoeuvres, flight days, battery levels, payloads and conditions on our aircraft. Keep entire sessions and near-duplicate trajectories together.
4. Reserve untouched recordings before selection, evaluate against persistence/trend/linear baselines, and inspect high-percentile and per-condition errors. A lower mean alone is insufficient when random-flight predictions still regress.

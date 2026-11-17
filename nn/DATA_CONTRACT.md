# Flight-response log contract, version 1

Each CSV represents exactly one flight/run, in ascending source timestamp order. A companion `dataset.json` explicitly assigns whole run groups to train, validation or test, declares source provenance, and records each CSV's SHA-256. See `data/software-fixture-v1/dataset.json` for a generated example.

| Column | Meaning |
| --- | --- |
| `timestamp_us` | Nonnegative integer snapshot/decision timestamp, microseconds |
| `gyro_timestamp_us` (optional) | Source timestamp of the rate sample used; defaults to `timestamp_us` for direct synchronous logs |
| `motor_timestamp_us` | Timestamp of the actuator sample used in this snapshot, in the same clock |
| `gyro_x_rad_s`, `gyro_y_rad_s`, `gyro_z_rad_s` | Right-hand body rates, rad/s, in forward/right/down (FRD) axes |
| `motor_0` through `motor_3` | Actuator outputs normalized to [0,1], in the order front-left, front-right, rear-right, rear-left |
| `gyro_valid`, `motor_valid` | Integer 0/1 source validity; 0 excludes affected windows |

The dataset declares `actuator_kind` as `normalized_output` or `normalized_pwm`. Normalized PWM is not asserted to be linear thrust, and measured RPM is not interchangeable with a command. The mapping must establish which physical motor occupies each position. These logs represent applied actuator outputs, not a joystick or high-level velocity command.

## Timing and validity

- Convert the sensor frame into FRD explicitly. The importer requires a proper rotation matrix; reflections and arbitrary scale transforms are rejected.
- Align actuator samples backward/as-of to each IMU timestamp. Never use a future sample or centered interpolation in an input feature.
- For resampled gyro/rate estimates, retain `gyro_timestamp_us` and declare `requires_gyro_timestamp: true` in the dataset contract. Data loading and live inference then require it. Reject negative/future source ages and samples older than `max_gyro_age_us` (default 15,000 us), including future target/intervening rows during scoring. Existing synchronous datasets can omit the optional column.
- Dataset-specific provenance must distinguish sensor acquisition time from telemetry receipt time, raw gyro from an onboard rate estimate, and logged commands from verified applied actuator outputs. IDSIA uses recorder-receipt time and rate-estimate aliases in the canonical gyro columns; it does not establish sensor acquisition latency.
- The default experiment expects 10,000 microseconds between snapshots, with a 2,000 microsecond interval tolerance and a 2,500 microsecond tolerance on the complete 50,000 microsecond forecast horizon. Actuator age must be between 0 and 15,000 microseconds. Review these settings for actual sensors and application needs.
- Duplicate/reversed timestamps and malformed required columns/flags fail log validation. Invalid flags, nonfinite values, stale/future actuator timestamps and actuator values outside [0,1] exclude windows; they are not filled with zeros.
- All historical input samples must be usable. The intervening gyro samples and target gyro must be usable, and the full window must be contiguous. Future actuator samples are neither required nor used as input.
- Inputs flatten the historical rows from oldest to newest, with gyro x/y/z then motors 0/1/2/3 in each row. Exact feature names and timing settings are stored with the model.
- CSV import preserves integer timestamp precision, floors sub-microsecond fractions and rejects duplicates created by that conversion. It does not synchronize clocks, estimate offsets or resample data.

If a raw source has no validity flags, a mapping can explicitly use `null` for its validity column names. In that case the adapter checks finite/range conditions only; this does not establish sensor health. The provenance records that policy. Source flags should be retained whenever available.

## Learning and deployment

Targets are future body rate minus current body rate, in rad/s. Fit input and target normalization using training data only. Keep all runs sharing a group in one split, including derivatives of a recording or near-duplicate trajectories. No random sample-level split is supported.

Source kinds are `software_fixture`, `px4_sitl` and `real_flight`. Software fixtures cannot be mixed into an evaluation dataset with the other kinds. Source declarations are provenance supplied by the collector; software cannot prove that a CSV came from a real aircraft.

A model is valid only for its declared signal contract. The live caller must check that incoming data use the same frame, actuator meaning, units and motor ordering, supply fresh source timestamps, and call the silence watchdog independently. Offline replay cannot establish that live acquisition and inference meet deadlines. Freshness/range checks are not calibrated uncertainty estimates or an aircraft safety case.

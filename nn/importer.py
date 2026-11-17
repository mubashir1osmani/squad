from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import shutil

import numpy as np
import pandas as pd

from .data import GYRO, KINDS, MOTOR_ORDER, MOTORS, SPLITS, sha256
from .pipeline import write_json


def timestamps(values, unit):
    factors = {"s": Decimal(1_000_000), "us": Decimal(1), "ns": Decimal("0.001")}
    if unit not in factors:
        raise ValueError("Timestamp unit must be s, us or ns")
    converted = []
    try:
        for value in values:
            value = Decimal(str(value)) * factors[unit]
            if not value.is_finite() or value < 0 or value > np.iinfo(np.int64).max:
                raise ValueError("Invalid timestamp")
            converted.append(int(value))
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError("Invalid timestamp") from exc
    result = np.asarray(converted, dtype=np.int64)
    if len(result) == 0 or np.any(np.diff(result) <= 0):
        raise ValueError("Timestamps must increase strictly after conversion to microseconds")
    return result


def _flags(df, column):
    if column is None:
        return np.ones(len(df), dtype=bool)
    if not df[column].isin(["0", "1"]).all():
        raise ValueError(f"{column} must contain integer 0/1 validity flags")
    return df[column].to_numpy() == "1"


def import_csv(imu_path, motors_path, mapping_path, output, flight_id, group_id, split, source_kind):
    """Import one flight; no automatic unit/frame/clock guesses."""
    if source_kind not in ("px4_sitl", "real_flight") or split not in SPLITS:
        raise ValueError("Declare simulation/real source kind and explicit split")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", flight_id) or not group_id:
        raise ValueError("Invalid flight or group id")
    output = Path(output)
    if output.exists():
        raise ValueError(f"Refusing to overwrite imported run: {output}")
    mapping = json.loads(Path(mapping_path).read_text())
    if mapping.get("schema_version") != 1 or mapping.get("timebase") != "shared":
        raise ValueError("Mapping must explicitly declare a shared, synchronized timebase")
    if mapping.get("validity_policy") != "finite_range_and_optional_source_flags":
        raise ValueError("Explicit validity policy required; finite data alone does not establish sensor health")
    if mapping["actuator_kind"] not in ("normalized_pwm", "normalized_output"):
        raise ValueError("Only normalized PWM/output is supported; RPM is not a drop-in motor command")
    rotation = np.asarray(mapping["imu_to_frd"], float)
    if (rotation.shape != (3, 3) or not np.isfinite(rotation).all()
            or not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(rotation), 1, atol=1e-6)):
        raise ValueError("imu_to_frd must be a proper 3x3 rotation")
    if mapping["gyro_units"] not in ("rad/s", "deg/s"):
        raise ValueError("Declare gyro units as rad/s or deg/s")
    if len(mapping["gyro_columns"]) != 3 or len(set(mapping["gyro_columns"])) != 3:
        raise ValueError("Need three distinct gyro columns")
    channels = mapping["motor_channels"]
    if len(channels) != 4 or [c["position"] for c in channels] != MOTOR_ORDER:
        raise ValueError("Motor channels must explicitly follow the canonical position order")
    if len({c["column"] for c in channels}) != 4:
        raise ValueError("Motor columns must be distinct")
    minimum = np.asarray([c["minimum"] for c in channels], float)
    maximum = np.asarray([c["maximum"] for c in channels], float)
    if not np.isfinite([minimum, maximum]).all() or np.any(maximum <= minimum):
        raise ValueError("Each motor needs finite, increasing calibration bounds")
    # Read timestamps as strings so nanosecond epoch values retain precision.
    imu = pd.read_csv(imu_path, dtype=str)
    motors = pd.read_csv(motors_path, dtype=str)
    it = timestamps(imu[mapping["imu_timestamp_column"]], mapping["imu_time_unit"])
    mt = timestamps(motors[mapping["motor_timestamp_column"]], mapping["motor_time_unit"])
    gyro = imu[mapping["gyro_columns"]].astype(float).to_numpy()
    if mapping["gyro_units"] == "deg/s":
        gyro = np.deg2rad(gyro)
    gyro = gyro @ rotation.T
    raw = motors[[c["column"] for c in channels]].astype(float).to_numpy()
    normalized = (raw - minimum) / (maximum - minimum)
    # Backward/as-of alignment only: future actuator samples never become features.
    indices = np.searchsorted(mt, it, side="right") - 1
    safe_indices = np.maximum(indices, 0)
    aligned = normalized[safe_indices].copy()
    aligned[indices < 0] = np.nan
    motor_t = mt[safe_indices].copy()
    motor_t[indices < 0] = 0
    imu_valid = _flags(imu, mapping.get("gyro_valid_column")) & np.isfinite(gyro).all(axis=1)
    motor_valid = (_flags(motors, mapping.get("motor_valid_column"))[safe_indices]
                   & (indices >= 0) & np.isfinite(aligned).all(axis=1)
                   & ((aligned >= 0) & (aligned <= 1)).all(axis=1))
    df = pd.DataFrame({"timestamp_us": it, "motor_timestamp_us": motor_t,
                       **{name: gyro[:, i] for i, name in enumerate(GYRO)},
                       **{name: aligned[:, i] for i, name in enumerate(MOTORS)},
                       "gyro_valid": imu_valid.astype(int), "motor_valid": motor_valid.astype(int)})
    output.mkdir(parents=True)
    df.to_csv(output / "flight.csv", index=False, float_format="%.12g")
    report = {"schema_version": 1,
              "contract": {"body_frame": "FRD", "gyro_units": "rad/s", "motor_order": MOTOR_ORDER,
                           "actuator_kind": mapping["actuator_kind"]},
              "dataset_entry": {"id": flight_id, "group_id": group_id, "split": split,
                                "path": "flight.csv", "source_kind": source_kind,
                                "sha256": sha256(output / "flight.csv")},
              "provenance": {"imu_sha256": sha256(imu_path), "motors_sha256": sha256(motors_path),
                             "mapping_sha256": sha256(mapping_path), "mapping": mapping},
              "rows": len(df), "rows_before_first_motor_sample": int((indices < 0).sum()),
              "alignment": "latest motor sample at or before each IMU sample; no interpolation",
              "warning": "Age limits are applied by window preparation/inference. No automatic resampling; review cadence against model config."}
    write_json(output / "run.json", report)
    return report


def assemble(run_paths, output):
    """Combine imported runs without resplitting samples or overwriting raw data."""
    output = Path(output)
    if output.exists():
        raise ValueError(f"Refusing to overwrite assembled dataset: {output}")
    runs, ids, digests, groups = [], set(), set(), {}
    contract = None
    for path in map(Path, run_paths):
        report = json.loads(path.read_text())
        if report.get("schema_version") != 1:
            raise ValueError("Unsupported run schema")
        entry = report["dataset_entry"].copy()
        source = (path.parent / entry["path"]).resolve()
        if not source.is_relative_to(path.parent.resolve()) or sha256(source) != entry["sha256"]:
            raise ValueError("Imported run path/checksum mismatch")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", entry["id"]):
            raise ValueError("Invalid run id")
        if entry["id"] in ids or entry["sha256"] in digests:
            raise ValueError("Duplicate imported run")
        if entry["split"] not in SPLITS or entry["source_kind"] not in KINDS:
            raise ValueError("Invalid split/source kind")
        group = entry["group_id"]
        if not isinstance(group, str) or not group:
            raise ValueError("Missing group id")
        if group in groups and groups[group] != entry["split"]:
            raise ValueError("A flight group cannot cross splits")
        groups[group] = entry["split"]
        ids.add(entry["id"])
        digests.add(entry["sha256"])
        if contract is None:
            contract = report["contract"]
        if contract != report["contract"]:
            raise ValueError("All runs must share the same frame/actuator contract")
        entry["path"] = f"{entry['id']}.csv"
        runs.append((entry, source, path))
    if set(groups.values()) != set(SPLITS):
        raise ValueError("Provide separate train, validation and test flight groups")
    output.mkdir(parents=True)
    for entry, source, path in runs:
        shutil.copyfile(source, output / entry["path"])
        shutil.copyfile(path, output / f"{entry['id']}.provenance.json")
    manifest = {"schema_version": 1, "name": output.name, "contract": contract,
                "flights": [entry for entry, _, _ in runs]}
    write_json(output / "dataset.json", manifest)
    return manifest

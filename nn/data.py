"""Validated logs, causal feature windows and flight-level split enforcement."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

GYRO = ["gyro_x_rad_s", "gyro_y_rad_s", "gyro_z_rad_s"]
MOTORS = [f"motor_{i}" for i in range(4)]
SIGNALS = GYRO + MOTORS
MOTOR_ORDER = ["front_left", "front_right", "rear_right", "rear_left"]
SPLITS = ("train", "validation", "test")
KINDS = ("software_fixture", "px4_sitl", "real_flight")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class WindowConfig:
    sample_period_us: int = 10_000
    interval_tolerance_us: int = 2_000
    history_steps: int = 4
    horizon_steps: int = 5
    horizon_tolerance_us: int = 2_500
    max_motor_age_us: int = 15_000
    max_gyro_age_us: int = 15_000

    def __post_init__(self):
        for key, value in asdict(self).items():
            if type(value) is not int or value < (1 if key in {
                "sample_period_us", "history_steps", "horizon_steps"
            } else 0):
                raise ValueError(f"Invalid window setting {key}: {value}")
        if self.interval_tolerance_us >= self.sample_period_us:
            raise ValueError("Interval tolerance must be smaller than the period")

    @property
    def horizon_us(self):
        return self.sample_period_us * self.horizon_steps

    @property
    def feature_names(self):
        return [f"t-{lag}:{name}" for lag in range(self.history_steps - 1, -1, -1)
                for name in SIGNALS]


def integer_column(series, name):
    """Avoid converting epoch-sized integer timestamps through float64."""
    strings = series.astype(str)
    if not strings.str.fullmatch(r"\d+").all():
        raise ValueError(f"{name} must contain nonnegative integer microseconds")
    try:
        return strings.to_numpy().astype(np.int64)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} outside int64 range") from exc


def read_log(path):
    df = pd.read_csv(path, dtype={"timestamp_us": str, "motor_timestamp_us": str,
                                  "gyro_timestamp_us": str})
    required = ["timestamp_us", "motor_timestamp_us", *SIGNALS,
                "gyro_valid", "motor_valid"]
    missing = set(required) - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    if df.empty:
        raise ValueError(f"{path}: empty log")
    for name in ("timestamp_us", "motor_timestamp_us", "gyro_timestamp_us"):
        if name not in df:
            continue
        df[name] = integer_column(df[name], name)
    if np.any(np.diff(df.timestamp_us.to_numpy()) <= 0):
        raise ValueError(f"{path}: timestamps must increase strictly within a flight")
    for name in SIGNALS:
        df[name] = pd.to_numeric(df[name], errors="raise")
    for name in ("gyro_valid", "motor_valid"):
        if not df[name].isin([0, 1]).all():
            raise ValueError(f"{path}: {name} must contain 0 or 1")
    return df


def row_validity(df, config):
    gyro = df[GYRO].to_numpy(float)
    motors = df[MOTORS].to_numpy(float)
    age = df.timestamp_us.to_numpy() - df.motor_timestamp_us.to_numpy()
    gyro_t = df.get("gyro_timestamp_us", df.timestamp_us).to_numpy()
    gyro_age = df.timestamp_us.to_numpy() - gyro_t
    gv = ((df.gyro_valid.to_numpy() == 1) & np.isfinite(gyro).all(axis=1)
          & (gyro_age >= 0) & (gyro_age <= config.max_gyro_age_us))
    mv = ((df.motor_valid.to_numpy() == 1) & np.isfinite(motors).all(axis=1)
          & ((motors >= 0) & (motors <= 1)).all(axis=1)
          & (age >= 0) & (age <= config.max_motor_age_us))
    return gv, mv


def make_windows(df, config):
    values = df[SIGNALS].to_numpy(float)
    t = df.timestamp_us.to_numpy()
    gv, mv = row_validity(df, config)
    x, y, current, anchors, target_times = [], [], [], [], []
    rejected = {"timing": 0, "invalid_gyro": 0, "invalid_motor": 0}
    for anchor in range(config.history_steps - 1, len(df) - config.horizon_steps):
        start = anchor - config.history_steps + 1
        end = anchor + config.horizon_steps
        intervals = np.diff(t[start:end + 1])
        if (np.any(abs(intervals - config.sample_period_us) > config.interval_tolerance_us)
                or abs((t[end] - t[anchor]) - config.horizon_us) > config.horizon_tolerance_us):
            rejected["timing"] += 1
            continue
        if not gv[start:end + 1].all():
            rejected["invalid_gyro"] += 1
            continue
        if not mv[start:anchor + 1].all():
            rejected["invalid_motor"] += 1
            continue
        x.append(values[start:anchor + 1].reshape(-1))
        current.append(values[anchor, :3])
        y.append(values[end, :3] - values[anchor, :3])
        anchors.append(t[anchor])
        target_times.append(t[end])
    n_features = len(config.feature_names)
    return {"x": np.asarray(x, dtype=float).reshape(-1, n_features),
            "y": np.asarray(y, dtype=float).reshape(-1, 3),
            "current": np.asarray(current, dtype=float).reshape(-1, 3),
            "timestamp_us": np.asarray(anchors, dtype=np.int64),
            "target_timestamp_us": np.asarray(target_times, dtype=np.int64),
            "rejected": rejected}


def load_dataset(manifest_path, config, include_splits: tuple[str, ...] = SPLITS):
    if not include_splits or not set(include_splits).issubset(SPLITS):
        raise ValueError("Select known nonempty dataset splits")
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported dataset schema")
    contract = manifest.get("contract", {})
    if (contract.get("body_frame") != "FRD" or contract.get("gyro_units") != "rad/s"
            or contract.get("motor_order") != MOTOR_ORDER
            or contract.get("actuator_kind") not in ("normalized_pwm", "normalized_output")):
        raise ValueError("Dataset must declare FRD, rad/s, canonical motor order and actuator kind")
    buckets = {s: [] for s in SPLITS}
    seen_ids, seen_hashes, groups, audit = set(), set(), {}, []
    source_kinds = set()
    for flight in manifest.get("flights", []):
        flight_id, group, split = flight["id"], flight["group_id"], flight["split"]
        if not isinstance(flight_id, str) or not flight_id or flight_id in seen_ids:
            raise ValueError("Missing or duplicate flight id")
        if not isinstance(group, str) or not group or split not in SPLITS:
            raise ValueError("Each flight needs a group id and explicit split")
        if flight.get("source_kind") not in KINDS:
            raise ValueError("Each flight must declare its source kind")
        source_kinds.add(flight["source_kind"])
        if group in groups and groups[group] != split:
            raise ValueError(f"Leakage: group {group} occurs in multiple splits")
        groups[group] = split
        path = (manifest_path.parent / flight["path"]).resolve()
        if not path.is_relative_to(manifest_path.parent):
            raise ValueError("Flight path must remain inside the dataset directory")
        digest = sha256(path)
        if digest != flight.get("sha256"):
            raise ValueError(f"Checksum mismatch: {flight_id}")
        if digest in seen_hashes:
            raise ValueError("Duplicate flight content; cannot treat copies as separate flights")
        seen_ids.add(flight_id)
        seen_hashes.add(digest)
        if split not in include_splits:
            audit.append({"id": flight_id, "group_id": group, "split": split,
                          "source_kind": flight["source_kind"], "sha256": digest,
                          "rows": None, "windows": None, "loaded": False})
            continue
        df = read_log(path)
        if contract.get("requires_gyro_timestamp") and "gyro_timestamp_us" not in df:
            raise ValueError("This dataset contract requires gyro source timestamps")
        windows = make_windows(df, config)
        if not len(windows["x"]):
            raise ValueError(f"No usable windows: {flight_id}")
        windows["flight_id"] = np.full(len(windows["x"]), flight_id)
        buckets[split].append(windows)
        audit.append({"id": flight_id, "group_id": group, "split": split,
                      "source_kind": flight["source_kind"], "sha256": digest,
                      "rows": len(df), "windows": len(windows["x"]),
                      "rejected": windows["rejected"]})
    if "software_fixture" in source_kinds and len(source_kinds) > 1:
        raise ValueError("Keep software fixtures separate from simulation/real-flight evaluations")
    result = {}
    if set(groups.values()) != set(SPLITS):
        raise ValueError("Provide separate training, validation and test groups")
    for split in include_splits:
        flights = buckets[split]
        if not flights:
            raise ValueError(f"Missing split: {split}")
        result[split] = {key: np.concatenate([f[key] for f in flights]) for key in
                         ("x", "y", "current", "timestamp_us", "target_timestamp_us", "flight_id")}
    return result, {"manifest_sha256": sha256(manifest_path), "contract": contract,
                    "source_kinds": sorted(source_kinds), "flights": audit}

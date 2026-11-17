"""Import approved local IDSIA HDF5 logs causally. No network or pickle reads."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import TypedDict

import h5py
import numpy as np
from numpy.typing import NDArray
import pandas as pd

from .data import GYRO, MOTOR_ORDER, MOTORS, SPLITS, WindowConfig, sha256

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
NumericArray = NDArray[np.generic]


class NativeSettings(TypedDict):
    max_status_age_us: int
    flying_mask: int
    excluded_status_mask: int
    rotation_to_frd: list[list[float]]
    motor_columns_in_canonical_order: list[str]
    motor_scale: float
    actuator_kind: str
    clock: str
    rate_source: str
    source_body_frame: str


class ImportPlan(TypedDict):
    native_import: NativeSettings
    window: dict[str, int]
    flights: dict[str, str]
    limitations: list[str]


@dataclass(frozen=True)
class NativeRun:
    gyro_time_ns: IntArray
    gyro_flu_rad_s: FloatArray
    motor_time_ns: IntArray
    motor_commands: FloatArray
    status_time_ns: IntArray
    supervisor: IntArray


def read_numeric_topic(path: Path, topic: str, columns: tuple[str, ...]) -> dict[str, NumericArray]:
    """Read selected numeric blocks, skipping object blocks without reading them."""
    result: dict[str, NumericArray] = {}
    with h5py.File(path, "r") as store:
        group = store[topic]
        if not isinstance(group, h5py.Group):
            raise ValueError(f"Missing HDF group: {topic}")
        for key in group:
            if not key.endswith("_items"):
                continue
            values = group[key.replace("_items", "_values")]
            items = group[key]
            if not isinstance(values, h5py.Dataset) or not isinstance(items, h5py.Dataset):
                raise ValueError("Unexpected HDF block layout")
            if values.dtype.kind not in "biuf":
                continue
            if items.dtype.kind != "S" or values.ndim != 2:
                raise ValueError("Expected byte-string column names and a numeric matrix")
            names = [bytes(value).decode("utf-8") for value in items[:]]
            if len(names) != values.shape[1]:
                raise ValueError("HDF numeric block dimensions differ from column labels")
            for index, name in enumerate(names):
                if name in columns:
                    if name in result:
                        raise ValueError(f"Duplicate numeric column: {name}")
                    result[name] = np.asarray(values[:, index])
    if set(result) != set(columns):
        raise ValueError(f"Missing numeric fields in {topic}: {sorted(set(columns) - set(result))}")
    if len({len(value) for value in result.values()}) != 1:
        raise ValueError("Topic columns have different row counts")
    return result


def receipt_timestamps(values: NumericArray) -> IntArray:
    if values.dtype.kind not in "iu" or not len(values):
        raise ValueError("Receipt timestamps must be nonempty integer nanoseconds")
    if np.any(values < 0) or np.any(values > np.iinfo(np.int64).max):
        raise ValueError("Receipt timestamps outside nonnegative int64")
    result = values.astype(np.int64)
    if np.any(np.diff(result) <= 0):
        raise ValueError("Receipt timestamps must increase strictly")
    return result


def read_run(path: Path, settings: NativeSettings) -> NativeRun:
    rate_columns = tuple(f"twist.twist.angular.{axis}" for axis in "xyz")
    motor_columns = tuple(settings["motor_columns_in_canonical_order"])
    gyro = read_numeric_topic(path, "/cf/image_odom", ("timestamp", *rate_columns))
    motors = read_numeric_topic(path, "/cf/motors", ("timestamp", *motor_columns))
    status = read_numeric_topic(path, "/cf/status", ("timestamp", "supervisor_info"))
    flags = status["supervisor_info"]
    if not np.isfinite(flags).all() or np.any(flags != np.floor(flags)) or np.any(flags < 0):
        raise ValueError("Malformed supervisor flags")
    return NativeRun(
        receipt_timestamps(gyro["timestamp"]),
        np.column_stack([gyro[name] for name in rate_columns]).astype(np.float64),
        receipt_timestamps(motors["timestamp"]),
        np.column_stack([motors[name] for name in motor_columns]).astype(np.float64),
        receipt_timestamps(status["timestamp"]),
        flags.astype(np.int64),
    )


def causal_grid(run: NativeRun, settings: NativeSettings, window: WindowConfig) -> tuple[pd.DataFrame, dict[str, object]]:
    """Align in integer ns before reducing precision; never select future packets."""
    for stamps in (run.gyro_time_ns, run.motor_time_ns, run.status_time_ns):
        receipt_timestamps(stamps)
    rotation = np.asarray(settings["rotation_to_frd"], dtype=np.float64)
    if (rotation.shape != (3, 3) or not np.isfinite(rotation).all()
            or not np.allclose(rotation @ rotation.T, np.eye(3))
            or not np.isclose(np.linalg.det(rotation), 1)):
        raise ValueError("Require a proper source-to-FRD rotation")
    if not np.isfinite(settings["motor_scale"]) or settings["motor_scale"] <= 0:
        raise ValueError("Motor command scale must be finite and positive")
    if settings["max_status_age_us"] <= 0 or settings["flying_mask"] <= 0:
        raise ValueError("Declare finite status age and a flying mask")
    origin = min(int(t[0]) for t in (run.gyro_time_ns, run.motor_time_ns, run.status_time_ns))
    end_us = (int(run.gyro_time_ns[-1]) - origin) // 1000
    grid_us = np.arange(0, end_us + 1, window.sample_period_us, dtype=np.int64)
    if len(grid_us) > 10_000_000:
        raise ValueError("Unexpectedly long flight: refusing oversized grid")
    grid_ns = grid_us * 1000 + origin

    def align(stamps: IntArray) -> tuple[IntArray, IntArray, NDArray[np.bool_]]:
        indices = np.searchsorted(stamps, grid_ns, side="right") - 1
        safe = np.maximum(indices, 0)
        selected = stamps[safe]
        stamp_us = (selected - origin) // 1000
        stamp_us[indices < 0] = 0
        present = (indices >= 0) & (selected <= grid_ns)
        return safe, stamp_us, present

    gi, gt, gp = align(run.gyro_time_ns)
    mi, mt, mp = align(run.motor_time_ns)
    si, st, sp = align(run.status_time_ns)
    flags = run.supervisor[si]
    flying = (sp & (grid_us - st <= settings["max_status_age_us"])
              & ((flags & settings["flying_mask"]) != 0)
              & ((flags & settings["excluded_status_mask"]) == 0))
    gyro = run.gyro_flu_rad_s[gi] @ rotation.T
    motors = run.motor_commands[mi] / settings["motor_scale"]
    gyro_valid = gp & flying & np.isfinite(gyro).all(axis=1) & (grid_us - gt <= window.max_gyro_age_us)
    motor_valid = (mp & flying & np.isfinite(motors).all(axis=1)
                   & ((motors >= 0) & (motors <= 1)).all(axis=1)
                   & (grid_us - mt <= window.max_motor_age_us))
    df = pd.DataFrame({
        "timestamp_us": grid_us, "gyro_timestamp_us": gt, "motor_timestamp_us": mt,
        **{name: gyro[:, i] for i, name in enumerate(GYRO)},
        **{name: motors[:, i] for i, name in enumerate(MOTORS)},
        "gyro_valid": gyro_valid.astype(int), "motor_valid": motor_valid.astype(int),
    })
    audit: dict[str, object] = {
        "receipt_origin_ns": origin, "grid_rows": len(df),
        "source_rows": {"gyro": len(run.gyro_time_ns), "motors": len(run.motor_time_ns), "status": len(run.status_time_ns)},
        "flying_and_fresh_status_rows": int(flying.sum()),
        "valid_gyro_rows": int(gyro_valid.sum()), "valid_motor_rows": int(motor_valid.sum()),
        "both_valid_rows": int((gyro_valid & motor_valid).sum()),
        "invalid_rows_retained": True,
        "alignment": "latest received past packet on an integer-nanosecond grid; no interpolation or future filling",
    }
    return df, audit


def import_local(source: Path, config_path: Path, output: Path) -> dict[str, object]:
    if output.exists():
        raise ValueError(f"Refusing to overwrite dataset: {output}")
    plan: ImportPlan = json.loads(config_path.read_text())
    settings = plan["native_import"]
    window = WindowConfig(**plan["window"])
    if (settings["actuator_kind"] != "normalized_output"
            or settings["motor_columns_in_canonical_order"] != ["thrust.m4", "thrust.m1", "thrust.m2", "thrust.m3"]):
        raise ValueError("This adapter requires the audited IDSIA command/channel mapping")
    if set(plan["flights"].values()) != set(SPLITS):
        raise ValueError("Plan must reserve complete train/validation/test recordings")
    manifest_path = source / "download_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if not manifest["complete"]:
        raise ValueError("Native source download manifest is incomplete")
    entries = {entry["name"]: entry for entry in manifest["files"]}
    expected = {f"rosbag2_{name}.h5" for name in plan["flights"]}
    if len(entries) != len(manifest["files"]) or set(entries) != expected:
        raise ValueError("Import plan must match the approved local recordings exactly")
    for flight in plan["flights"]:
        if not re.fullmatch(r"(?:square|chirp|random)_\d{8}_run\d+", flight):
            raise ValueError("Invalid native flight identifier")
        name = f"rosbag2_{flight}.h5"
        path = source / name
        if path.stat().st_size != entries[name]["bytes"] or sha256(path) != entries[name]["sha256"]:
            raise ValueError(f"Native checksum/size mismatch: {name}")
    # Preserve every grid row (invalid flags included) to avoid bridging gaps.
    output.mkdir(parents=True)
    flights: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []
    for flight, split in plan["flights"].items():
        name = f"rosbag2_{flight}.h5"
        df, audit = causal_grid(read_run(source / name, settings), settings, window)
        path = output / f"{flight}.csv"
        df.to_csv(path, index=False, float_format="%.12g")
        flights.append({"id": flight, "group_id": flight, "split": split,
                        "path": path.name, "sha256": sha256(path), "source_kind": "real_flight",
                        "native_name": name, "native_sha256": entries[name]["sha256"]})
        audits.append({"id": flight, "split": split, **audit})
    contract: dict[str, object] = {
        "body_frame": "FRD", "gyro_units": "rad/s", "motor_order": MOTOR_ORDER,
        "actuator_kind": settings["actuator_kind"], "timebase": "ROS recorder receipt clock",
        "gyro_source": "received onboard body-rate estimate; canonical gyro columns are aliases",
        "actuator_source": "logged normalized per-motor thrust commands; applied ESC duty not verified",
        "requires_gyro_timestamp": True,
    }
    dataset: dict[str, object] = {"schema_version": 1, "name": output.name,
                                 "contract": contract, "flights": flights}
    report: dict[str, object] = {
        "schema_version": 1, "settings": settings, "window": asdict(window),
        "config_sha256": sha256(config_path), "source_manifest_sha256": sha256(manifest_path),
        "importer_sha256": sha256(Path(__file__)), "runs": audits,
        "limitations": plan["limitations"], "future_information_used_in_features": False,
        "test_metrics_computed": False,
    }
    for filename, value in (("dataset.json", dataset), ("import_audit.json", report)):
        (output / filename).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    return report

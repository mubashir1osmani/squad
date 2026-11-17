"""Causal Keras forecasts as observation-only ground-station JSONL packets."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext
import json
from pathlib import Path
import sys
import time
from typing import Any, TextIO

import numpy as np
import pandas as pd

from .data import MOTOR_ORDER, integer_column, read_log, sha256
from .forecast import ForecastPredictor, predict_arrays
from .forecast_data import POSITION, make_inputs, plan_columns, trajectory_contract

SOURCE_KINDS = ("real_flight", "px4_sitl", "software_fixture", "unknown")
MAX_TIMESTAMP = np.iinfo(np.int64).max


def read_metadata(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(),
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"Nonfinite metadata: {value}")
        ),
    )
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Unsupported source metadata")
    return value


def validate_contract(contract: dict[str, Any], predictor: ForecastPredictor) -> None:
    """A CSV's units/frame/actuator mapping must agree with the trained model."""
    expected = predictor.metadata["dataset_audit"]["contract"]
    if (
        contract.get("body_frame") != "FRD"
        or contract.get("gyro_units") != "rad/s"
        or contract.get("motor_order") != MOTOR_ORDER
    ):
        raise ValueError("Source must declare FRD, rad/s and canonical motor order")
    for key in ("actuator_kind", "requires_gyro_timestamp"):
        if contract.get(key, False) != expected.get(key, False):
            raise ValueError(f"Source/model contract mismatch: {key}")
    if predictor.config.mode == "trajectory":
        required = trajectory_contract(predictor.config)
        if (
            contract.get("trajectory") != required
            or expected.get("trajectory") != required
        ):
            raise ValueError(
                "Trajectory source/model must declare NED state and a causal planned route"
            )


def validate_source(
    input_path: Path,
    metadata_path: Path | None,
    source_kind: str,
    predictor: ForecastPredictor,
) -> dict[str, Any]:
    if source_kind not in SOURCE_KINDS:
        raise ValueError("Unsupported source_kind")
    path = metadata_path or input_path.parent / "dataset.json"
    metadata = read_metadata(path)
    if "flights" in metadata:
        matches = [
            flight
            for flight in metadata["flights"]
            if (path.parent / flight["path"]).resolve() == input_path.resolve()
        ]
        if len(matches) != 1:
            raise ValueError("Input must match exactly one metadata flight")
        source = matches[0]
        digest = source.get("sha256")
    else:
        source = metadata
        digest = source.get("input_sha256")
    if source.get("source_kind") != source_kind:
        raise ValueError("Requested source_kind contradicts source metadata")
    if digest != sha256(input_path):
        raise ValueError("Source input checksum mismatch")
    contract = metadata.get("contract")
    if not isinstance(contract, dict):
        raise ValueError("Source metadata needs a contract")
    validate_contract(contract, predictor)
    return contract


def validate_model(predictor: ForecastPredictor) -> None:
    config, metadata = predictor.config, predictor.metadata
    if not 1 <= config.horizon_steps <= 128:
        raise ValueError("Ground-station export supports 1..128 horizons")
    shapes = {"history": [56]}
    if config.mode == "trajectory":
        shapes.update(state=[7], route=[config.horizon_steps, 3])
    if (
        metadata.get("feature_names") != config.window.feature_names
        or metadata.get("input_shapes") != shapes
        or metadata.get("output_names") != list(config.output_names)
        or metadata.get("offsets_us") != config.offsets_us.tolist()
        or metadata.get("rate_axis_order") != ["roll_x", "pitch_y", "yaw_z"]
    ):
        raise ValueError(
            "Model metadata disagrees with forecast feature/output contract"
        )
    actual_inputs, actual_outputs = predictor.model.input, predictor.model.output
    if not isinstance(actual_inputs, dict) or not isinstance(actual_outputs, dict):
        raise ValueError(
            "Actual Keras input/output names must follow the forecast contract"
        )
    expected_outputs = {name: [config.horizon_steps, 3] for name in config.output_names}
    for kind, actual, expected in (
        ("input", actual_inputs, shapes),
        ("output", actual_outputs, expected_outputs),
    ):
        if set(actual) != set(expected) or any(
            tuple(actual[name].shape) != (None, *shape)
            for name, shape in expected.items()
        ):
            raise ValueError(
                f"Actual Keras {kind} shapes/names disagree with the forecast contract"
            )
    for name, shape in shapes.items():
        for suffix in ("mean", "scale", "min", "max"):
            if predictor.scaling.get(f"{name}_{suffix}", np.empty(0)).shape != tuple(
                shape
            ):
                raise ValueError(f"Invalid model normalization shape: {name}_{suffix}")
    for name in config.output_names:
        if predictor.scaling.get(f"{name}_target_scale", np.empty(0)).shape != ():
            raise ValueError(f"Invalid output scale: {name}")


def packets(
    predictor: ForecastPredictor,
    frame: pd.DataFrame,
    source_kind: str,
    model_id: str,
    source_now_us: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Each forecast reads eight past/current rows and the anchor's available plan."""
    validate_model(predictor)
    if (
        source_kind not in SOURCE_KINDS
        or not isinstance(model_id, str)
        or not model_id.strip()
        or not 1 <= len(model_id) <= 128
    ):
        raise ValueError("Invalid model ID or source_kind")
    if source_now_us is not None and (
        type(source_now_us) is not int or not 0 <= source_now_us <= MAX_TIMESTAMP
    ):
        raise ValueError(
            "source_now_us must be nonnegative int64 source-clock microseconds"
        )
    frame = frame.copy()
    required_times = ["timestamp_us", "motor_timestamp_us"]
    if predictor.metadata["dataset_audit"]["contract"].get("requires_gyro_timestamp"):
        required_times.append("gyro_timestamp_us")
    if predictor.config.mode == "trajectory":
        required_times.extend(("state_timestamp_us", "plan_timestamp_us"))
    for name in dict.fromkeys(
        required_times + (["gyro_timestamp_us"] if "gyro_timestamp_us" in frame else [])
    ):
        if name not in frame:
            raise ValueError(f"Missing source timestamp: {name}")
        frame[name] = integer_column(frame[name], name)
    stamps = frame.timestamp_us.to_numpy()
    if not len(frame) or np.any(np.diff(stamps) <= 0):
        raise ValueError("Source timestamps must be nonempty and strictly increasing")
    if int(stamps[-1]) > MAX_TIMESTAMP - int(predictor.config.offsets_us[-1]):
        raise ValueError("Prediction timestamp outside int64 range")
    # Validate schema before writing even the first warmup packet. This also accepts
    # short streams, which produce explicit warmup packets instead of old predictions.
    if len(frame) < 8:
        padded = pd.concat(
            [frame.iloc[:1]] * (8 - len(frame)) + [frame], ignore_index=True
        )
        make_inputs(padded, np.array([7]), predictor.config)
    results: dict[int, tuple[dict[str, np.ndarray], bool]] = {}
    if len(frame) >= 8:
        anchors = np.arange(7, len(frame), dtype=np.int64)
        inputs, baseline, valid = make_inputs(frame, anchors, predictor.config)
        if source_now_us is not None:
            age = source_now_us - stamps[anchors]
            valid &= (age >= 0) & (age <= predictor.config.max_source_age_us)
        # Bounded batches keep repeated live/export inference costs reasonable.
        for begin in range(0, len(anchors), 1024):
            selected = np.flatnonzero(valid[begin : begin + 1024]) + begin
            if not len(selected):
                continue
            chosen = {key: value[selected] for key, value in inputs.items()}
            predicted = predict_arrays(
                predictor.model,
                chosen,
                {key: value[selected] for key, value in baseline.items()},
                predictor.scaling,
            )
            outside = np.zeros(len(selected), dtype=bool)
            for name, value in chosen.items():
                out = (value < predictor.scaling[f"{name}_min"]) | (
                    value > predictor.scaling[f"{name}_max"]
                )
                outside |= out.reshape(len(value), -1).any(axis=1)
            for local, index in enumerate(selected):
                results[int(anchors[index])] = (
                    {key: value[local] for key, value in predicted.items()},
                    bool(outside[local]),
                )
    for index, stamp in enumerate(stamps):
        packet: dict[str, Any] = {
            "schema_version": 1,
            "model_id": model_id,
            "model_mode": predictor.config.mode,
            "source_kind": source_kind,
            "position_frame": "NED",
            "rates_frame": "FRD",
            "timestamp_us": int(stamp),
            "prediction_valid": False,
            "outside_training_range": False,
            "reason": "history_warmup"
            if index < 7
            else "invalid_or_stale_source_history_state_or_plan",
            "observed_position_m": None,
            "planned_path": [],
            "predictions": [],
        }
        if index in results:
            predicted, outside = results[index]
            packet.update(
                prediction_valid=True, outside_training_range=outside, reason="ok"
            )
            trajectory = predictor.config.mode == "trajectory"
            if trajectory:
                packet["observed_position_m"] = (
                    frame.iloc[index][POSITION].to_numpy(dtype=float).tolist()
                )
                planned = (
                    frame.iloc[index][plan_columns(predictor.config)]
                    .to_numpy(dtype=float)
                    .reshape(-1, 3)
                )
            for step, offset in enumerate(predictor.config.offsets_us):
                target = int(stamp) + int(offset)
                packet["predictions"].append(
                    {
                        "target_timestamp_us": target,
                        "rates_rad_s": predicted["rates"][step].tolist(),
                        "position_m": predicted["position"][step].tolist()
                        if trajectory
                        else None,
                        "velocity_m_s": predicted["velocity"][step].tolist()
                        if trajectory
                        else None,
                    }
                )
                if trajectory:
                    packet["planned_path"].append(
                        {
                            "target_timestamp_us": target,
                            "position_m": planned[step].tolist(),
                        }
                    )
        yield packet


def write_packets(
    values: Iterator[dict[str, Any]],
    stream: TextIO,
    pace: bool = False,
    speed: float = 1.0,
) -> dict[str, int]:
    if not np.isfinite(speed) or speed <= 0:
        raise ValueError("Pacing speed must be finite and positive")
    started, first, count, valid = time.monotonic(), None, 0, 0
    for packet in values:
        if first is None:
            first = packet["timestamp_us"]
            started = time.monotonic()
        if pace:
            delay = (packet["timestamp_us"] - first) / (1_000_000 * speed) - (
                time.monotonic() - started
            )
            if delay > 0:
                time.sleep(delay)
        line = json.dumps(packet, separators=(",", ":"), allow_nan=False)
        if len(line.encode("utf-8")) > 1_048_576:
            raise ValueError("Prediction exceeds JSONL line limit")
        stream.write(line + "\n")
        stream.flush()
        count += 1
        valid += int(packet["prediction_valid"])
    return {"packets": count, "valid_packets": valid}


def export(
    model: Path,
    input_path: Path,
    output: str,
    source_kind: str,
    metadata: Path | None = None,
    model_id: str | None = None,
    pace: bool = False,
    speed: float = 1.0,
    source_now_us: int | None = None,
) -> dict[str, object]:
    if output != "-" and Path(output).exists():
        raise ValueError(f"Refusing to overwrite export: {output}")
    predictor = ForecastPredictor(model)
    contract = validate_source(input_path, metadata, source_kind, predictor)
    frame = read_log(input_path)
    if contract.get("requires_gyro_timestamp") and "gyro_timestamp_us" not in frame:
        raise ValueError("Source contract requires gyro_timestamp_us")
    values = packets(
        predictor,
        frame,
        source_kind,
        model.name if model_id is None else model_id,
        source_now_us,
    )
    # Resolve all source validation before creating a partial output on a schema error.
    first = next(values)

    def all_values() -> Iterator[dict[str, Any]]:
        yield first
        yield from values

    if output != "-":
        Path(output).parent.mkdir(parents=True, exist_ok=True)
    context = (
        nullcontext(sys.stdout)
        if output == "-"
        else Path(output).open("x", encoding="utf-8")
    )
    with context as stream:
        summary = write_packets(all_values(), stream, pace, speed)
    return {
        "output": output,
        "source_kind": source_kind,
        "model_mode": predictor.config.mode,
        **summary,
    }

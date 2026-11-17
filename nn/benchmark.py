"""Whole-flight development comparisons on already exposed IDSIA recordings.

No networking. Original frozen experiments and dataset manifests are never changed.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any

import numpy as np
from numpy.typing import NDArray
import pandas as pd

from .data import WindowConfig, load_dataset, read_log, sha256
from .inference import Predictor
from .model import metrics, predict_delta
from .pipeline import load_model, read_config, train_loaded, write_json

WindowArrays = dict[str, NDArray[Any]]
MODES = ("persistence", "rate_trend", "ridge", "mlp_original", "mlp_shared")


def recording_fold(flight_id: str) -> int:
    match = re.fullmatch(r"(?:square|chirp|random)_\d{8}_run([1-5])", flight_id)
    if match is None:
        raise ValueError(f"This protocol requires named IDSIA manoeuvre recordings: {flight_id}")
    return (int(match[1]) - 1) % 4


def split_flights(flight_ids: list[str], score_fold: int) -> dict[str, str]:
    if score_fold not in range(4):
        raise ValueError("Choose one of four recording folds")
    return {
        name: ("test" if recording_fold(name) == score_fold else
               "validation" if recording_fold(name) == (score_fold + 1) % 4 else "train")
        for name in flight_ids
    }


def rate_trend(data: WindowArrays, window: WindowConfig) -> NDArray[np.float64]:
    """OLS slope on past grid snapshots, extrapolated from the latest rate.

    This predicts received rate values; it does not reconstruct acquisition timing.
    Exact grid spacing is required so no nominal-time assumption is hidden.
    """
    if window.interval_tolerance_us != 0 or window.history_steps < 2:
        raise ValueError("Rate trend requires exact grid intervals and at least two snapshots")
    offsets = np.arange(window.history_steps, dtype=float) * window.sample_period_us / 1e6
    centered = offsets - offsets.mean()
    rates = data["x"].reshape(-1, window.history_steps, 7)[:, :, :3]
    slope = np.einsum("t,ntc->nc", centered / np.sum(centered ** 2), rates)
    return data["current"] + slope * (window.horizon_us / 1e6)


def streaming_parity(
    manifest_path: Path, assignments: dict[str, str], model_dir: Path,
    scored: WindowArrays, prediction: NDArray[np.float64],
) -> dict[str, int | float]:
    manifest = json.loads(manifest_path.read_text())
    count, largest = 0, 0.0
    for flight in manifest["flights"]:
        if assignments[flight["id"]] != "test":
            continue
        predictor = Predictor(model_dir)
        mask = scored["flight_id"] == flight["id"]
        expected = dict(zip(scored["timestamp_us"][mask].tolist(), prediction[mask], strict=True))
        frame = read_log(manifest_path.parent / flight["path"])
        for sample in frame.to_dict("records"):
            result = predictor.push(sample)
            stamp = sample["timestamp_us"]
            if stamp not in expected:
                continue
            if not result["prediction_valid"]:
                raise ValueError("Streaming predictor rejected a scored offline window")
            actual = np.array([result[f"predicted_gyro_{axis}_rad_s"] for axis in "xyz"])
            error = float(np.max(abs(actual - expected[stamp])))
            largest = max(largest, error)
            count += 1
    if count != len(prediction) or largest > 1e-12:
        raise ValueError("Streaming/offline prediction parity failed")
    return {"compared_windows": count, "max_absolute_difference_rad_s": largest}


def run_benchmark(dataset: Path, config_path: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError(f"Refusing to overwrite benchmark: {output}")
    config, window = read_config(config_path)
    if not config.get("acknowledge_exposed_development_data"):
        raise ValueError("This benchmark reuses exposed test recordings; explicitly acknowledge development use")
    data, audit = load_dataset(dataset, window)
    if audit["source_kinds"] != ["real_flight"]:
        raise ValueError("This protocol is for existing IDSIA real-flight development data")
    combined = {key: np.concatenate([part[key] for part in data.values()]) for key in data["train"]}
    flight_ids = sorted(np.unique(combined["flight_id"]).tolist())
    # Validate the entire plan before creating an output directory.
    plans = [split_flights(flight_ids, fold) for fold in range(4)]
    for plan in plans:
        group_splits: dict[str, set[str]] = {}
        for flight in audit["flights"]:
            group_splits.setdefault(flight["group_id"], set()).add(plan[flight["id"]])
        if any(len(splits) != 1 for splits in group_splits.values()):
            raise ValueError("Recording folds cross a dataset group")
        for split in ("train", "validation", "test"):
            families = {name.split("_")[0] for name, value in plan.items() if value == split}
            if families != {"square", "chirp", "random"}:
                raise ValueError("Each split must cover all three manoeuvre families")
    output.mkdir(parents=True)
    protocol = {
        "status": "development_cross_validation_on_previously_exposed_data",
        "config": config, "config_sha256": sha256(config_path),
        "dataset_manifest_sha256": audit["manifest_sha256"],
        "folds": plans, "score_role": "development score, not a new untouched final test",
        "selection": "Fixed variants; epoch selected only by the distinct validation recordings",
        "forecast": f"Received onboard body-rate estimate {window.horizon_us / 1000:g} ms ahead; past-only inputs",
    }
    write_json(output / "protocol.json", protocol)
    tables: list[pd.DataFrame] = []
    fold_reports: list[dict[str, Any]] = []
    for fold, plan in enumerate(plans):
        roles = np.array([plan[str(name)] for name in combined["flight_id"]])
        partition = {role: {key: value[roles == role] for key, value in combined.items()}
                     for role in ("train", "validation", "test")}
        fold_audit = deepcopy(audit)
        for flight in fold_audit["flights"]:
            flight["split"] = plan[flight["id"]]
        fold_audit["development_protocol"] = protocol["status"]
        scored = partition["test"]
        truth = scored["current"] + scored["y"]
        predictions = {"persistence": scored["current"], "rate_trend": rate_trend(scored, window)}
        variants: dict[str, Any] = {}
        for name in ("mlp_original", "mlp_shared"):
            settings = deepcopy(config)
            shared = name == "mlp_shared"
            settings.update({"target_scaling": "shared_zero_center" if shared else "per_axis",
                             "selection_metric": "normalized_mae" if shared else "normalized_mse",
                             "zero_output_init": shared})
            model_dir = output / f"fold-{fold}" / name
            metadata, validation = train_loaded(partition, fold_audit, model_dir, settings, window)
            metadata.update({"status": protocol["status"], "score_recordings": sorted(
                name for name, role in plan.items() if role == "test")})
            write_json(model_dir / "model.json", metadata)
            _, arrays, _ = load_model(model_dir)
            predictions[name] = scored["current"] + predict_delta(scored["x"], arrays)
            if name == "mlp_original":
                predictions["ridge"] = scored["current"] + predict_delta(scored["x"], arrays, "ridge")
            variants[name] = {"best_epoch": metadata["best_epoch"],
                              "weights_sha256": metadata["weights_sha256"],
                              "validation": validation,
                              "streaming_parity": streaming_parity(dataset, plan, model_dir, scored, predictions[name])}
        fold_reports.append({"fold": fold, "recordings": plan,
                             "windows": {role: len(part["x"]) for role, part in partition.items()},
                             "metrics": {name: metrics(truth, predictions[name], scored["flight_id"]) for name in MODES},
                             "models": variants})
        table: dict[str, Any] = {"fold": fold, "flight_id": scored["flight_id"],
                                 "timestamp_us": scored["timestamp_us"],
                                 "target_timestamp_us": scored["target_timestamp_us"]}
        for axis, letter in enumerate("xyz"):
            table[f"truth_{letter}_rad_s"] = truth[:, axis]
            for name in MODES:
                table[f"{name}_{letter}_rad_s"] = predictions[name][:, axis]
        tables.append(pd.DataFrame(table))
    full = pd.concat(tables, ignore_index=True)
    truth = full[[f"truth_{axis}_rad_s" for axis in "xyz"]].to_numpy()
    ids = full.flight_id.to_numpy(dtype=str)
    comparison = {name: metrics(truth, full[[f"{name}_{axis}_rad_s" for axis in "xyz"]].to_numpy(), ids)
                  for name in MODES}
    for result in comparison.values():
        result["equal_flight_mean_mae"] = float(np.mean([f["mae"] for f in result["by_flight"].values()]))
    report = {**protocol, "windows_scored_once_each": len(full), "metrics": comparison,
              "fold_results": fold_reports,
              "code_sha256": {path.name: sha256(path) for path in sorted(Path(__file__).parent.glob("*.py"))},
              "limitations": ["All recordings share one aircraft/date; correlated windows are not independent trials",
                              "Old test recordings have been exposed; fresh recordings are needed for a final claim",
                              "No additional real flights were acquired or downloaded",
                              "No FPGA execution, flight-control or failure-detection validation"]}
    full.to_csv(output / "predictions.csv", index=False, float_format="%.12g")
    write_json(output / "summary.json", report)
    return {"output": str(output), "status": protocol["status"], "windows": len(full),
            "mae_rad_s": {name: value["mae"] for name, value in comparison.items()},
            "equal_flight_mae_rad_s": {name: value["equal_flight_mean_mae"] for name, value in comparison.items()}}

"""Reproducible training and explicitly requested held-out evaluation."""
from dataclasses import asdict
import json
import platform
from pathlib import Path
import time
from typing import Any

import numpy as np
import pandas as pd

from .data import WindowConfig, load_dataset, sha256
from .model import fit_ridge, fit_scaling, metrics, normalized, predict_delta, train_mlp

DEFAULT_CONFIG = Path(__file__).parent / "configs/default.json"


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def read_config(path):
    config = json.loads(Path(path).read_text())
    window = WindowConfig(**config["window"])
    training = config["training"]
    for key in ("seed", "hidden_units", "epochs", "patience", "batch_size"):
        if type(training[key]) is not int or training[key] < (0 if key == "seed" else 1):
            raise ValueError(f"Invalid training setting: {key}")
    for key in ("learning_rate", "l2", "ridge_penalty"):
        value = training[key]
        if not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid training setting: {key}")
    if config.get("target_scaling", "per_axis") not in {"per_axis", "shared_zero_center"}:
        raise ValueError("Unknown target scaling")
    if config.get("selection_metric", "normalized_mse") not in {"normalized_mse", "normalized_mae"}:
        raise ValueError("Unknown selection metric")
    if type(config.get("zero_output_init", False)) is not bool:
        raise ValueError("zero_output_init must be boolean")
    return config, window


def evaluate_arrays(data, arrays):
    truth = data["current"] + data["y"]
    predictions = {"persistence": data["current"],
                   "ridge": data["current"] + predict_delta(data["x"], arrays, "ridge"),
                   "mlp": data["current"] + predict_delta(data["x"], arrays)}
    return {name: metrics(truth, prediction, data["flight_id"])
            for name, prediction in predictions.items()}


def train(dataset, output, config_path=DEFAULT_CONFIG):
    config, window = read_config(config_path)
    data, audit = load_dataset(dataset, window, include_splits=("train", "validation"))
    return train_loaded(data, audit, output, config, window)


def train_loaded(
    data: dict[str, dict[str, np.ndarray]], audit: dict[str, Any],
    output: Path, config: dict[str, Any], window: WindowConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit already partitioned windows; callers must enforce whole-group splits."""
    output = Path(output)
    if output.exists():
        raise ValueError(f"Refusing to overwrite experiment: {output}")
    scaling = fit_scaling(data["train"]["x"], data["train"]["y"],
                          config.get("target_scaling", "per_axis"))
    x, y = normalized(data["train"]["x"], data["train"]["y"], scaling)
    xv, yv = normalized(data["validation"]["x"], data["validation"]["y"], scaling)
    begin = time.perf_counter()
    weights, history, best_epoch = train_mlp(
        x, y, xv, yv, config["training"],
        selection_metric=config.get("selection_metric", "normalized_mse"),
        zero_output_init=config.get("zero_output_init", False),
    )
    arrays = {**weights, **scaling, "ridge": fit_ridge(x, y, config["training"]["ridge_penalty"]),
              "x_min": data["train"]["x"].min(axis=0),
              "x_max": data["train"]["x"].max(axis=0)}
    # Evaluate the same float32 parameters that will be saved and reloaded.
    arrays = {k: a.astype(np.float32) for k, a in arrays.items()}
    validation = evaluate_arrays(data["validation"], arrays)
    output.mkdir(parents=True)
    np.savez(output / "weights.npz", **arrays)
    code_hashes = {p.name: sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    metadata = {"schema_version": 1, "architecture": {"input": x.shape[1],
                "hidden": config["training"]["hidden_units"], "output": 3, "activation": "relu"},
                "parameter_count": int(sum(a.size for a in weights.values())),
                "window": asdict(window), "feature_names": window.feature_names,
                "prediction": "future_body_gyro = current_body_gyro + predicted_delta_rad_s",
                "contract": audit["contract"], "training_config": config["training"],
                "source_kinds": audit["source_kinds"], "best_epoch": best_epoch,
                "epochs_run": int(history[-1]["epoch"]), "weights_sha256": sha256(output / "weights.npz"),
                "dataset_audit": audit, "code_sha256": code_hashes,
                "software": {"python": platform.python_version(), "numpy": np.__version__,
                             "pandas": pd.__version__},
                "training_seconds": time.perf_counter() - begin,
                "target_scaling": config.get("target_scaling", "per_axis"),
                "zero_output_init": config.get("zero_output_init", False),
                "validation_selection_metric": config.get("selection_metric", "normalized_mse"),
                "status": "software_fixture_only" if audit["source_kinds"] == ["software_fixture"]
                          else "experimental_unvalidated_for_flight",
                "test_evaluated_during_training": False,
                "fpga_deployed": False}
    write_json(output / "model.json", metadata)
    write_json(output / "validation.json", validation)
    pd.DataFrame(history).to_csv(output / "training_history.csv", index=False)
    return metadata, validation


def load_model(directory):
    directory = Path(directory)
    metadata = json.loads((directory / "model.json").read_text())
    if metadata.get("schema_version") != 1 or metadata["architecture"]["activation"] != "relu":
        raise ValueError("Unsupported model")
    if sha256(directory / "weights.npz") != metadata["weights_sha256"]:
        raise ValueError("Model weights checksum mismatch")
    window = WindowConfig(**metadata["window"])
    if metadata["feature_names"] != window.feature_names:
        raise ValueError("Model feature contract mismatch")
    n, h = metadata["architecture"]["input"], metadata["architecture"]["hidden"]
    if n != len(window.feature_names) or metadata["architecture"]["output"] != 3:
        raise ValueError("Model dimensions mismatch")
    shapes = {"w1": (n, h), "b1": (h,), "w2": (h, 3), "b2": (3,),
              "x_mean": (n,), "x_scale": (n,), "y_mean": (3,), "y_scale": (3,),
              "ridge": (n + 1, 3), "x_min": (n,), "x_max": (n,)}
    with np.load(directory / "weights.npz", allow_pickle=False) as saved:
        arrays = {k: saved[k] for k in shapes}
    for k, shape in shapes.items():
        if arrays[k].shape != shape or not np.isfinite(arrays[k]).all():
            raise ValueError(f"Malformed model array: {k}")
    if np.any(arrays["x_scale"] <= 0) or np.any(arrays["y_scale"] <= 0):
        raise ValueError("Model scales must be positive")
    return metadata, arrays, window


def evaluate(dataset, model_dir, split, output, acknowledge_test=False):
    if split == "test" and not acknowledge_test:
        raise ValueError("Final test is explicit: pass --acknowledge-test after freezing model/config")
    if Path(output).exists():
        raise ValueError(f"Refusing to overwrite evaluation: {output}")
    metadata, arrays, window = load_model(model_dir)
    data, audit = load_dataset(dataset, window)
    if audit["contract"] != metadata["contract"]:
        raise ValueError("Dataset actuator/frame contract does not match model")
    if audit["manifest_sha256"] != metadata["dataset_audit"]["manifest_sha256"]:
        # A new dataset is acceptable only when its groups and files are independent
        # of ALL model-development data. Reassigning an old train run to test is not.
        old = metadata["dataset_audit"]["flights"]
        old_groups, old_hashes = {f["group_id"] for f in old}, {f["sha256"] for f in old}
        for f in audit["flights"]:
            if f["split"] == split and (f["group_id"] in old_groups or f["sha256"] in old_hashes):
                raise ValueError("Evaluation run overlaps model-development dataset")
    report = {"split": split, "source_kinds": audit["source_kinds"],
              "model_status": metadata["status"], "horizon_us": window.horizon_us,
              "weights_sha256": metadata["weights_sha256"],
              "dataset_manifest_sha256": audit["manifest_sha256"],
              "windows": len(data[split]["x"]), "metrics": evaluate_arrays(data[split], arrays)}
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return report

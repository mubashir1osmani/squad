"""Inspectable held-out predictions and streaming parity, without retraining."""
from __future__ import annotations

import json
from pathlib import Path
import time

import numpy as np
from numpy.typing import NDArray
import pandas as pd

from .data import GYRO, load_dataset, read_log, sha256
from .inference import Predictor
from .model import metrics, predict_delta
from .pipeline import load_model

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
SERIES = ("Measured", "Neural network", "Ridge", "Persistence")


def display_indices(times: IntArray, truth: FloatArray, predicted: FloatArray,
                    period_us: int, limit: int = 500) -> IntArray:
    """Retain gap boundaries and extrema; evenly sample the remaining display.

    This only changes the plot. CSV predictions and error metrics use all rows.
    """
    if len(times) <= limit:
        return np.arange(len(times), dtype=np.int64)
    gaps = np.flatnonzero(np.diff(times) != period_us)
    keep = {0, len(times) - 1, *gaps.tolist(), *(gaps + 1).tolist()}
    for axis in range(3):
        keep.update((int(truth[:, axis].argmin()), int(truth[:, axis].argmax()),
                     int(predicted[:, axis].argmin()), int(predicted[:, axis].argmax()),
                     int(abs(predicted[:, axis] - truth[:, axis]).argmax())))
    if len(keep) >= limit:
        raise ValueError("Too many discontinuities for the declared display bound")
    candidates = np.setdiff1d(np.arange(len(times)), np.array(sorted(keep)))
    sampled = candidates[np.linspace(0, len(candidates) - 1, limit - len(keep), dtype=int)]
    return np.array(sorted(keep | set(sampled.tolist())), dtype=np.int64)


def build_report(dataset: Path, model: Path, evaluation: Path, output: Path) -> dict[str, object]:
    if output.exists():
        raise ValueError(f"Refusing to overwrite inspection: {output}")
    metadata, arrays, window = load_model(model)
    scored = json.loads(evaluation.read_text())
    if scored["split"] != "test" or scored["weights_sha256"] != metadata["weights_sha256"]:
        raise ValueError("Inspection requires a final-test evaluation of these exact weights")
    if scored["dataset_manifest_sha256"] != sha256(dataset):
        raise ValueError("Inspection dataset differs from the evaluated dataset")
    data, audit = load_dataset(dataset, window, include_splits=("test",))
    test = data["test"]
    truth = test["current"] + test["y"]
    predictions = {
        "Neural network": test["current"] + predict_delta(test["x"], arrays),
        "Ridge": test["current"] + predict_delta(test["x"], arrays, "ridge"),
        "Persistence": test["current"],
    }
    frame = pd.DataFrame({"flight_id": test["flight_id"], "anchor_timestamp_us": test["timestamp_us"],
                          "target_timestamp_us": test["target_timestamp_us"]})
    for name, values in {"measured": truth, "mlp": predictions["Neural network"],
                         "ridge": predictions["Ridge"], "persistence": predictions["Persistence"]}.items():
        for axis, column in enumerate(GYRO):
            frame[f"{name}_{column}"] = values[:, axis]

    # Prove that online push() reproduces every scored offline feature window.
    manifest = json.loads(dataset.read_text())
    checks: list[dict[str, object]] = []
    latencies: list[float] = []
    for flight in manifest["flights"]:
        if flight["split"] != "test":
            continue
        predictor = Predictor(model)
        streamed: dict[int, FloatArray] = {}
        for sample in read_log(dataset.parent / flight["path"]).to_dict("records"):
            begin = time.perf_counter_ns()
            result = predictor.push(sample)
            elapsed = (time.perf_counter_ns() - begin) / 1000
            if result["prediction_valid"]:
                streamed[int(result["timestamp_us"])] = np.array([result[f"predicted_{column}"] for column in GYRO])
                latencies.append(elapsed)
        mask = test["flight_id"] == flight["id"]
        actual = np.array([streamed[int(stamp)] for stamp in test["timestamp_us"][mask]])
        max_error = float(abs(actual - predictions["Neural network"][mask]).max())
        if max_error > 1e-5:
            raise ValueError("Streaming and offline predictions disagree")
        checks.append({"flight_id": flight["id"], "scored_windows": int(mask.sum()),
                       "streaming_predictions": len(streamed), "max_difference_rad_s": max_error})

    configs: list[dict[str, object]] = []
    summaries: dict[str, object] = {}
    all_values = np.stack([truth, *predictions.values()])
    domains = [(float(all_values[:, :, axis].min()), float(all_values[:, :, axis].max())) for axis in range(3)]
    for flight_id in np.unique(test["flight_id"]):
        mask = test["flight_id"] == flight_id
        times = test["target_timestamp_us"][mask]
        series = np.stack([truth[mask], *(predictions[name][mask] for name in SERIES[1:])], axis=1)
        selected = display_indices(times, truth[mask], predictions["Neural network"][mask], window.sample_period_us)
        groups = np.r_[0, np.cumsum(np.diff(times) != window.sample_period_us)]
        summaries[str(flight_id)] = {
            "windows": int(mask.sum()), "displayed_windows": len(selected),
            "mae_by_axis": {name: np.mean(abs(predictions[name][mask] - truth[mask]), axis=0).tolist()
                            for name in SERIES[1:]},
        }
        for axis, axis_name in enumerate(("Roll", "Pitch", "Yaw")):
            rows: list[list[float | None]] = []
            previous: int | None = None
            for index in selected.tolist():
                if previous is not None and groups[previous] != groups[index]:
                    rows.append([float(times[previous] + window.sample_period_us) / 1e6, None, None, None, None])
                rows.append([float(times[index]) / 1e6, *series[index, :, axis].tolist()])
                previous = index
            lo, hi = domains[axis]
            margin = max(.01, (hi - lo) * .06)
            configs.append({
                "id": f"{flight_id}-{axis}", "title": f"{str(flight_id).replace('_', ' ')} · {axis_name.lower()} rate",
                "height": 320,
                "description": "50 ms ahead, receiver-clock forecast. Gaps are excluded windows. Plot sampled; metrics and CSV use all valid windows.",
                "spec": {"type": "line", "x": "Target time", "y": list(SERIES),
                         "xLabel": "Target time from recording origin (s)", "yLabel": "Angular rate (rad/s)",
                         "startAtZero": False, "showLegend": True, "yDomain": [lo - margin, hi + margin]},
                "columns": ["Target time", *SERIES], "rows": rows,
            })
    summary: dict[str, object] = {
        "horizon_ms": window.horizon_us / 1000, "flights": summaries,
        "test_windows": len(test["x"]), "metrics": scored["metrics"],
        "weights_sha256": metadata["weights_sha256"],
        "streaming_checks": checks,
        "latency_scope": "host CPU Python push() only, offline replay; not onboard or FPGA timing",
        "latency_us": {"p50": float(np.median(latencies)), "p95": float(np.quantile(latencies, .95)),
                       "max": float(max(latencies))},
        "source_kinds": audit["source_kinds"],
    }
    def script_json(value: object) -> str:
        return json.dumps(value, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")
    blocks = "\n".join(f'<script type="application/json" data-viz-chart>{script_json(config)}</script>' for config in configs)
    template = Path(__file__).with_name("inspection.html").read_text()
    fragment = template.replace("__CHART_CONFIGS__", blocks).replace("__SUMMARY__", script_json(summary))
    if len(fragment.encode()) >= 1_000_000:
        raise ValueError("Inspection display exceeds the one-megabyte bound")
    output.mkdir(parents=True)
    frame.to_csv(output / "predictions.csv", index=False)
    (output / "inspection.html").write_text(fragment)
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    return {"output": str(output), "test_windows": len(test["x"]),
            "chart_count": len(configs), "streaming_matches_offline": True,
            "inspection_bytes": len(fragment.encode())}


def build_development_report(results: Path, output: Path) -> dict[str, object]:
    """Plot the measured per-recording comparison, checking the exported CSV."""
    if output.exists():
        raise ValueError(f"Refusing to overwrite inspection: {output}")
    summary = json.loads((results / "summary.json").read_text())
    if summary["status"] != "development_cross_validation_on_previously_exposed_data":
        raise ValueError("Expected the exposed-data development benchmark")
    frame = pd.read_csv(results / "predictions.csv")
    if len(frame) != summary["windows_scored_once_each"] or frame.duplicated(["flight_id", "timestamp_us"]).any():
        raise ValueError("Missing or repeated scored windows")
    truth = frame[[f"truth_{axis}_rad_s" for axis in "xyz"]].to_numpy()
    names = {"persistence": "Persistence", "mlp_original": "Original NN (retrained)",
             "mlp_shared": "Revised NN"}
    predictions = {key: frame[[f"{key}_{axis}_rad_s" for axis in "xyz"]].to_numpy() for key in names}
    for key, values in predictions.items():
        measured = metrics(truth, values, frame.flight_id.to_numpy(dtype=str))
        expected = summary["metrics"][key]
        if (not np.isclose(measured["mae"], expected["mae"], atol=1e-10, rtol=0)
                or any(not np.isclose(value["mae"], expected["by_flight"][flight]["mae"], atol=1e-10, rtol=0)
                       for flight, value in measured["by_flight"].items())):
            raise ValueError("Prediction export differs from recorded benchmark metrics")
    configs: list[dict[str, object]] = []
    readouts: dict[str, object] = {}
    flights = sorted(frame.flight_id.unique())
    upper = max(summary["metrics"][key]["by_flight"][flight]["mae"] for key in names for flight in flights) * 1.08
    for family in ("all", "chirp", "random", "square"):
        selected = [flight for flight in flights if family == "all" or flight.startswith(family + "_")]
        rows = [[f"{flight.split('_')[0].title()} {flight.rsplit('run', 1)[1]}",
                 *(summary["metrics"][key]["by_flight"][flight]["mae"] for key in names)] for flight in selected]
        mask = frame.flight_id.isin(selected).to_numpy()
        readouts[family] = {"windows": int(mask.sum()),
                            "revised": float(np.mean(abs(predictions["mlp_shared"][mask] - truth[mask]))),
                            "persistence": float(np.mean(abs(predictions["persistence"][mask] - truth[mask])))}
        configs.append({
            "id": family, "title": "Whole-recording prediction comparison",
            "description": "Mean absolute error across all three angular-rate axes. Each recording is scored by a model trained and early-stopped on other recordings. All recordings were previously exposed.",
            "height": max(300, 80 + 40 * len(selected)),
            "spec": {"type": "horizontalBar", "y": "Recording", "x": list(names.values()),
                     "xLabel": "Mean absolute error (rad/s)", "yLabel": "Recording",
                     "xDomain": [0, upper], "showLegend": True},
            "columns": ["Recording", *names.values()], "rows": rows,
        })

    def script_json(value: object) -> str:
        return json.dumps(value, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")

    blocks = "\n".join(f'<script type="application/json" data-viz-chart>{script_json(config)}</script>' for config in configs)
    template = Path(__file__).with_name("development.html").read_text()
    fragment = template.replace("__CHART_CONFIGS__", blocks).replace("__SUMMARY__", script_json(readouts))
    output.mkdir(parents=True)
    (output / "comparison.html").write_text(fragment)
    return {"output": str(output), "charts": len(configs), "csv_metrics_verified": True}

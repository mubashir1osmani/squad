"""Small residual MLP and ridge baseline; NumPy only, CPU execution."""
from __future__ import annotations

from typing import Literal, TypedDict

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.floating]
Arrays = dict[str, FloatArray]


class TrainingConfig(TypedDict):
    seed: int
    hidden_units: int
    epochs: int
    patience: int
    batch_size: int
    learning_rate: float
    l2: float
    ridge_penalty: float


def fit_scaling(x: FloatArray, y: FloatArray, target_scaling: str = "per_axis") -> Arrays:
    if target_scaling not in {"per_axis", "shared_zero_center"}:
        raise ValueError("Unknown residual scaling")
    shared = target_scaling == "shared_zero_center"
    return {
        "x_mean": x.mean(axis=0),
        "x_scale": np.maximum(x.std(axis=0), 1e-6),
        "y_mean": np.zeros(y.shape[1]) if shared else y.mean(axis=0),
        "y_scale": (np.full(y.shape[1], max(float(np.sqrt(np.mean(y ** 2))), 1e-6))
                    if shared else np.maximum(y.std(axis=0), 1e-6)),
    }


def normalized(x: FloatArray, y: FloatArray, scaling: Arrays) -> tuple[FloatArray, FloatArray]:
    return ((x - scaling["x_mean"]) / scaling["x_scale"],
            (y - scaling["y_mean"]) / scaling["y_scale"])


def forward(x: FloatArray, weights: Arrays) -> FloatArray:
    hidden = np.maximum(x @ weights["w1"] + weights["b1"], 0)
    return hidden @ weights["w2"] + weights["b2"]


def loss_and_gradients(
    x: FloatArray, y: FloatArray, w: Arrays, l2: float,
) -> tuple[float, Arrays]:
    z = x @ w["w1"] + w["b1"]
    h = np.maximum(z, 0)
    error = h @ w["w2"] + w["b2"] - y
    loss = np.mean(error ** 2) + l2 * (np.sum(w["w1"] ** 2) + np.sum(w["w2"] ** 2))
    d = 2 * error / error.size
    dh = (d @ w["w2"].T) * (z > 0)
    return float(loss), {
        "w1": x.T @ dh + 2 * l2 * w["w1"], "b1": dh.sum(axis=0),
        "w2": h.T @ d + 2 * l2 * w["w2"], "b2": d.sum(axis=0),
    }


def train_mlp(
    x: FloatArray, y: FloatArray, xv: FloatArray, yv: FloatArray,
    config: TrainingConfig, *, selection_metric: str = "normalized_mse",
    zero_output_init: bool = False,
) -> tuple[Arrays, list[dict[str, float | int]], int]:
    if selection_metric not in {"normalized_mse", "normalized_mae"}:
        raise ValueError("Unknown validation selection metric")
    rng = np.random.default_rng(config["seed"])
    width = config["hidden_units"]
    w = {
        "w1": rng.normal(0, np.sqrt(2 / x.shape[1]), (x.shape[1], width)),
        "b1": np.zeros(width),
        "w2": (np.zeros((width, y.shape[1])) if zero_output_init
               else rng.normal(0, .05, (width, y.shape[1]))),
        "b2": np.zeros(y.shape[1]),
    }
    m = {k: np.zeros_like(v) for k, v in w.items()}
    v = {k: np.zeros_like(a) for k, a in w.items()}

    def selection_loss(error: FloatArray) -> float:
        return float(np.mean(abs(error) if selection_metric == "normalized_mae" else error ** 2))

    best_loss = selection_loss(forward(xv, w) - yv) if zero_output_init else float("inf")
    best = {k: a.copy() for k, a in w.items()}
    best_epoch, step = 0, 0
    history: list[dict[str, float | int]] = []
    if zero_output_init:
        history.append({"epoch": 0, "train_normalized_mse": float(np.mean(y ** 2)),
                        "validation_normalized_mse": float(np.mean(yv ** 2)),
                        "validation_selection_loss": best_loss})
    for epoch in range(1, config["epochs"] + 1):
        order = rng.permutation(len(x))
        for start in range(0, len(x), config["batch_size"]):
            idx = order[start:start + config["batch_size"]]
            _, gradients = loss_and_gradients(x[idx], y[idx], w, config["l2"])
            step += 1
            for k, g in gradients.items():
                m[k] = .9 * m[k] + .1 * g
                v[k] = .999 * v[k] + .001 * g * g
                w[k] -= config["learning_rate"] * (m[k] / (1 - .9 ** step)) / (
                    np.sqrt(v[k] / (1 - .999 ** step)) + 1e-8)
        train_loss = float(np.mean((forward(x, w) - y) ** 2))
        error = forward(xv, w) - yv
        val_loss, selected_loss = float(np.mean(error ** 2)), selection_loss(error)
        if not np.isfinite(train_loss + val_loss):
            raise ValueError("Training diverged; no model saved")
        history.append({"epoch": epoch, "train_normalized_mse": train_loss,
                        "validation_normalized_mse": val_loss,
                        "validation_selection_loss": selected_loss})
        if selected_loss < best_loss - 1e-8:
            best, best_loss, best_epoch = {k: a.copy() for k, a in w.items()}, selected_loss, epoch
        if epoch - best_epoch >= config["patience"]:
            break
    return best, history, best_epoch


def fit_ridge(x: FloatArray, y: FloatArray, penalty: float) -> FloatArray:
    a = np.column_stack([x, np.ones(len(x))])
    regularizer = np.eye(a.shape[1]) * penalty
    regularizer[-1, -1] = 0
    return np.linalg.solve(a.T @ a + regularizer, a.T @ y)


def predict_delta(
    x: FloatArray, artifact: Arrays, mode: Literal["mlp", "ridge"] = "mlp",
) -> FloatArray:
    xn = (x - artifact["x_mean"]) / artifact["x_scale"]
    yn = (forward(xn, artifact) if mode == "mlp" else
          np.column_stack([xn, np.ones(len(xn))]) @ artifact["ridge"])
    return yn * artifact["y_scale"] + artifact["y_mean"]


def metrics(
    truth: FloatArray, prediction: FloatArray, flight_ids: NDArray[np.str_],
) -> dict[str, object]:
    e = prediction - truth
    by_flight = {}
    for flight in np.unique(flight_ids):
        mask = flight_ids == flight
        by_flight[str(flight)] = {"windows": int(mask.sum()),
                                 "mae": float(np.mean(abs(e[mask])))}
    return {
        "units": "rad/s", "mae": float(np.mean(abs(e))),
        "mae_by_axis": np.mean(abs(e), axis=0).tolist(),
        "rmse_by_axis": np.sqrt(np.mean(e * e, axis=0)).tolist(),
        "p95_absolute_error_by_axis": np.quantile(abs(e), .95, axis=0).tolist(),
        "by_flight": by_flight,
    }

"""Run from project root: python -m nn --help."""
import argparse
import json
from pathlib import Path
import sys

from . import fixture, importer, pipeline
from .data import load_dataset
from .inference import replay


def main(argv=None):
    p = argparse.ArgumentParser(description="Observation-only flight-response experiments")
    commands = p.add_subparsers(dest="command", required=True)
    f = commands.add_parser("fixture", help="Generate synthetic software fixtures, NOT PX4/real flights")
    f.add_argument("--output", type=Path, required=True)
    f.add_argument("--samples", type=int, default=1600)
    f.add_argument("--seed", type=int, default=20261111)
    for name in ("audit", "train", "evaluate"):
        s = commands.add_parser(name)
        s.add_argument("--dataset", type=Path, required=True)
        if name in ("audit", "train"):
            s.add_argument("--config", type=Path, default=pipeline.DEFAULT_CONFIG)
        if name in ("train", "evaluate"):
            s.add_argument("--output", type=Path, required=True)
        if name == "evaluate":
            s.add_argument("--model", type=Path, required=True)
            s.add_argument("--split", choices=["validation", "test"], default="validation")
            s.add_argument("--acknowledge-test", action="store_true")
    r = commands.add_parser("replay", help="Offline Python CPU inference, no vehicle commands")
    for name in ("model", "input", "output"):
        r.add_argument(f"--{name}", type=Path, required=True)
    i = commands.add_parser("import-csv", help="Import explicitly mapped asynchronous IMU/motor CSVs")
    for name in ("imu", "motors", "mapping", "output"):
        i.add_argument(f"--{name}", type=Path, required=True)
    i.add_argument("--flight-id", required=True)
    i.add_argument("--group-id", required=True)
    i.add_argument("--split", choices=["train", "validation", "test"], required=True)
    i.add_argument("--source-kind", choices=["px4_sitl", "real_flight"], required=True)
    a = commands.add_parser("assemble", help="Assemble audited run.json records without sample-level splitting")
    a.add_argument("--runs", nargs="+", type=Path, required=True)
    a.add_argument("--output", type=Path, required=True)
    native = commands.add_parser("import-idsia", help="Import approved local native logs; no downloads")
    native.add_argument("--source", type=Path, required=True)
    native.add_argument("--config", type=Path, default=Path(__file__).parent / "configs/idsia-v1.json")
    native.add_argument("--output", type=Path, required=True)
    view = commands.add_parser("inspect", help="Export final-test prediction plots, CSV and streaming parity checks")
    for name in ("dataset", "model", "evaluation", "output"):
        view.add_argument(f"--{name}", type=Path, required=True)
    benchmark = commands.add_parser("benchmark", help="Whole-recording IDSIA development comparison; no downloads")
    benchmark.add_argument("--dataset", type=Path, required=True)
    benchmark.add_argument("--config", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, required=True)
    comparison = commands.add_parser("inspect-development", help="Compare scored flight errors from an existing development benchmark")
    comparison.add_argument("--results", type=Path, required=True)
    comparison.add_argument("--output", type=Path, required=True)
    forecast_train = commands.add_parser("forecast-train", help="Train Keras with the PyTorch backend")
    for name in ("dataset", "config", "output"):
        forecast_train.add_argument(f"--{name}", type=Path, required=True)
    forecast_replay = commands.add_parser("forecast-replay", help="Predict future rates/states from causal snapshots")
    for name in ("model", "input", "output"):
        forecast_replay.add_argument(f"--{name}", type=Path, required=True)
    forecast_evaluate = commands.add_parser("forecast-evaluate", help="Score a frozen Keras/PyTorch forecast")
    for name in ("dataset", "model", "output"):
        forecast_evaluate.add_argument(f"--{name}", type=Path, required=True)
    forecast_evaluate.add_argument("--split", choices=["validation", "test"], default="validation")
    forecast_evaluate.add_argument("--acknowledge-test", action="store_true")
    export = commands.add_parser("groundstation-export", help="Emit causal Keras forecasts as ground-station JSONL; observation only")
    export.add_argument("--model", type=Path, required=True)
    export.add_argument("--input", type=Path, required=True)
    export.add_argument("--output", required=True, help="JSONL file or - for stdout")
    export.add_argument("--source-kind", choices=["real_flight", "px4_sitl", "software_fixture", "unknown"], required=True)
    export.add_argument("--metadata", type=Path, help="Source manifest/metadata; defaults to input directory's dataset.json")
    export.add_argument("--model-id")
    export.add_argument("--pace", action="store_true", help="Pace source timestamps against monotonic time for a live capture pipe")
    export.add_argument("--speed", type=float, default=1.0, help="Positive source-clock playback multiplier")
    export.add_argument("--source-now-us", type=int, help="Optional current source clock; reject anchors older than the model freshness limit")
    demo = commands.add_parser("groundstation-demo", help="Train a deterministic synthetic trajectory model and export labelled JSONL")
    demo.add_argument("--output", type=Path, required=True)
    demo.add_argument("--samples", type=int, default=600)
    demo.add_argument("--epochs", type=int, default=12)
    args = p.parse_args(argv)
    try:
        if args.command == "fixture":
            result = fixture.generate(args.output, args.samples, args.seed)
            result = {"dataset": str(args.output / "dataset.json"), "flights": len(result["flights"]),
                      "source_kind": "software_fixture", "warning": result["warning"]}
        elif args.command == "audit":
            _, window = pipeline.read_config(args.config)
            data, result = load_dataset(args.dataset, window)
            result["window_counts"] = {k: len(v["x"]) for k, v in data.items()}
        elif args.command == "train":
            metadata, validation = pipeline.train(args.dataset, args.output, args.config)
            result = {"output": str(args.output), "status": metadata["status"],
                      "parameters": metadata["parameter_count"], "best_epoch": metadata["best_epoch"],
                      "validation_mae_rad_s": {k: v["mae"] for k, v in validation.items()},
                      "test_evaluated": False}
        elif args.command == "evaluate":
            result = pipeline.evaluate(args.dataset, args.model, args.split, args.output, args.acknowledge_test)
        elif args.command == "replay":
            result = replay(args.model, args.input, args.output)
        elif args.command == "import-csv":
            result = importer.import_csv(args.imu, args.motors, args.mapping, args.output,
                                         args.flight_id, args.group_id, args.split, args.source_kind)
        elif args.command == "assemble":
            result = importer.assemble(args.runs, args.output)
        elif args.command == "import-idsia":
            from .idsia_import import import_local
            result = import_local(args.source, args.config, args.output)
        elif args.command == "inspect":
            from .report import build_report
            result = build_report(args.dataset, args.model, args.evaluation, args.output)
        elif args.command == "benchmark":
            from .benchmark import run_benchmark
            result = run_benchmark(args.dataset, args.config, args.output)
        elif args.command == "inspect-development":
            from .report import build_development_report
            result = build_development_report(args.results, args.output)
        elif args.command == "groundstation-export":
            from .groundstation_export import export
            result = export(args.model, args.input, args.output, args.source_kind,
                            args.metadata, args.model_id, args.pace, args.speed, args.source_now_us)
        elif args.command == "groundstation-demo":
            from .groundstation_demo import run
            result = run(args.output, args.samples, args.epochs)
        elif args.command.startswith("forecast-"):
            from . import forecast
            if args.command == "forecast-train":
                result = forecast.train(args.dataset, args.output, args.config)
            elif args.command == "forecast-replay":
                result = forecast.replay(args.model, args.input, args.output)
            else:
                result = forecast.evaluate(args.dataset, args.model, args.split, args.output, args.acknowledge_test)
        print(json.dumps(result, indent=2, allow_nan=False),
              file=sys.stderr if args.command == "groundstation-export" and args.output == "-" else sys.stdout)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

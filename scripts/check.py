#!/usr/bin/env python3
"""Build/test host software and preserve real cross-component evaluation reports."""

import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
MANIFEST = ROOT / "groundstation" / "Cargo.toml"


def run(*args, expected=0):
    command = [str(arg) for arg in args]
    print("+ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != expected:
        raise RuntimeError(
            f"{command[0]} exited {result.returncode}; expected {expected}"
        )


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_report(path):
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if "=" in line
    )


def check_integration(groundstation):
    parent = BUILD / "integration"
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    print(f"Integration evidence: {output}", flush=True)
    sim = BUILD / "simulation" / "quad-sim"
    vision = BUILD / "vision" / "quad-vision"
    scenarios = (
        "nominal", "translation_cardinal", "low_texture", "image_noise",
        "dropped_frame", "frozen_frames", "command_stale_gap",
    )
    results = []
    for scenario in scenarios:
        directory = output / scenario
        run(sim, "generate", "--scenario", scenario, "--seed", 1,
            "--output", directory)
        run(groundstation, "replay", "--input", directory / "commands.csv",
            "--output", directory / "commands-replay.csv",
            "--events", directory / "gaps.csv")
        run(sim, "check-schema", "--commands", directory / "commands-replay.csv")
        with (directory / "gaps.csv").open(newline="") as stream:
            gaps = list(csv.DictReader(stream))
        require(len(gaps) == (1 if scenario == "command_stale_gap" else 0),
                f"Unexpected command gaps in {scenario}")
        record = {"scenario": scenario, "command_replay": "pass"}
        if scenario == "command_stale_gap":
            require(gaps[0]["t_us"] == "700000" and
                    "680000" in gaps[0]["detail"], "Stale gap was not preserved")
        else:
            run(vision, "--manifest", directory / "manifest.csv",
                "--output", directory / "flow-vision.csv")
            noisy = scenario == "image_noise"
            run(sim, "evaluate", "--truth", directory / "flow.csv",
                "--estimate", directory / "flow-vision.csv",
                "--output", directory / "evaluation.txt", expected=3 if noisy else 0)
            report = read_report(directory / "evaluation.txt")
            if noisy:
                expected = {
                    "inputs_well_formed": "1", "truth_rows": "6",
                    "estimate_rows": "6", "matched_rows": "6",
                    "truth_valid_rows": "6", "covered_valid_rows": "2",
                    "valid_coverage": "0.333333", "missed_valid_count": "4",
                    "false_valid_count": "0", "valid_truth_missing": "0",
                    "missing_truth_records": "0", "unmatched_estimate_records": "0",
                    "max_displacement_error_px": "0.000000", "overall_success": "0",
                }
                require(all(report.get(key) == value for key, value in expected.items()),
                        "Noisy-motion outcome changed; inspect and update its documented limitation")
                record["vision"] = "known_limitation"
                print("KNOWN LIMITATION: image_noise rejects 4/6 valid motions", flush=True)
                if os.environ.get("GITHUB_ACTIONS") == "true":
                    print("::warning::Known vision limitation: image_noise rejects 4/6 valid motions")
            else:
                require(report.get("overall_success") == "1",
                        f"Evaluation did not succeed for {scenario}")
                record["vision"] = "pass"
            record["evaluation"] = report
        results.append(record)
    (output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"Reports saved to {output.relative_to(ROOT)}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gamepad", action="store_true", help="Also test optional gilrs support")
    parser.add_argument("--strict-noise", action="store_true",
                        help="Return failure for the documented noisy-motion limitation")
    options = parser.parse_args()
    for component in ("simulation", "vision"):
        directory = BUILD / component
        configure = ["cmake", "-S", ROOT / component, "-B", directory,
                     "-DCMAKE_BUILD_TYPE=Release"]
        if component == "vision":
            configure.append("-DQUAD_VISION_SANITIZERS=ON")
        run(*configure)
        run("cmake", "--build", directory, "--parallel", 2)
        run("ctest", "--test-dir", directory, "--output-on-failure")

    run("cargo", "fmt", "--manifest-path", MANIFEST, "--check")
    configurations = [()] + ([("--features", "gamepad")] if options.gamepad else [])
    for features in configurations:
        run("cargo", "test", "--locked", "--manifest-path", MANIFEST, *features)
        run("cargo", "clippy", "--locked", "--manifest-path", MANIFEST,
            "--all-targets", *features, "--", "-D", "warnings")
    run("cargo", "build", "--locked", "--manifest-path", MANIFEST)
    # Cargo metadata respects CARGO_TARGET_DIR and local Cargo configuration.
    metadata = json.loads(subprocess.check_output(
        ["cargo", "metadata", "--locked", "--no-deps", "--format-version", "1",
         "--manifest-path", str(MANIFEST)], cwd=ROOT, text=True))
    check_integration(Path(metadata["target_directory"]) / "debug" / "quad-groundstation")
    if options.strict_noise:
        print("FAIL: strict mode includes the known image_noise limitation", file=sys.stderr)
        return 1
    print("Host checks passed; integration reproduced one documented vision limitation.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)

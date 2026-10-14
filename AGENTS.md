# Contributor guidance

- Read README.md, PLAN.md, and docs/interfaces.md before changing component behavior.
- Use Rust for groundstation and C++17 for vision/simulation. Keep components independently buildable.
- Preserve existing work. Do not reset, clean, force-push, or modify unrelated projects.
- Do not spawn additional agents unless the user explicitly requests delegation.
- Fetch normal project dependencies as needed; do not install global packages, change shell configuration, or download large simulator/vendor runtimes without a task that requires them.
- Network control remains localhost-only and explicitly enabled. Do not add automatic arming or takeoff to this milestone.
- Distinguish host tests, real controller tests, PX4 SITL, HLS synthesis, FPGA measurements, and flight validation. Claim only checks actually performed.
- Keep fixtures deterministic and validate malformed data, timestamps, ambiguity, and missing measurements. Invalid flow is not a confident stationary measurement.
- Run `python3 scripts/check.py` after software changes; include `--gamepad` when touching controller support and its dependencies are available.
- The noisy search-edge case is a documented limitation. Do not weaken truth, drop failing rows, or silently mark it successful. If fixing it, update the regression expectations and verification documentation together.
- Keep component STATUS.md reports and public documentation accurate. Do not commit local session notes, credentials, build outputs, or virtual environments.

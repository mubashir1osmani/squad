# FPGA-assisted quadcopter project plan

Created: 2026-10-11

Updated: 2026-10-12. See [SCOPE.md](SCOPE.md) for the complete DIY build, current budget, missing parts, and delivery gates. It supersedes the earlier development-kit purchasing assumptions.

## Objective

Build a PS4-controlled quadcopter using PX4 for stabilization and an onboard FPGA for camera motion processing. Progress from simulation and a desktop vision prototype to manually controlled flight, onboard measurement, and vision-assisted hovering. This is separate from the CanSat project.

Languages: C++ for vision and replay tools; Rust for ground-station software. C++ HLS is the intended FPGA path, subject to board and camera selection. HDL integration may be needed. No neural network is required for the first version.

Current planning allowances, excluding the FPGA and before tax/shipping: CAD 350–605 for manual flight plus onboard camera/FPGA integration hardware, or CAD 235–415 with a suitable radio set and charger borrowed. Range sensing for assisted hover adds CAD 20–40. These assume an existing laptop, PS4 controller, printer access, and basic tools; exact compatibility and the user's spending cap remain unresolved. See SCOPE.md for caveats and itemized costs. Purchases are staged; no hardware purchases are authorized by this plan.

## Phase 1: Simulation and PS4 control

### 1.1 Simulation environment
- Document the supported PX4 SITL setup and simulator prerequisites using official references.
- Supply repeatable local synthetic fixtures for development without a full simulator.
- Distinguish a synthetic replay test from an actual PX4 simulator run.

### 1.2 Controller and commands
- Implement a Rust command application with deterministic CSV replay input.
- Provide optional real gamepad input, stick mapping, dead zones, and enable/deadman handling.
- Implement an explicit opt-in localhost MAVLink MANUAL_CONTROL path for PX4 SITL.
- Validate axis mapping, normalization, input freshness, disconnection, and invalid values.
- Do not automatically arm or request takeoff. The operator configures and arms SITL separately.

### 1.3 Recording and inspection
- Record normalized commands and supported telemetry with timestamps.
- Provide a repeatable software-only demo and tests.

Exit criterion: reproducible command replay and loopback transport tests pass. Real PS4 and PX4 SITL behavior are separately reported as verified or pending, depending on available equipment/runtime.

## Phase 2: FPGA vision prototype

### 2.1 Hardware and toolchain selection
- Select an FPGA board, camera interface, power supply, and supported development OS together.
- This remains pending; do not claim a hardware-specific design, resource count, or timing result.

### 2.2 C++ reference
- Implement a dependency-light, causal frame-pair optical-flow reference for textured surfaces.
- Use PGM image sequences and deterministic fixtures before introducing a camera dependency.
- Report displacement, interval, quality, and valid/invalid status using docs/interfaces.md.
- Validate translation direction, subregions/search bounds, low texture, and invalid input.

### 2.3 Hardware-oriented implementation
- Isolate a bounded, allocation-free C++ processing kernel suitable for future HLS work.
- Compare the kernel to the reference and report host measurements accurately.
- Document the remaining HLS, camera, buffering, and timing work. Host compilation is not synthesis.

Exit criterion: known synthetic image translations are measured correctly, ambiguous frames are rejected, and the reference/kernel comparison is repeatable.

## Phase 3: Aircraft assembly and manual flight

### 3.1 Hardware assembly
- Freeze a compatible DIY bill of materials; the generic DIY 400 geometry is a prototype, not the final airframe.
- Complete the missing landing gear, camera mount, board-specific mounts, battery cradle, cover, and wiring details before the final print release.
- Assemble a supported flight controller, matched motors/ESCs/propellers, physical command/data link, dedicated RC link, battery, charger, and regulated FPGA power. GPS is outside the initial build scope.
- Measure printed-part mass and verify fit, stiffness, and joint behavior before selecting the final flight structure. Use a documented hybrid arm revision if needed.

### 3.2 Configuration
- Calibrate sensors and configure the aircraft and connection-loss behavior.
- Verify motor ordering/direction with propellers removed.

### 3.3 Manual flight
- Establish reliable flight with the dedicated RC controller.
- Then validate the PS4/ground-station command path with the flight controller.

Exit criterion: stable manual flight and usable logs. Hardware and flight tests are outside the desktop milestone.

## Phase 4: Onboard integration and measurement

### 4.1 Installation
- Add FPGA, downward camera, range sensor, regulated power, and mounts; check balance, power demand, and payload.

### 4.2 Interfaces
- Calibrate camera intrinsics, mounting orientation, frame timing, range alignment, and units.
- Adapt pixel displacement into the optical-flow interface required by the selected PX4 version.
- Do not treat pixel displacement as velocity or position without the required calibration and sensor data.

### 4.3 Measurement flights
- Log FPGA estimates without using them for control initially.
- Evaluate texture, lighting, vibration, latency, dropped frames, and range validity.

Exit criterion: validated onboard measurements with documented error and failure conditions.

## Phase 5: Assisted hover and limited autonomy

### 5.1 Estimator integration
- Connect validated flow/range measurements to PX4's supported sensor interfaces.

### 5.2 Assisted hold
- Test manual-to-assisted mode transitions and short-duration hovering under appropriate conditions.
- Optical flow does not establish drift-free absolute position or obstacle avoidance.

### 5.3 Autonomous sequence
- Add and test a bounded takeoff/hover/land sequence only after the preceding hardware milestones.

Exit criterion: measured assisted-hover performance and repeatable bounded autonomous behavior.

## Software components

The first desktop milestone includes Rust ground-station software, a C++ vision kernel/reference, and a C++ fixture generator/evaluator. Each component remains independently buildable; shared contracts live in docs/interfaces.md. Run the root check script for cross-component validation.

## Completion requirements

- Working implementations, not placeholder-only scaffolds.
- Document exact build/test/demo commands and prerequisites.
- Exercise malformed input, timing, and relevant failure cases.
- No claim of actual flight, real gamepad validation, full SITL validation, HLS synthesis, or FPGA deployment without running that check.
- Preserve other sessions' work. No broad refactors, force operations, or unrelated directory changes.

## Progress

- [x] Create separate project directory and initial plan.
- [x] Define software ownership and shared contracts.
- [x] Ground-station implementation and component checks.
- [x] Vision implementation and component checks.
- [x] Simulation/replay implementation and component checks.
- [x] Review software reports and run cross-component checks; see docs/verification.md.
- [ ] Resolve or explicitly constrain the noisy-image search-boundary limitation found in integration (4/6 valid motions rejected in the image_noise fixture).
- [x] Scope the complete DIY aircraft, missing print parts, software work, and cost assumptions in SCOPE.md.
- [ ] Confirm budget, FPGA/camera/toolchain, printer access, and supported flight-controller choice.
- [ ] Finish the hardware-specific CAD, mounts, landing gear, and wiring/BOM package.
- [ ] Hardware selection and procurement.
- [ ] Physical integration and flight phases.

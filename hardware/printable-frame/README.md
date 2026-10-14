# DIY 400 modular quadcopter frame — prototype v0.1

Created 2026-10-12. Dimensions are **millimetres**. This is an original printable fit-and-assembly prototype, not a structurally qualified or flight-tested airframe. Motors, FPGA board, battery, print material, loads, and operating conditions are not selected. Do not treat mesh validation as evidence of flightworthiness.

## Files to print

| STL | Quantity | Nominal footprint / height |
|---|---:|---|
| `01_bottom_plate_print_1.stl` | 1 | 120 × 120 × 3 mm |
| `02_top_clamp_print_1.stl` | 1 | 120 × 120 × 3 mm |
| `03_arm_print_4.stl` | 4 | 178 × 36 × 8 mm |
| `04_electronics_tray_print_1.stl` | 1 | 130 × 110 × 3 mm |
| `05_spacer_15mm_print_4.stl` | 4 | 8 mm diameter × 15 mm |

`06_optional_four_arm_print_plate.stl` contains four arms already laid flat in a 178 × 162 mm footprint. Use it **instead of** printing four copies of the individual arm. It intentionally contains four separate watertight solids.

All parts fit individually on a 220 × 220 mm bed, with space for a modest brim. Slicer auto-arrange/centre the parts. Leave scale at 100%; STL does not embed physical units, so select millimetres. Every part is supplied with its flat bottom at Z=0. The assembly GLB and SCAD files are previews, not printable assembled models.

## Frame dimensions and fit

- X layout; 400 mm diagonal distance between opposite motor centres.
- Adjacent motor centres are approximately 282.84 mm apart. Nominal 10-inch propeller discs would have 28.84 mm planar separation, but this is only a geometry calculation, not a motor/propeller recommendation or clearance validation under flex.
- Arm begins 40 mm radially from the centre, with motor centre 160 mm along the local arm axis.
- Each arm has two 3.4 mm root holes at local X=12 and X=30 mm, Y=0. Matching bottom/top plate holes clamp the arm in an 18 mm bolt spacing.
- Motor pads: 36 mm diameter, 8 mm thickness, 8 mm central opening. Four 3.4 mm diagonal slots span square **16 × 16 to 19 × 19 mm** mounting patterns. They do **not** promise compatibility with rectangular or cross-pattern motor mounts. Check the actual motor drawing and screw engagement before use.
- Bottom plate has 30.5 × 30.5 mm M3-clearance and 20 × 20 mm M2-clearance patterns for a future controller adapter, plus two 4 mm wide battery-strap slots. No specific controller is guaranteed to fit.
- Electronics tray is 130 × 110 mm with a central opening, M3 adjustment slots, and two 4 mm strap slots. Use insulating board standoffs or a board-specific adapter; do not assume the tray matches an unknown FPGA board.
- Tray support-hole grid: 90 × 40 mm. Four 15 mm spacers raise it above the clamp plate. Matching bottom-plate clearance holes accommodate protruding tray-bolt ends.

## Assembly

1. Put the bottom plate flat. Place four arms above it, pointing out at 45°, 135°, 225°, and 315°; align the two root holes on each arm with the plate.
2. Place the top clamp above the arm roots. Clamp the sandwich with eight M3 bolts, washers, and locknuts. Plate/arm/plate thickness is 14 mm; M3×20–25 is a starting selection, subject to washer and nut dimensions.
3. Attach the electronics tray through the four 15 mm spacers. The tray/spacer/clamp stack is 21 mm; four M3×30 bolts, washers, and locknuts are a starting selection. Spacers have through holes, not printed threads.
4. Mount electronics on insulated standoffs. Route cables away from rotating parts and secure the battery using the lower strap slots.

Motor screws are a separate selection: account for the 8 mm pad and the motor manufacturer's permitted thread engagement. Screws must not contact windings. Motors, motor screws, battery straps, electronics standoffs, and landing gear are not included in the printed design.

## First print

Print **one arm first** to check motor fit and print quality before committing to the complete frame. A PLA fit prototype is reasonable; it does not qualify the material for flight. PETG/nylon and the actual print process need evaluation for temperature, vibration, stiffness, impact, creep, and layer adhesion. A 0.2 mm layer height, 5–6 walls, and generous top/bottom layers can be a starting point for fit prototypes, but no infill recipe here is a structural guarantee. These flat extrusions need no geometric supports; small spacers may benefit from a brim.

Manufactured propellers are required; there are no printable propellers in this bundle. The present design also has no propeller guards or validated landing gear. Continue with bench fit and structural testing before considering powered flight.

## What was checked

The generator exports real binary STLs and reloads them to check closed/watertight meshes, consistent winding, positive volume, finite coordinates, expected component counts, flat Z=0 placement, and bed footprint. Assembly solids are checked for positive-volume intersections. See `mesh_validation.json` for the measured geometry and full-density volume estimate.

No strength, fatigue, vibration, thrust, flight, slicer/toolpath, or physical-print validation has been performed. Fastener tolerances and actual printed holes must be checked on your printer.

## Edit and regenerate

`generate_frame.py` is the parametric source. Install the listed Python dependencies in a local environment and run it. Parameters at the top control motor radius, plate thickness, arm thickness, clearance holes, and tray spacing. Some dependent dimensions remain explicit in `make_parts`; review mating geometry after changes. `assembly_view.scad` provides an OpenSCAD assembly view by importing the generated STLs.

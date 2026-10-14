// Dimensions in mm. Geometry is parameterized in generate_frame.py.
// Assembly VIEW ONLY. Slice the individual STL files, not this assembly.
color("SlateGray") import("stl/01_bottom_plate_print_1.stl");
for(a=[45,135,225,315])
  color("LightSeaGreen") rotate([0,0,a]) translate([40,0,3])
    import("stl/03_arm_print_4.stl");
color("SlateGray") translate([0,0,11]) import("stl/02_top_clamp_print_1.stl");
for(x=[-45,45]) for(y=[-20,20])
  color("SlateGray") translate([x,y,14]) import("stl/05_spacer_15mm_print_4.stl");
color("Goldenrod") translate([0,0,29]) import("stl/04_electronics_tray_print_1.stl");

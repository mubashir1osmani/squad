"""Parametric millimetre-scale frame prototype; no structural or flight validation."""
from pathlib import Path
import json
import math
import zipfile

import manifold3d as md
import numpy as np
import trimesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "stl"
OUT.mkdir(exist_ok=True)
MOTOR_RADIUS = 200.0
ARM_START_RADIUS = 40.0
ARM_MOTOR_X = MOTOR_RADIUS - ARM_START_RADIUS
PLATE_T = 3.0
ARM_T = 8.0
TRAY_T = 3.0
SPACER_H = 15.0
M3_CLEARANCE = 3.4


def poly(points):
    return md.CrossSection([np.asarray(points, dtype=float)])


def circle(x, y, radius):
    return md.CrossSection.circle(radius, 96).translate((x, y))


def rounded_rect(cx, cy, w, h, r):
    corners = [(cx + w/2-r, cy + h/2-r, 0),
               (cx - w/2+r, cy + h/2-r, 90),
               (cx - w/2+r, cy - h/2+r, 180),
               (cx + w/2-r, cy - h/2+r, 270)]
    points = []
    for x, y, start in corners:
        for angle in np.linspace(start, start+90, 17):
            rad = math.radians(angle)
            points.append((x+r*math.cos(rad), y+r*math.sin(rad)))
    return poly(points)


def slot(a, b, diameter):
    a, b = np.asarray(a, float), np.asarray(b, float)
    axis = b-a
    length = float(np.linalg.norm(axis))
    if length == 0:
        return circle(*a, diameter/2)
    side = np.array([-axis[1], axis[0]]) / length * diameter/2
    return (circle(*a, diameter/2) + circle(*b, diameter/2)
            + poly([a-side, b-side, b+side, a+side]))


def hub_outline():
    s, c = 60.0, 14.0
    return poly([(-s+c,-s),(s-c,-s),(s,-s+c),(s,s-c),
                 (s-c,s),(-s+c,s),(-s,s-c),(-s,-s+c)])


def arm_bolt_centres():
    return [(r*math.cos(math.radians(a)), r*math.sin(math.radians(a)))
            for a in [45,135,225,315] for r in [52,70]]


def make_parts():
    bottom = hub_outline()
    top = hub_outline() - rounded_rect(0,0,64,64,6)
    for x,y in arm_bolt_centres():
        bottom -= circle(x,y,M3_CLEARANCE/2)
        top -= circle(x,y,M3_CLEARANCE/2)
    for x in [-45,45]:
        bottom -= slot((x,-12),(x,12),4)
    for side,diam in [(30.5,3.4),(20,2.3)]:
        for x in [-side/2,side/2]:
            for y in [-side/2,side/2]:
                bottom -= circle(x,y,diam/2)
    for x in [-45,45]:
        for y in [-20,20]:
            top -= circle(x,y,M3_CLEARANCE/2)
            # Clearance for long tray bolts that project below the upper clamp.
            bottom -= circle(x,y,M3_CLEARANCE/2)

    arm = (rounded_rect(22.5,0,45,26,5)
           + poly([(30,-13),(ARM_MOTOR_X,-10),(ARM_MOTOR_X,10),(30,13)])
           + circle(ARM_MOTOR_X,0,18))
    for x in [12,30]:
        arm -= circle(x,0,M3_CLEARANCE/2)
    arm -= circle(ARM_MOTOR_X,0,4)
    # Four radial slots support square 16x16 through 19x19 motor patterns.
    for sx in [-1,1]:
        for sy in [-1,1]:
            arm -= slot((ARM_MOTOR_X+sx*8,sy*8),
                        (ARM_MOTOR_X+sx*9.5,sy*9.5),M3_CLEARANCE)

    tray = rounded_rect(0,0,130,110,6) - rounded_rect(0,0,64,42,5)
    for x in [-45,45]:
        for y in [-20,20]:
            tray -= circle(x,y,M3_CLEARANCE/2)
    for x in [-54,54]:
        tray -= slot((x,-13),(x,13),4)
    for y in [-40,40]:
        for x1,x2 in [(-44,-10),(10,44)]:
            tray -= slot((x1,y),(x2,y),3.4)

    spacer = circle(0,0,4)-circle(0,0,M3_CLEARANCE/2)
    return {
        "01_bottom_plate_print_1": bottom.extrude(PLATE_T),
        "02_top_clamp_print_1": top.extrude(PLATE_T),
        "03_arm_print_4": arm.extrude(ARM_T),
        "04_electronics_tray_print_1": tray.extrude(TRAY_T),
        "05_spacer_15mm_print_4": spacer.extrude(SPACER_H),
    }


def to_trimesh(solid):
    assert solid.status() == md.Error.NoError, solid.status()
    m = solid.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(m.vert_properties)[:,:3],
                           faces=np.asarray(m.tri_verts), process=True)


def export_validate(name,solid,expected_components=1):
    path = OUT / (name+".stl")
    mesh = to_trimesh(solid)
    mesh.export(path, file_type="stl")
    loaded = trimesh.load_mesh(path, process=True)
    parts = loaded.split(only_watertight=False)
    assert loaded.is_watertight, name
    assert loaded.is_winding_consistent, name
    assert loaded.volume > 0, name
    assert len(parts) == expected_components, (name,len(parts))
    assert all(p.is_watertight and p.volume > 0 for p in parts), name
    assert np.isfinite(loaded.vertices).all(), name
    assert loaded.bounds[0,2] > -0.001, name
    assert max(loaded.extents[:2]) <= 220, (name,loaded.extents)
    return {"file":path.name,"watertight":True,"winding_consistent":True,
            "connected_components":len(parts),"triangles":len(loaded.faces),
            "dimensions_mm":np.round(loaded.extents,3).tolist(),
            "solid_volume_cm3":round(float(loaded.volume)/1000,3)}


def assembled(parts):
    keys = list(parts)
    items = [("Bottom plate",parts[keys[0]],"#293a48")]
    for angle in [45,135,225,315]:
        solid = parts[keys[2]].translate((ARM_START_RADIUS,0,PLATE_T)).rotate((0,0,angle))
        items.append((f"Arm {angle}",solid,"#239b98"))
    top_z = PLATE_T + ARM_T
    items.append(("Top clamp",parts[keys[1]].translate((0,0,top_z)),"#405464"))
    for x in [-45,45]:
        for y in [-20,20]:
            solid = parts[keys[4]].translate((x,y,top_z+PLATE_T))
            items.append(("Spacer",solid,"#455a68"))
    items.append(("Electronics tray",parts[keys[3]].translate((0,0,top_z+PLATE_T+SPACER_H)),"#e4a349"))
    # Intersections may touch at mating faces but must have no positive solid volume.
    for i,(_,a,_) in enumerate(items):
        for _,b,_ in items[i+1:]:
            assert abs((a^b).volume()) < 1e-4, "Assembly collision"
    return items


def rasterize(items, width=1400, height=1080):
    """Orthographic z-buffer avoids painter-order artifacts on thin CAD parts."""
    direction=np.array([0.46,-0.66,0.59]);direction/=np.linalg.norm(direction)
    right=np.cross([0.,0.,1.],direction);right/=np.linalg.norm(right)
    up=np.cross(direction,right)
    basis=np.stack([right,up,direction],axis=1)
    meshes=[(to_trimesh(solid),color) for _,solid,color in items]
    all_points=np.concatenate([mesh.vertices@basis for mesh,_ in meshes])
    bounds=np.array([all_points[:,:2].min(axis=0),all_points[:,:2].max(axis=0)])
    scale=min((width-100)/(bounds[1,0]-bounds[0,0]),
              (height-100)/(bounds[1,1]-bounds[0,1]))
    center=bounds.mean(axis=0)
    canvas=np.full((height,width,3),[246,244,239],dtype=np.uint8)
    depths=np.full((height,width),-np.inf)
    light=np.array([-0.4,-0.5,1.]);light/=np.linalg.norm(light)
    for mesh,color in meshes:
        coords=mesh.vertices@basis
        coords[:,0]=(coords[:,0]-center[0])*scale+width/2
        coords[:,1]=height/2-(coords[:,1]-center[1])*scale
        base=np.array(matplotlib.colors.to_rgb(color))*255
        for face,normal in zip(mesh.faces,mesh.face_normals):
            tri=coords[face]
            x0=max(0,int(np.floor(tri[:,0].min())))
            x1=min(width-1,int(np.ceil(tri[:,0].max())))
            y0=max(0,int(np.floor(tri[:,1].min())))
            y1=min(height-1,int(np.ceil(tri[:,1].max())))
            if x1<x0 or y1<y0:continue
            a,b,c=tri
            denom=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
            if abs(denom)<1e-9:continue
            yy,xx=np.mgrid[y0:y1+1,x0:x1+1]
            xx=xx+.5;yy=yy+.5
            wa=((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/denom
            wb=((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/denom
            wc=1-wa-wb
            z=wa*a[2]+wb*b[2]+wc*c[2]
            view=depths[y0:y1+1,x0:x1+1]
            mask=(wa>=-1e-8)&(wb>=-1e-8)&(wc>=-1e-8)&(z>view)
            view[mask]=z[mask]
            shade=.48+.52*max(0.,float(np.dot(normal,light)))
            canvas[y0:y1+1,x0:x1+1][mask]=np.clip(base*shade,0,255).astype(np.uint8)
    return canvas


def render(items):
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":11})
    fig = plt.figure(figsize=(14,9),facecolor="#f6f4ef")
    fig.text(.055,.94,"DIY 400",fontsize=32,fontweight="bold",color="#20323f")
    fig.text(.055,.893,"Modular quadcopter frame  /  fit-and-assembly prototype",fontsize=16,color="#405464")
    ax = fig.add_axes([.015,.12,.70,.74])
    ax.imshow(rasterize(items))
    ax.set_axis_off()
    lines=[("400 mm","opposite motor centres"),
           ("130 × 110 mm","slotted electronics tray"),
           ("178 × 36 mm","largest arm footprint"),
           ("11 printed pieces","5 unique STL parts"),
           ("M3 fasteners","bolted, replaceable arms")]
    for i,(big,small) in enumerate(lines):
        y=.77-i*.12
        fig.text(.725,y,big,fontsize=17,fontweight="bold",color="#20323f")
        fig.text(.725,y-.031,small,fontsize=10.5,color="#526571")
    fig.text(.055,.065,"PRINTABLE GEOMETRY CHECKED",fontsize=10,fontweight="bold",color="#17746f")
    fig.text(.055,.038,"Not load-tested or flight-tested. Confirm motor and board fit before printing all parts.",fontsize=11,color="#526571")
    fig.savefig(ROOT/"frame_preview.png",dpi=150,facecolor=fig.get_facecolor())
    plt.close(fig)


def write_scad():
    # A simple editable viewer, using the exact exported STL components.
    scad='''// Dimensions in mm. Geometry is parameterized in generate_frame.py.
// Assembly VIEW ONLY. Slice the individual STL files, not this assembly.
color("SlateGray") import("stl/01_bottom_plate_print_1.stl");
for(a=[45,135,225,315])
  color("LightSeaGreen") rotate([0,0,a]) translate([40,0,3])
    import("stl/03_arm_print_4.stl");
color("SlateGray") translate([0,0,11]) import("stl/02_top_clamp_print_1.stl");
for(x=[-45,45]) for(y=[-20,20])
  color("SlateGray") translate([x,y,14]) import("stl/05_spacer_15mm_print_4.stl");
color("Goldenrod") translate([0,0,29]) import("stl/04_electronics_tray_print_1.stl");
'''
    (ROOT/"assembly_view.scad").write_text(scad)


def main():
    parts=make_parts()
    result=[export_validate(name,solid) for name,solid in parts.items()]
    # Four arms packed flat on a separate optional 178x162 mm print plate.
    arm=parts["03_arm_print_4"]
    pack=md.Manifold()
    for y in [18,60,102,144]:
        pack += arm.translate((0,y,0))
    result.append(export_validate("06_optional_four_arm_print_plate",pack,4))
    items=assembled(parts)
    scene=trimesh.Scene()
    for i,(name,solid,color) in enumerate(items):
        mesh=to_trimesh(solid)
        rgba=(np.array(matplotlib.colors.to_rgba(color))*255).astype(np.uint8)
        mesh.visual.vertex_colors=np.tile(rgba,(len(mesh.vertices),1))
        scene.add_geometry(mesh,node_name=f"{i}_{name}")
    scene.export(ROOT/"assembly_preview.glb",include_normals=False)
    render(items)
    write_scad()
    counts=[1,1,4,1,4]
    total=sum(float(parts[k].volume())*n for k,n in zip(parts,counts))/1000
    report={"units":"mm","prototype_only":True,"flight_tested":False,
            "structural_analysis_performed":False,
            "diagonal_motor_spacing_mm":400,
            "adjacent_motor_spacing_mm":round(400/math.sqrt(2),3),
            "planar_gap_between_nominal_10inch_prop_discs_mm":round(400/math.sqrt(2)-254,3),
            "assembled_solid_collisions":0,"total_solid_volume_cm3":round(total,3),
            "solid_petg_mass_g_at_1_27g_cm3":round(total*1.27,1),
            "mass_note":"Full-density CAD volume; actual slicer mass depends on walls and infill. Hardware excluded.",
            "stl_validation":result}
    (ROOT/"mesh_validation.json").write_text(json.dumps(report,indent=2)+'\n')
    with zipfile.ZipFile(ROOT/"DIY_400_quadcopter_STLs.zip","w",zipfile.ZIP_DEFLATED) as z:
        for path in sorted(OUT.glob('*.stl')):
            z.write(path,"DIY_400/stl/"+path.name)
        for name in ["README.md","mesh_validation.json","frame_preview.png","assembly_preview.glb","assembly_view.scad","generate_frame.py","requirements.txt"]:
            path=ROOT/name
            if path.exists():z.write(path,"DIY_400/"+name)
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()

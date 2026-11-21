"""
Small pythonOCC helpers shared by the substructure builders.
All dimensions in mm.  Descriptors come from layout.py.
"""
import math

from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCC.Core.BRepPrimAPI import (
    BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism,
    BRepPrimAPI_MakeSphere, BRepPrimAPI_MakeTorus,
)
from OCC.Core.TopoDS import TopoDS_Compound
from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec


def make_compound(shapes):
    """Merge shapes into ONE compound (keeps the number of AIS objects small)."""
    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    for s in shapes:
        builder.Add(comp, s)
    return comp


def cylinder(base, radius, height):
    """Vertical cylinder, base = centre of bottom face."""
    ax = gp_Ax2(gp_Pnt(*base), gp_Dir(0, 0, 1))
    return BRepPrimAPI_MakeCylinder(ax, radius, height).Shape()


def box(x0, x1, y0, y1, z0, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def prism_from_xz_polygon(polygon_xz, y0, y1, shear_x=0.0):
    """
    Extrude an XZ polygon (given at y = y0) to y = y1.  shear_x gives an
    oblique sweep so skewed pier caps keep a constant trapezoidal section.
    """
    # order chosen so that the face normal is +Y (same side as the sweep)
    pts = [polygon_xz[0], polygon_xz[3], polygon_xz[2], polygon_xz[1]]
    poly = BRepBuilderAPI_MakePolygon()
    for x, z in pts:
        poly.Add(gp_Pnt(x, y0, z))
    poly.Close()
    face = BRepBuilderAPI_MakeFace(poly.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(shear_x, y1 - y0, 0.0)).Shape()


def _segment(p1, p2, radius):
    v = gp_Vec(gp_Pnt(*p1), gp_Pnt(*p2))
    length = v.Magnitude()
    if length < 1e-6:
        return None
    ax = gp_Ax2(gp_Pnt(*p1), gp_Dir(v))
    return BRepPrimAPI_MakeCylinder(ax, radius, length).Shape()


def bar_solids(path, diameter, closed=False):
    """Straight / bent / closed rebar as cylinders (+ spheres at the bends)."""
    r = diameter / 2.0
    pts = list(path) + ([path[0]] if closed else [])
    out = []
    for a, b in zip(pts[:-1], pts[1:]):
        seg = _segment(a, b, r)
        if seg is not None:
            out.append(seg)
    if len(pts) > 2:                                   # smooth the bends
        joints = pts[1:-1] if not closed else pts[:-1]
        for j in joints:
            out.append(BRepPrimAPI_MakeSphere(gp_Pnt(*j), r).Shape())
    return out


def ring_solid(centre, ring_radius, diameter):
    """Circular hoop (torus) lying in a horizontal plane."""
    ax = gp_Ax2(gp_Pnt(*centre), gp_Dir(0, 0, 1))
    return BRepPrimAPI_MakeTorus(ax, ring_radius, diameter / 2.0).Shape()


def rebar_compound(rebar_items):
    """Turn Bar / RingBar descriptors into one compound."""
    shapes = []
    for b in rebar_items:
        if hasattr(b, "path"):
            shapes += bar_solids(b.path, b.diameter, b.closed)
        else:
            shapes.append(ring_solid(b.centre, b.radius, b.diameter))
    return make_compound(shapes) if shapes else None

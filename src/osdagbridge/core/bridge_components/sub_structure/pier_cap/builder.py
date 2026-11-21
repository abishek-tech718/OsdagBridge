"""
Pier cap (hammerhead / trapezoidal bent cap) builder - pythonOCC.
Section: trapezoid in XZ, length along Y (skew-sheared), see layout.py.
"""
from osdagbridge.core.bridge_components.sub_structure.occ_helpers import (
    prism_from_xz_polygon, rebar_compound,
)


def build_pier_cap(layout):
    concrete = [prism_from_xz_polygon(p.polygon_xz, p.y0, p.y1, p.shear_x)
                for p in layout.pier_caps]
    rebar = rebar_compound(layout.rebar_of("pier_cap"))
    return {"concrete": concrete, "rebar": [rebar] if rebar else []}

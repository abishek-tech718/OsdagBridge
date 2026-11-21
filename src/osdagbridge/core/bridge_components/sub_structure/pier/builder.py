"""
Pier (circular column) builder - pythonOCC.

Input : SubstructureLayout (layout.py)   Output : concrete solids + rebar cage
"""
from osdagbridge.core.bridge_components.sub_structure.occ_helpers import (
    cylinder, rebar_compound,
)


def build_pier(layout):
    """
    Returns
    -------
    dict(concrete=[TopoDS_Shape,...], rebar=[TopoDS_Compound] or [])
    """
    concrete = [cylinder(c.base, c.radius, c.height) for c in layout.piers]
    rebar = rebar_compound(layout.rebar_of("pier"))
    return {"concrete": concrete, "rebar": [rebar] if rebar else []}

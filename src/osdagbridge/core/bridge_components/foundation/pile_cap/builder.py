"""Pile cap (rectangular prism) builder - pythonOCC."""
from osdagbridge.core.bridge_components.sub_structure.occ_helpers import (
    box, rebar_compound,
)


def build_pile_cap(layout):
    concrete = [box(b.x0, b.x1, b.y0, b.y1, b.z0, b.z1) for b in layout.pile_caps]
    rebar = rebar_compound(layout.rebar_of("pile_cap"))
    return {"concrete": concrete, "rebar": [rebar] if rebar else []}

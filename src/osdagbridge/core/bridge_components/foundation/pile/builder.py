"""Pile (circular, bored) builder - pythonOCC."""
from osdagbridge.core.bridge_components.sub_structure.occ_helpers import (
    cylinder, rebar_compound,
)


def build_pile(layout):
    concrete = [cylinder(c.base, c.radius, c.height) for c in layout.piles]
    rebar = rebar_compound(layout.rebar_of("pile"))
    return {"concrete": concrete, "rebar": [rebar] if rebar else []}

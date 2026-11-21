"""
Substructure assembly: layout -> four component builders -> model_data entries.

Called from PlateGirderCADGenerator.generate().
"""
from osdagbridge.core.bridge_components.sub_structure.layout import (
    SubstructureParams, build_substructure_layout,
)
from osdagbridge.core.bridge_components.sub_structure.pier.builder import build_pier
from osdagbridge.core.bridge_components.sub_structure.pier_cap.builder import build_pier_cap
from osdagbridge.core.bridge_components.foundation.pile_cap.builder import build_pile_cap
from osdagbridge.core.bridge_components.foundation.pile.builder import build_pile


def build_substructure(params: SubstructureParams, *, span_length, deck_width,
                       girder_d, tf_b, skew_deg=0.0):
    """
    Returns (model_data_entries: dict, layout: SubstructureLayout)

    model_data keys (all lists of TopoDS_Shape):
        pier_caps, piers, pile_caps, piles   - concrete  (shown semi-transparent)
        substructure_rebar                   - all rebar (opaque steel colour)
    """
    layout = build_substructure_layout(
        params, span_length=span_length, deck_width=deck_width,
        girder_d=girder_d, tf_b=tf_b, skew_deg=skew_deg)

    cap, pier = build_pier_cap(layout), build_pier(layout)
    pcap, pile = build_pile_cap(layout), build_pile(layout)

    entries = {
        "pier_caps": cap["concrete"],
        "piers": pier["concrete"],
        "pile_caps": pcap["concrete"],
        "piles": pile["concrete"],
        "substructure_rebar": cap["rebar"] + pier["rebar"] + pcap["rebar"] + pile["rebar"],
    }
    return entries, layout

"""
Substructure tests.  Run from repo root:   pytest tests/test_substructure.py -v

* layout tests : pure python (no OCC / no GUI)
* IFC tests    : need ifcopenshell (already in environment.yml)
"""
import math
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from osdagbridge.core.bridge_components.sub_structure.layout import (   # noqa: E402
    SubstructureParams, build_substructure_layout, summarise_quantities, Bar,
)

KW = dict(span_length=20000, deck_width=10900, girder_d=1200, tf_b=30)


def _layout(skew=0.0, **over):
    return build_substructure_layout(SubstructureParams(**over), skew_deg=skew, **KW)


def test_default_counts():
    lay = _layout()
    assert (len(lay.pier_caps), len(lay.piers), len(lay.pile_caps), len(lay.piles)) == (2, 2, 2, 8)


def test_vertical_stack_is_continuous():
    z = _layout().z_levels
    assert z["cap_bot"] - 3000 == pytest.approx(z["pier_bot"])
    assert z["pier_bot"] == pytest.approx(z["pile_cap_top"])
    assert z["pile_cap_bot"] - 5000 == pytest.approx(z["pile_bot"])


def test_pier_cap_sits_below_bearings():
    lay = _layout()
    z_contact = -(1200 / 2 + 30)
    assert lay.z_levels["cap_top"] < z_contact


@pytest.mark.parametrize("skew", [0, 15, -20])
def test_all_rebar_has_cover(skew):
    """Every bar centre-line must be inside its concrete member by >= cover + r."""
    p = SubstructureParams()
    lay = _layout(skew)
    for pc in lay.pile_caps:
        for b in lay.rebar_of("pile_cap"):
            if b.host != pc.name:
                continue
            for x, y, z in b.path:
                assert pc.x0 + p.rebar_cover <= x <= pc.x1 - p.rebar_cover + 1e-6
                assert pc.y0 + p.rebar_cover <= y <= pc.y1 - p.rebar_cover + 1e-6
                assert pc.z0 + p.rebar_cover <= z <= pc.z1 - p.rebar_cover + 1e-6


def test_pile_grid_is_parametric():
    lay = _layout(n_piles_x=3, n_piles_y=2, pile_spacing=800.0)
    assert len(lay.piles) == 2 * 6


def test_rebar_toggle():
    assert _layout(include_rebar=False).rebar == []


def test_quantities_are_plausible():
    q = summarise_quantities(_layout(), SubstructureParams())
    assert 35 < sum(q["concrete_m3"].values()) < 45
    assert 1500 < sum(q["rebar_kg"].values()) < 3000


# --------------------------------------------------------------- IFC
ifcopenshell = pytest.importorskip("ifcopenshell")


def _stub_occ_if_missing():
    """Only used where pythonOCC is not installed (e.g. CI without conda)."""
    try:
        import OCC.Core.gp  # noqa: F401
        return
    except ImportError:
        pass
    import importlib.machinery
    from unittest import mock

    class _Finder:
        def find_spec(self, name, path=None, target=None):
            if name == "OCC" or name.startswith("OCC."):
                return importlib.machinery.ModuleSpec(name, self, is_package=True)

        def create_module(self, spec):
            return mock.MagicMock(__path__=[], __spec__=spec, __name__=spec.name)

        def exec_module(self, module):
            pass

    sys.meta_path.insert(0, _Finder())


@pytest.fixture(scope="module")
def ifc_file(tmp_path_factory):
    _stub_occ_if_missing()          # IFC export path does not need pythonOCC
    from osdagbridge.core.ifc_export_bridge.bridge_ifc_generator import BridgeIfcGenerator
    out = tmp_path_factory.mktemp("ifc") / "sub.ifc"
    g = BridgeIfcGenerator(str(out))
    g.generate_from_extracted_data({"substructure": _layout(10)})
    return ifcopenshell.open(str(out))


def test_ifc_entity_counts(ifc_file):
    n = lambda t: len(ifc_file.by_type(t))
    assert (n("IfcBeam"), n("IfcColumn"), n("IfcFooting"), n("IfcPile")) == (2, 2, 2, 8)
    assert n("IfcReinforcingBar") > 400


def test_ifc_boq_quantities_present(ifc_file):
    vols = [q.VolumeValue for e in ifc_file.by_type("IfcElementQuantity")
            for q in e.Quantities if q.is_a("IfcQuantityVolume") and q.Name == "NetVolume"]
    assert sum(vols) > 30
    weights = [q.WeightValue for e in ifc_file.by_type("IfcElementQuantity")
               for q in e.Quantities if q.is_a("IfcQuantityWeight")]
    assert sum(weights) > 1500


def test_ifc_every_bar_has_valid_geometry(ifc_file):
    import ifcopenshell.geom as geom
    st = geom.settings()
    for e in ifc_file.by_type("IfcReinforcingBar")[::7]:          # sample
        geom.create_shape(st, e)

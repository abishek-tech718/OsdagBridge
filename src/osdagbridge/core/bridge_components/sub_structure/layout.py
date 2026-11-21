"""
Substructure layout (single source of truth).

Pure-Python module (NO pythonOCC / ifcopenshell imports).  It turns a
`SubstructureParams` object plus a few superstructure facts into a list of
geometric *descriptors* (Cylinder, Box, Prism, Bar, RingBar).

    layout.py  ->  builder.py files (pythonOCC solids for the CAD window)
               ->  ifc_export_bridge/substructure_ifc.py (IFC entities)

Because both the 3D view and the IFC are produced from the same descriptors,
they can never drift apart, and the layout is testable without a GUI.

Units  : millimetres (mm)
Axes   : X = longitudinal (span), Y = transverse (deck width), Z = vertical
Frame  : the SAME frame used by the existing superstructure code
         (x = 0 .. L along the span, girder mid-depth at z = 0).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

Point = Tuple[float, float, float]


# --------------------------------------------------------------------------
# 1. PARAMETERS  (defaults = values given in the task sheet, in mm)
# --------------------------------------------------------------------------
@dataclass
class SubstructureParams:
    # Piers (circular columns)
    pier_diameter: float = 800.0
    pier_height: float = 3000.0
    n_columns_per_bent: int = 1          # columns under one pier cap
    column_spacing: float = 5000.0       # c/c, only used if n_columns > 1

    # Pier cap (trapezoidal / hammerhead, section in XZ, extruded along Y)
    pier_cap_top_width: float = 3000.0
    pier_cap_bottom_width: float = 1200.0
    pier_cap_depth: float = 600.0
    pier_cap_length: Optional[float] = None   # None -> match deck width

    # Pile cap (rectangular prism)
    pile_cap_length: float = 2200.0      # along X
    pile_cap_width: float = 1200.0       # along Y
    pile_cap_depth: float = 600.0

    # Piles (circular) - n_x * n_y grid under each pile cap
    n_piles_x: int = 2
    n_piles_y: int = 2
    pile_diameter: float = 400.0
    pile_length: float = 5000.0
    pile_spacing: float = 600.0          # centre-to-centre
    pile_embedment: float = 0.0          # pile head embedded into pile cap

    # Reinforcement
    rebar_main_diameter: float = 16.0
    rebar_spacing_longitudinal: float = 150.0
    rebar_transverse_diameter: float = 8.0
    rebar_spacing_transverse: float = 200.0
    rebar_cover: float = 40.0
    include_rebar: bool = True

    # Materials (names only - used in IFC)
    concrete_grade: str = "M35"
    rebar_grade: str = "Fe500D"

    @property
    def n_piles_per_cap(self) -> int:
        return self.n_piles_x * self.n_piles_y


# --------------------------------------------------------------------------
# 2. DESCRIPTORS
# --------------------------------------------------------------------------
@dataclass
class Cylinder:
    name: str
    comp: str                 # 'pier' | 'pile'
    base: Point               # centre of the bottom face
    radius: float
    height: float             # extruded along +Z


@dataclass
class Box:
    name: str
    comp: str                 # 'pile_cap'
    x0: float; x1: float
    y0: float; y1: float
    z0: float; z1: float


@dataclass
class Prism:
    """Polygon in the XZ plane at y = y0, swept along (shear_x, dy, 0)."""
    name: str
    comp: str                 # 'pier_cap'
    polygon_xz: List[Tuple[float, float]]
    y0: float
    y1: float
    shear_x: float            # x-shift between the y0 and y1 end faces (skew)


@dataclass
class Bar:
    """Rebar following a polyline (closed=True -> stirrup loop)."""
    name: str
    comp: str                 # 'pier' | 'pier_cap' | 'pile_cap' | 'pile'
    role: str                 # 'MAIN' | 'LIGATURE' | 'STIRRUP' | 'SHEAR'...
    path: List[Point]
    diameter: float
    closed: bool = False
    host: str = ""           # name of the concrete member that owns the bar

    @property
    def length(self) -> float:
        pts = self.path + ([self.path[0]] if self.closed else [])
        return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


@dataclass
class RingBar:
    """Circular hoop / spiral turn in a horizontal plane."""
    name: str
    comp: str
    role: str
    centre: Point
    radius: float             # radius of the bar centre-line
    diameter: float
    host: str = ""

    @property
    def length(self) -> float:
        return 2 * math.pi * self.radius


@dataclass
class SubstructureLayout:
    """Everything needed to draw / export the substructure."""
    pier_caps: List[Prism] = field(default_factory=list)
    piers: List[Cylinder] = field(default_factory=list)
    pile_caps: List[Box] = field(default_factory=list)
    piles: List[Cylinder] = field(default_factory=list)
    rebar: List[object] = field(default_factory=list)     # Bar | RingBar
    # key facts for summary / report / BOQ
    z_levels: dict = field(default_factory=dict)
    params: Optional[SubstructureParams] = None

    def rebar_of(self, comp: str):
        return [b for b in self.rebar if b.comp == comp]


# --------------------------------------------------------------------------
# 3. SMALL HELPERS
# --------------------------------------------------------------------------
def bearing_seat(span_length, girder_d, tf_b):
    """
    Bearing seat geometry - mirrors PlateGirderCADGenerator / IFC extractor
    support sizing so the pier cap sits exactly under the bearings.

    Returns (z_contact, h_supp, w_supp, r_cyl)
    """
    z_contact = -(girder_d / 2.0 + tf_b)
    base_dim = min(0.10 * span_length, 0.75 * girder_d)
    h_supp = base_dim / 1.5
    w_supp = base_dim / 2.0
    r_cyl = h_supp / 2.0
    return z_contact, h_supp, w_supp, r_cyl


def _spread(lo: float, hi: float, max_spacing: float) -> List[float]:
    """Evenly spread points over [lo, hi] with spacing <= max_spacing."""
    if hi <= lo:
        return [0.5 * (lo + hi)]
    n = int(math.ceil((hi - lo) / max_spacing)) + 1
    return [lo + i * (hi - lo) / (n - 1) for i in range(n)]


def _levels(start: float, stop: float, spacing: float) -> List[float]:
    """start, start+spacing, ... up to <= stop."""
    out, z = [], start
    while z <= stop + 1e-6:
        out.append(z)
        z += spacing
    return out


def _trap_half_width(top_w, bot_w, depth, inset, z_rel):
    """
    Half-width of the trapezoid inset by `inset` (perpendicular to the sloping
    faces) at height z_rel above the cap soffit.
    """
    s = (top_w - bot_w) / 2.0 / depth                # dx per dz
    return bot_w / 2.0 + s * z_rel - inset * math.sqrt(1 + s * s)


# --------------------------------------------------------------------------
# 4. REINFORCEMENT GENERATORS
# --------------------------------------------------------------------------
def _circular_cage(p, comp, tag, cx, cy, z_bot, z_top, radius, ext_bot, ext_top):
    """
    Circular column / pile cage: vertical main bars on a ring + hoops.
    Main bars are extended by ext_bot / ext_top (anchorage into caps).
    """
    dm, dt, c = p.rebar_main_diameter, p.rebar_transverse_diameter, p.rebar_cover
    r_main = radius - c - dt - dm / 2.0
    r_hoop = radius - c - dt / 2.0
    n_main = max(6, int(2 * math.pi * r_main // p.rebar_spacing_longitudinal))
    bars = []
    for i in range(n_main):
        a = 2 * math.pi * i / n_main
        x, y = cx + r_main * math.cos(a), cy + r_main * math.sin(a)
        bars.append(Bar(f"{tag} Main Bar {i+1}", comp, "MAIN",
                        [(x, y, z_bot - ext_bot), (x, y, z_top + ext_top)], dm))
    zs = _levels(z_bot + c + dt / 2.0, z_top - c, p.rebar_spacing_transverse)
    for j, z in enumerate(zs):
        bars.append(RingBar(f"{tag} Hoop {j+1}", comp, "LIGATURE", (cx, cy, z), r_hoop, dt))
    for b in bars:
        b.host = tag
    return bars


def _pile_cap_cage(p, x0, x1, y0, y1, z0, z1, tag):
    """Top & bottom meshes of a pile cap (main along X, transverse along Y)."""
    dm, dt, c = p.rebar_main_diameter, p.rebar_transverse_diameter, p.rebar_cover
    bars = []
    ys = _spread(y0 + c + dm / 2, y1 - c - dm / 2, p.rebar_spacing_longitudinal)
    xs = _spread(x0 + c + dt / 2, x1 - c - dt / 2, p.rebar_spacing_transverse)
    # layer heights: bottom mesh = main (lowest) then transverse; top mesh mirrored
    z_main_bot, z_tr_bot = z0 + c + dm / 2, z0 + c + dm + dt / 2
    z_main_top, z_tr_top = z1 - c - dm / 2, z1 - c - dm - dt / 2
    for face, zm, zt in (("Bottom", z_main_bot, z_tr_bot), ("Top", z_main_top, z_tr_top)):
        for i, y in enumerate(ys):
            bars.append(Bar(f"{tag} {face} Main Bar {i+1}", "pile_cap", "MAIN",
                            [(x0 + c, y, zm), (x1 - c, y, zm)], dm))
        for i, x in enumerate(xs):
            bars.append(Bar(f"{tag} {face} Distribution Bar {i+1}", "pile_cap", "DISTRIBUTION",
                            [(x, y0 + c, zt), (x, y1 - c, zt)], dt))
    for b in bars:
        b.host = tag
    return bars


def _pier_cap_cage(p, xc, y0, y1, z_soffit, skew_tan, tag):
    """
    Trapezoidal pier cap cage:
      * main bars along Y in bottom & top rows (spacing = rebar_spacing_longitudinal)
      * trapezoidal closed stirrups every rebar_spacing_transverse along Y
    All x-positions are sheared by y*tan(skew) to follow the skewed cap axis.
    """
    dm, dt, c = p.rebar_main_diameter, p.rebar_transverse_diameter, p.rebar_cover
    T, B, D = p.pier_cap_top_width, p.pier_cap_bottom_width, p.pier_cap_depth
    bars = []
    sh = lambda y: xc + y * skew_tan

    # stirrups (centre-line inset = cover + dt/2)
    ins_s = c + dt / 2.0
    zl, zh = ins_s, D - ins_s
    hl = _trap_half_width(T, B, D, ins_s, zl)
    hh = _trap_half_width(T, B, D, ins_s, zh)
    ys = _levels(y0 + c + dt / 2, y1 - c - dt / 2, p.rebar_spacing_transverse)
    for k, y in enumerate(ys):
        x0 = sh(y)
        loop = [(x0 - hl, y, z_soffit + zl), (x0 + hl, y, z_soffit + zl),
                (x0 + hh, y, z_soffit + zh), (x0 - hh, y, z_soffit + zh)]
        bars.append(Bar(f"{tag} Stirrup {k+1}", "pier_cap", "STIRRUP", loop, dt, closed=True))

    # main bars (inside stirrups)
    ins_m = c + dt + dm / 2.0
    for row, zr in (("Bottom", ins_m), ("Top", D - ins_m)):
        half = _trap_half_width(T, B, D, ins_m, zr)
        xs = _spread(-half, half, p.rebar_spacing_longitudinal)
        for i, xo in enumerate(xs):
            bars.append(Bar(f"{tag} {row} Main Bar {i+1}", "pier_cap", "MAIN",
                            [(sh(y0 + c) + xo, y0 + c, z_soffit + zr),
                             (sh(y1 - c) + xo, y1 - c, z_soffit + zr)], dm))
    for b in bars:
        b.host = tag
    return bars


# --------------------------------------------------------------------------
# 5. MAIN ENTRY POINT
# --------------------------------------------------------------------------
def build_substructure_layout(
    p: SubstructureParams,
    *,
    span_length: float,
    deck_width: float,
    girder_d: float,
    tf_b: float,
    skew_deg: float = 0.0,
) -> SubstructureLayout:
    """
    Two bents (abutment-line piers) - one under each end bearing line:
        left  bent at x = w_supp            (under the triangular bearing)
        right bent at x = L - r_cyl         (under the cylindrical bearing)
    Vertical stack (top -> bottom):
        bearing seat -> PIER CAP -> PIER -> PILE CAP -> PILES
    """
    lay = SubstructureLayout(params=p)
    z_contact, h_supp, w_supp, r_cyl = bearing_seat(span_length, girder_d, tf_b)
    skew_tan = math.tan(math.radians(skew_deg)) if skew_deg else 0.0

    cap_len = p.pier_cap_length or (deck_width / math.cos(math.radians(skew_deg))
                                    if skew_deg else deck_width)

    # vertical levels (mm)
    z_cap_top = z_contact - h_supp
    z_cap_bot = z_cap_top - p.pier_cap_depth
    z_pier_bot = z_cap_bot - p.pier_height
    z_pc_top = z_pier_bot
    z_pc_bot = z_pc_top - p.pile_cap_depth
    z_pile_top = z_pc_bot + p.pile_embedment
    z_pile_bot = z_pile_top - p.pile_length
    lay.z_levels = dict(cap_top=z_cap_top, cap_bot=z_cap_bot, pier_bot=z_pier_bot,
                        pile_cap_top=z_pc_top, pile_cap_bot=z_pc_bot,
                        pile_top=z_pile_top, pile_bot=z_pile_bot)

    # column centres across the bent (Y)
    n = max(1, int(p.n_columns_per_bent))
    col_ys = [(i - (n - 1) / 2.0) * p.column_spacing for i in range(n)]

    # pile grid (relative to column centre)
    gx = [(i - (p.n_piles_x - 1) / 2.0) * p.pile_spacing for i in range(p.n_piles_x)]
    gy = [(j - (p.n_piles_y - 1) / 2.0) * p.pile_spacing for j in range(p.n_piles_y)]

    dm, c = p.rebar_main_diameter, p.rebar_cover
    anchor_cap = p.pier_cap_depth - c - 2 * dm          # pier bars up into pier cap
    anchor_pc = p.pile_cap_depth - c - 2 * dm           # pile bars up into pile cap

    for b_idx, (bname, xc) in enumerate((("Left Bent", w_supp),
                                         ("Right Bent", span_length - r_cyl))):
        tag = f"{bname}"
        y0, y1 = -cap_len / 2.0, cap_len / 2.0

        # ---- pier cap (trapezoid, sheared along skewed axis)
        T, B, D = p.pier_cap_top_width, p.pier_cap_bottom_width, p.pier_cap_depth
        # section is drawn at the y0 end, shifted so the cap axis crosses the
        # bent centre (xc) at y = 0 once the skew shear is applied
        x0 = xc + y0 * skew_tan
        poly = [(x0 - B / 2, z_cap_bot), (x0 + B / 2, z_cap_bot),
                (x0 + T / 2, z_cap_top), (x0 - T / 2, z_cap_top)]
        lay.pier_caps.append(Prism(f"{tag} Pier Cap", "pier_cap", poly,
                                   y0, y1, shear_x=(y1 - y0) * skew_tan))
        if p.include_rebar:
            lay.rebar += _pier_cap_cage(p, xc, y0, y1, z_cap_bot, skew_tan, f"{tag} Pier Cap")

        for k, cy in enumerate(col_ys):
            cx = xc + cy * skew_tan
            ctag = f"{tag} Col {k+1}"
            # ---- pier column
            lay.piers.append(Cylinder(f"{ctag} Pier", "pier", (cx, cy, z_pier_bot),
                                      p.pier_diameter / 2.0, p.pier_height))
            if p.include_rebar:
                lay.rebar += _circular_cage(
                    p, "pier", f"{ctag} Pier", cx, cy, z_pier_bot, z_cap_bot,
                    p.pier_diameter / 2.0,
                    ext_bot=p.pile_cap_depth - c - dm - p.rebar_transverse_diameter,
                    ext_top=anchor_cap)

            # ---- pile cap
            x0, x1 = cx - p.pile_cap_length / 2, cx + p.pile_cap_length / 2
            py0, py1 = cy - p.pile_cap_width / 2, cy + p.pile_cap_width / 2
            lay.pile_caps.append(Box(f"{ctag} Pile Cap", "pile_cap", x0, x1, py0, py1, z_pc_bot, z_pc_top))
            if p.include_rebar:
                lay.rebar += _pile_cap_cage(p, x0, x1, py0, py1, z_pc_bot, z_pc_top, f"{ctag} Pile Cap")

            # ---- piles
            pn = 0
            for ix in gx:
                for iy in gy:
                    pn += 1
                    px, py = cx + ix, cy + iy
                    lay.piles.append(Cylinder(f"{ctag} Pile {pn}", "pile", (px, py, z_pile_bot),
                                              p.pile_diameter / 2.0, p.pile_length))
                    if p.include_rebar:
                        lay.rebar += _circular_cage(
                            p, "pile", f"{ctag} Pile {pn}", px, py, z_pile_bot, z_pile_top,
                            p.pile_diameter / 2.0, ext_bot=0.0, ext_top=anchor_pc)
    return lay


# --------------------------------------------------------------------------
# 6. QUANTITIES (used for the IFC BOQ and for sanity checks)
# --------------------------------------------------------------------------
def summarise_quantities(lay: SubstructureLayout, p: SubstructureParams) -> dict:
    """Concrete volumes (m^3) and rebar mass (kg) - steel density 7850 kg/m^3."""
    def prism_area(poly):
        return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
                       - poly[(i + 1) % len(poly)][0] * poly[i][1]
                       for i in range(len(poly)))) / 2.0
    mm3 = 1e-9
    vol = {
        "pier_cap": sum(prism_area(pr.polygon_xz) * (pr.y1 - pr.y0) for pr in lay.pier_caps) * mm3,
        "pier": sum(math.pi * c.radius ** 2 * c.height for c in lay.piers) * mm3,
        "pile_cap": sum((b.x1 - b.x0) * (b.y1 - b.y0) * (b.z1 - b.z0) for b in lay.pile_caps) * mm3,
        "pile": sum(math.pi * c.radius ** 2 * c.height for c in lay.piles) * mm3,
    }
    mass = {}
    for b in lay.rebar:
        m = math.pi * (b.diameter / 2) ** 2 * b.length * mm3 * 7850.0
        mass[b.comp] = mass.get(b.comp, 0.0) + m
    return {"concrete_m3": vol, "rebar_kg": mass}

"""
Substructure -> IFC4 writer.

Consumes the SAME SubstructureLayout used by the 3D CAD builders
(bridge_components/sub_structure/layout.py), so the IFC and the CAD window
always agree.  No pythonOCC needed here.

IFC mapping
-----------
  Pier cap   -> IfcBeam              (storey "Substructure")
  Pier       -> IfcColumn            (storey "Substructure")
  Pile cap   -> IfcFooting/PILE_CAP  (storey "Foundation")
  Pile       -> IfcPile/BORED        (storey "Foundation")
  Rebar      -> IfcReinforcingBar    (IfcSweptDiskSolid along the bar centre-line),
                grouped per member in an IfcElementAssembly "... Reinforcement Cage"

BOQ support
-----------
  * IfcMaterial (concrete grade / rebar grade) linked with IfcRelAssociatesMaterial
  * IfcElementQuantity (NetVolume, Length, Count, Weight ...) on every element
  * Pset_OsdagSubstructure with the design dimensions
  * IfcReinforcingBar.NominalDiameter / CrossSectionArea / BarLength / SteelGrade

Axes: global X = span, Y = deck width, Z = up (metres in the file).
Each element has its own IfcLocalPlacement relative to its storey; storeys sit
at the building origin, so local axes stay parallel to the global axes.
"""
import math
import time

from osdagbridge.core.ifc_export_bridge.bridge_geometry_mapper import create_ifc_guid

S = 0.001                       # mm -> m
STEEL_DENSITY = 7850.0          # kg/m^3

CONCRETE_RGB = (0.60, 0.65, 0.72)
REBAR_RGB = (0.59, 0.27, 0.20)
CONCRETE_TRANSPARENCY = 0.65    # == opacity 0.35 used in the CAD window

_BAR_TYPE = {"MAIN": "MAIN", "LIGATURE": "RING", "STIRRUP": "LIGATURE"}   # layout role -> IFC enum


class SubstructureIfcWriter:
    def __init__(self, gen, layout):
        self.g = gen                    # BridgeIfcGenerator (already initialised)
        self.f = gen.file
        self.m = gen.mapper
        self.oh = gen._owner_history
        self.lay = layout
        self.p = layout.params
        self._styles = {}
        self._mat_members = {}          # IfcMaterial -> [elements]
        self._materials = {}
        self.counts = {}

    # ------------------------------------------------------------------ utils
    def _place(self, origin, z_dir=(0, 0, 1), x_dir=(1, 0, 0), parent=None):
        a2p = self.m.create_axis2placement_3d(tuple(v * S for v in origin), z_dir=z_dir, x_dir=x_dir)
        return self.f.createIfcLocalPlacement(parent, a2p)

    def _style(self, kind):
        if kind in self._styles:
            return self._styles[kind]
        rgb, transp = (CONCRETE_RGB, CONCRETE_TRANSPARENCY) if kind == "concrete" else (REBAR_RGB, 0.0)
        col = self.f.createIfcColourRgb(None, *rgb)
        rend = self.f.createIfcSurfaceStyleRendering(SurfaceColour=col, Transparency=transp,
                                                     ReflectanceMethod="NOTDEFINED")
        st = self.f.createIfcSurfaceStyle(f"Osdag {kind}", "BOTH", [rend])
        self._styles[kind] = st
        return st

    def _shape(self, solid, kind, rep_type="SweptSolid"):
        self.f.createIfcStyledItem(solid, [self._style(kind)], None)
        rep = self.f.createIfcShapeRepresentation(self.m._context3d, "Body", rep_type, [solid])
        return self.f.createIfcProductDefinitionShape(None, None, [rep])

    def _material(self, name, category):
        if name not in self._materials:
            self._materials[name] = self.f.createIfcMaterial(name, None, category)
            self._mat_members[name] = []
        return name

    def _assign_material(self, elem, name):
        self._mat_members[name].append(elem)

    def _qto(self, elem, qto_name, items):
        """items: (kind, name, value) with kind in V(olume) L(ength) A(rea) W(eight) C(ount)."""
        mk = {"V": self.f.createIfcQuantityVolume, "L": self.f.createIfcQuantityLength,
              "A": self.f.createIfcQuantityArea, "W": self.f.createIfcQuantityWeight,
              "C": self.f.createIfcQuantityCount}
        qs = [mk[k](n, None, None, float(v), None) for k, n, v in items]
        eq = self.f.createIfcElementQuantity(create_ifc_guid(), self.oh, qto_name, None, None, qs)
        self.f.createIfcRelDefinesByProperties(create_ifc_guid(), self.oh, None, None, [elem], eq)

    def _pset(self, elem, name, props):
        out = []
        for k, v in props.items():
            if isinstance(v, bool):
                val = self.f.create_entity("IfcBoolean", v)
            elif isinstance(v, int):
                val = self.f.create_entity("IfcInteger", v)
            elif isinstance(v, float):
                val = self.f.create_entity("IfcReal", v)
            else:
                val = self.f.create_entity("IfcLabel", str(v))
            out.append(self.f.createIfcPropertySingleValue(k, None, val, None))
        ps = self.f.createIfcPropertySet(create_ifc_guid(), self.oh, name, None, out)
        self.f.createIfcRelDefinesByProperties(create_ifc_guid(), self.oh, None, None, [elem], ps)

    def _count(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1

    # ---------------------------------------------------------------- storeys
    def _make_storey(self, name, elevation_mm):
        st = self.f.createIfcBuildingStorey(
            create_ifc_guid(), self.oh, Name=name, Elevation=elevation_mm * S,
            ObjectPlacement=self.f.createIfcLocalPlacement(
                self.g.building.ObjectPlacement, self.m.create_axis2placement_3d((0., 0., 0.))))
        self.f.createIfcRelAggregates(create_ifc_guid(), self.oh, RelatingObject=self.g.building,
                                      RelatedObjects=[st])
        return st

    def _contain(self, storey, elems):
        self.f.createIfcRelContainedInSpatialStructure(
            create_ifc_guid(), self.oh, RelatingStructure=storey, RelatedElements=elems)

    # --------------------------------------------------------------- concrete
    def _cylinder_member(self, cyl, ifc_class, kwargs, storey, mat, pset_extra):
        prof = self.f.createIfcCircleProfileDef("AREA", cyl.name + " Profile", None, cyl.radius * S)
        solid = self.f.createIfcExtrudedAreaSolid(
            prof, self.m.create_axis2placement_3d((0, 0, 0)),
            self.f.createIfcDirection((0., 0., 1.)), cyl.height * S)
        elem = getattr(self.f, f"create{ifc_class}")(
            create_ifc_guid(), self.oh, Name=cyl.name,
            ObjectPlacement=self._place(cyl.base, parent=storey.ObjectPlacement),
            Representation=self._shape(solid, "concrete"), **kwargs)
        vol = math.pi * cyl.radius ** 2 * cyl.height * 1e-9
        self._qto(elem, f"Qto_{ifc_class[3:]}BaseQuantities",
                  [("L", "Length", cyl.height * S), ("A", "CrossSectionArea", math.pi * (cyl.radius * S) ** 2),
                   ("V", "NetVolume", vol), ("V", "GrossVolume", vol)])
        self._pset(elem, "Pset_OsdagSubstructure",
                   dict(Diameter_mm=cyl.radius * 2, Height_mm=cyl.height, ConcreteGrade=self.p.concrete_grade,
                        **pset_extra))
        self._assign_material(elem, mat)
        return elem, vol

    def _pier_cap(self, pr):
        # Local frame: origin (0, y0, 0), local z = +Y (extrusion), local y = -Z  ->  profile (x, -z)
        pts = [(x * S, -z * S) for x, z in pr.polygon_xz]
        prof = self.m.create_polygonal_profile(pts, pr.name + " Profile")
        dy = pr.y1 - pr.y0
        norm = math.hypot(pr.shear_x, dy)                       # oblique sweep for skew
        direction = self.f.createIfcDirection((pr.shear_x / norm, 0., dy / norm))
        solid = self.f.createIfcExtrudedAreaSolid(
            prof, self.m.create_axis2placement_3d((0, 0, 0)), direction, norm * S)
        elem = self.f.createIfcBeam(
            create_ifc_guid(), self.oh, Name=pr.name, ObjectType="PIER_CAP",
            ObjectPlacement=self._place((0, pr.y0, 0), z_dir=(0, 1, 0), x_dir=(1, 0, 0),
                                        parent=self.storey_sub.ObjectPlacement),
            Representation=self._shape(solid, "concrete"), PredefinedType="BEAM")
        T, B, D = (self.p.pier_cap_top_width, self.p.pier_cap_bottom_width, self.p.pier_cap_depth)
        area = (T + B) / 2.0 * D
        vol = area * dy * 1e-9
        self._qto(elem, "Qto_BeamBaseQuantities",
                  [("L", "Length", norm * S), ("A", "CrossSectionArea", area * 1e-6),
                   ("V", "NetVolume", vol), ("V", "GrossVolume", vol)])
        self._pset(elem, "Pset_OsdagSubstructure",
                   dict(TopWidth_mm=T, BottomWidth_mm=B, Depth_mm=D, Length_mm=dy,
                        ConcreteGrade=self.p.concrete_grade))
        self._assign_material(elem, self._concrete)
        return elem, vol

    def _pile_cap(self, b):
        lx, ly, lz = b.x1 - b.x0, b.y1 - b.y0, b.z1 - b.z0
        prof = self.f.createIfcRectangleProfileDef("AREA", b.name + " Profile", None, lx * S, ly * S)
        solid = self.f.createIfcExtrudedAreaSolid(
            prof, self.m.create_axis2placement_3d((0, 0, 0)),
            self.f.createIfcDirection((0., 0., 1.)), lz * S)
        # origin at the centre of the bottom face
        origin = ((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2, b.z0)
        elem = self.f.createIfcFooting(
            create_ifc_guid(), self.oh, Name=b.name,
            ObjectPlacement=self._place(origin, parent=self.storey_fnd.ObjectPlacement),
            Representation=self._shape(solid, "concrete"), PredefinedType="PILE_CAP")
        vol = lx * ly * lz * 1e-9
        self._qto(elem, "Qto_FootingBaseQuantities",
                  [("L", "Length", lx * S), ("L", "Width", ly * S), ("L", "Height", lz * S),
                   ("A", "GrossArea", lx * ly * 1e-6), ("V", "GrossVolume", vol)])
        self._pset(elem, "Pset_OsdagSubstructure",
                   dict(Length_mm=lx, Width_mm=ly, Depth_mm=lz, ConcreteGrade=self.p.concrete_grade))
        self._assign_material(elem, self._concrete)
        return elem, vol

    # ------------------------------------------------------------------ rebar
    def _bar_directrix(self, bar):
        """
        Returns (origin_mm, directrix).
          * straight / bent bars  -> open IfcPolyline
          * closed stirrups       -> open polyline that stops one lap-gap short of
                                     the start (sweeps along a CLOSED polyline are
                                     rejected by most IFC kernels, and real stirrups
                                     have a lap/hook anyway)
          * circular hoops        -> exact IfcCircle (full 360 deg)
        """
        if not hasattr(bar, "path"):                    # RingBar
            origin = bar.centre
            circ = self.f.createIfcCircle(self.m.create_axis2placement_3d((0., 0., 0.)),
                                          bar.radius * S)
            return origin, circ
        pts = list(bar.path)
        if bar.closed:                                  # A B C D  ->  A B C D A'
            a, d = pts[0], pts[-1]
            v = [a[i] - d[i] for i in range(3)]
            L = math.sqrt(sum(c * c for c in v))
            gap = min(2.0 * bar.diameter, 0.25 * L)
            pts.append(tuple(a[i] - v[i] / L * gap for i in range(3)))
        o = pts[0]
        rel = [self.m.create_cartesian_point_3d((x - o[0]) * S, (y - o[1]) * S, (z - o[2]) * S)
               for x, y, z in pts]
        return o, self.f.createIfcPolyline(rel)

    def _bar(self, bar):
        origin, directrix = self._bar_directrix(bar)
        disk = self.f.createIfcSweptDiskSolid(directrix, bar.diameter * S / 2.0, None, None, None)
        d = bar.diameter * S
        area = math.pi * d * d / 4.0
        enum = _BAR_TYPE.get(bar.role, "USERDEFINED")
        elem = self.f.createIfcReinforcingBar(
            create_ifc_guid(), self.oh, Name=bar.name,
            ObjectType=bar.role if enum == "USERDEFINED" else None,
            ObjectPlacement=self._place(origin, parent=self._storey_of(bar.comp).ObjectPlacement),
            Representation=self._shape(disk, "steel", "AdvancedSweptSolid"),
            SteelGrade=self.p.rebar_grade, PredefinedType=enum,
            NominalDiameter=d, CrossSectionArea=area, BarLength=bar.length * S)
        mass = area * bar.length * S * STEEL_DENSITY
        self._qto(elem, "Qto_ReinforcingElementBaseQuantities",
                  [("C", "Count", 1), ("L", "Length", bar.length * S), ("W", "Weight", mass)])
        self._assign_material(elem, self._steel)
        self._count("rebar")
        return elem, mass

    def _storey_of(self, comp):
        return self.storey_sub if comp in ("pier", "pier_cap") else self.storey_fnd

    # ------------------------------------------------------------------- main
    def write(self):
        lay, p = self.lay, self.p
        zl = lay.z_levels
        self.storey_sub = self._make_storey("Substructure", zl["cap_bot"])
        self.storey_fnd = self._make_storey("Foundation", zl["pile_bot"])
        self._concrete = self._material(f"Concrete {p.concrete_grade}", "Concrete")
        self._steel = self._material(f"Reinforcement {p.rebar_grade}", "Steel")

        host_elems, sub_elems, fnd_elems = {}, [], []
        concrete_m3 = 0.0

        for pr in lay.pier_caps:
            e, v = self._pier_cap(pr); host_elems[pr.name] = e; sub_elems.append(e); concrete_m3 += v
        for c in lay.piers:
            e, v = self._cylinder_member(c, "IfcColumn", {"PredefinedType": "COLUMN"},
                                         self.storey_sub, self._concrete, {})
            host_elems[c.name] = e; sub_elems.append(e); concrete_m3 += v
        for b in lay.pile_caps:
            e, v = self._pile_cap(b); host_elems[b.name] = e; fnd_elems.append(e); concrete_m3 += v
        for c in lay.piles:
            e, v = self._cylinder_member(
                c, "IfcPile", {"PredefinedType": "BORED"}, self.storey_fnd, self._concrete,
                {"PileLength_mm": c.height})
            host_elems[c.name] = e; fnd_elems.append(e); concrete_m3 += v

        # --- rebar cages: one IfcElementAssembly per host member
        steel_kg = 0.0
        cages = {}
        for bar in lay.rebar:
            cages.setdefault((bar.host, bar.comp), []).append(bar)
        for (host, comp), bars in cages.items():
            parts, cage_mass = [], 0.0
            for bar in bars:
                e, m = self._bar(bar); parts.append(e); cage_mass += m
            steel_kg += cage_mass
            cage = self.f.createIfcElementAssembly(
                create_ifc_guid(), self.oh, Name=f"{host} Reinforcement Cage",
                ObjectPlacement=self._place((0, 0, 0), parent=self._storey_of(comp).ObjectPlacement),
                AssemblyPlace="SITE", PredefinedType="USERDEFINED",
                ObjectType="REINFORCEMENT_CAGE")
            self.f.createIfcRelAggregates(create_ifc_guid(), self.oh, RelatingObject=cage, RelatedObjects=parts)
            self._qto(cage, "Qto_ReinforcementCageQuantities",
                      [("C", "BarCount", len(parts)), ("W", "TotalWeight", cage_mass)])
            (sub_elems if comp in ("pier", "pier_cap") else fnd_elems).append(cage)

        self._contain(self.storey_sub, sub_elems)
        self._contain(self.storey_fnd, fnd_elems)

        for name, elems in self._mat_members.items():
            if elems:
                self.f.createIfcRelAssociatesMaterial(create_ifc_guid(), self.oh, None, None,
                                                      elems, self._materials[name])
        self.counts.update(concrete_m3=concrete_m3, rebar_kg=steel_kg)
        return self.counts


def add_substructure_to_ifc(generator, layout):
    """Entry point used by BridgeIfcGenerator.generate_from_extracted_data()."""
    return SubstructureIfcWriter(generator, layout).write()

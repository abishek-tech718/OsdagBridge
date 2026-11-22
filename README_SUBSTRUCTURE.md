# OsdagBridge – Parametric Substructure + IFC

## Data flow
```
SubstructureParams ──► layout.py (pure python, no OCC)  ──┬─► pier/builder.py       ┐
 (all dims, mm)         build_substructure_layout()       │   pier_cap/builder.py   ├─► sub_structure/builder.py
                        = list of Cylinder/Box/Prism/     │   pile_cap/builder.py   │     build_substructure()
                          Bar/RingBar descriptors         │   pile/builder.py       ┘            │
                                                          │                         cad_generator.generate() → model_data
                                                          │                                      │
                                                          │                         cad_3d.py  (semi-transparent concrete,
                                                          │                                     opaque rebar, "Substructure" checkbox)
                                                          └─► ifc_export_bridge/substructure_ifc.py
                                                                 ▲ called from bridge_ifc_generator.generate_from_extracted_data()
                                                                 ▲ layout supplied by bridge_cad_extraction._extract_substructure()
```
One layout feeds both the CAD window and the IFC → they cannot disagree.

## Files
| File | Role |
|---|---|
| `core/bridge_components/sub_structure/layout.py` | Parameters + all geometry/rebar logic (testable without GUI) |
| `core/bridge_components/sub_structure/occ_helpers.py` | pythonOCC primitives (cylinder, box, oblique prism, bars, hoops) |
| `.../sub_structure/pier/builder.py`, `pier_cap/builder.py` | pythonOCC pier & pier cap (+ rebar) |
| `.../foundation/pile_cap/builder.py`, `pile/builder.py` | pythonOCC pile cap & piles (+ rebar) |
| `.../sub_structure/builder.py` | Assembles the four builders into `model_data` entries |
| `core/bridge_types/plate_girder/cad_generator.py` | STEP 8 calls `build_substructure()`; keys `pier_caps, piers, pile_caps, piles, substructure_rebar` |
| `desktop/ui/cad_3d.py` | Displays them (concrete opacity 0.35, rebar opaque) + "Substructure" checkbox + hover text |
| `core/ifc_export_bridge/substructure_ifc.py` | IFC4 writer |
| `core/ifc_export_bridge/bridge_cad_extraction.py` / `bridge_ifc_generator.py` | 2 small hooks (extract + write) |
| `tests/test_substructure.py` | 12 tests (layout, cover, parametrics, IFC entities, BOQ quantities, bar geometry) |

## Parameters (`SubstructureParams`, mm) – defaults are the task-sheet values
pier 800 × 3000 · cap top 3000 / bottom 1200 / depth 600 / length = deck width ·
pile cap 2200 × 1200 × 600 · piles 2×2, Ø400, L5000, 600 c/c ·
rebar 16 @150 main, 8 @200 transverse, cover 40.
Extras: `n_columns_per_bent`, `n_piles_x/y`, `pile_embedment`, `include_rebar`, `concrete_grade`, `rebar_grade`.
Override: `generator.substructure_params = SubstructureParams(pier_diameter=1000, ...)`.

## Coordinates
X span, Y transverse, Z up – the frame already used by the superstructure code
(x = 0…L, girder mid-depth z = 0). Skew is supported (cap axis & rebar sheared by y·tan(skew)).

## IFC mapping
Pier cap → IfcBeam · Pier → IfcColumn · Pile cap → IfcFooting(PILE_CAP) · Pile → IfcPile(BORED) ·
Rebar → IfcReinforcingBar (IfcSweptDiskSolid; hoops = IfcCircle directrix) grouped in IfcElementAssembly cages.
Storeys: Superstructure / Substructure / Foundation. IfcMaterial + IfcElementQuantity
(NetVolume, Length, Count, Weight) + Pset_OsdagSubstructure on every element → BOQ-ready.
Run: File → Save 3D CAD Model → *.ifc

---
name: material-takeoff
description: Parse CAD/BIM/PDF files (IFC, DXF, DWG, Bluebeam PDF, raw blueprint PDF, CSV) and generate a structured material takeoff table with counts of doors, windows, outlets, and other building components.
---

## When to use
- User has a construction/architectural drawing file and wants to count materials (doors, windows, outlets, fixtures, etc.)
- User says "count the windows/doors/outlets", "material takeoff", "quantity survey", "bill of materials from this drawing"
- Input can be .ifc, .dxf, .dwg, .pdf (Bluebeam or raw), .csv, .xlsx
- Prefer over manual counting or manual spreadsheet entry for any construction takeoff

## Steps

### 1. Detect file format and route

Check the file extension and probe the file:

```
.ifc  → IFC parser (ifcopenshell)
.dxf  → DXF parser (ezdxf)
.dwg  → Try ezdxf first. If it fails → QCAD dwg2svg → SVG geometry parser (see Step 3b)
.pdf  → Probe for annotations (pymupdf):
        - Has Bluebeam markups → Bluebeam PDF parser
        - No annotations → Raw blueprint OCR parser
.csv  → Direct CSV import
.xlsx → Convert to CSV or read via openpyxl, then import
```

### 2. Dependencies

This skill has an isolated Python environment managed by Nexus. After calling `skill_view(name="material-takeoff")`, use the `python.path` from the response to run scripts that import `ifcopenshell`, `ezdxf`, `pymupdf`, or `openpyxl`.

For DWG files that ezdxf can't read, install QCAD:
```bash
# macOS
brew install --cask qcad

# Linux — download trial from https://www.ribbonsoft.com/en/qcad-downloads
# Then: chmod +x qcad*.run && ./qcad*.run
# CLI tool at: /opt/qcad*/dwg2svg or similar
```

### 3. Parse based on format

#### 3a. IFC path (richest, most accurate)

```python
import ifcopenshell

model = ifcopenshell.open(file_path)

IFC_CATEGORIES = {
    "IfcDoor": "Door",
    "IfcWindow": "Window",
    "IfcElectricAppliance": "Electric Appliance",
    "IfcFlowTerminal": "Fixture/Outlet",
    "IfcLightFixture": "Light Fixture",
    "IfcSwitchingDevice": "Switch",
    "IfcTransportElement": "Elevator/Stair",
    "IfcFurniture": "Furniture",
    "IfcSanitaryTerminal": "Sanitary Fixture",
    "IfcFireSuppressionTerminal": "Fire Suppression",
    "IfcPipeSegment": "Pipe",
    "IfcDuctSegment": "Duct",
    "IfcCableSegment": "Cable",
    "IfcBuildingElementProxy": "Building Element (Proxy)",
}

for ifc_type, category in IFC_CATEGORIES.items():
    elements = model.by_type(ifc_type)
    for el in elements:
        name = el.Name or "Unnamed"
        if el.is_a("IfcElement"):
            rels = el.IsDefinedBy
            # Extract properties, material, quantity sets
        # Get level/floor via ContainedInStructure
```

- Confidence: **exact**

#### 3b. DWG → SVG fallback path (when ezdxf can't read the DWG)

Use this when `ezdxf.readfile(dwg_path)` raises an exception (unsupported DWG version).

**Step 3b-1: Convert DWG to SVG via QCAD**

```bash
# Find dwg2svg binary
DWG2SVG=$(find / -name "dwg2svg" -type f 2>/dev/null | head -1)
# Common paths: /opt/qcad*/dwg2svg, /Applications/QCAD.app/Contents/MacOS/dwg2svg

$DWG2SVG -f -o /tmp/floor.svg /path/to/input.dwg
# -f = force overwrite, -o = output path
```

**Step 3b-2: Run the SVG geometry analysis script**

Run this via `terminal` with a heredoc (`python3 << 'PYEOF' ... PYEOF`).
The script handles coordinate normalization, entity classification, and room counting in one pass.

```python
import re, math
from collections import Counter, defaultdict

with open('/tmp/floor.svg', 'r') as f:
    content = f.read()

# Extract viewBox for coordinate normalization
vb_match = re.search(r'viewBox="([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)"', content)
VB_X = float(vb_match.group(1))
VB_Y = float(vb_match.group(2))

def norm(x, y):
    """Convert absolute SVG coords to drawing-relative coords."""
    return (x - VB_X, y - VB_Y)

# Parse entities: QCAD SVG uses <!--EntityType--> comments before each <path>
segments = re.split(r'(<!--\s*\w+\s*-->)', content)
entities = []
current_type = None
for seg in segments:
    type_match = re.match(r'<!--\s*(\w+)\s*-->', seg)
    if type_match:
        current_type = type_match.group(1)
    elif current_type:
        path_match = re.search(r'<path d="([^"]+)"', seg)
        color_match = re.search(r'stroke:(#[0-9a-fA-F]+)', seg)
        fill_match = re.search(r'fill:(#[0-9a-fA-F]+)', seg)
        if path_match:
            entities.append({
                'type': current_type,
                'path': path_match.group(1),
                'color': color_match.group(1) if color_match else (fill_match.group(1) if fill_match else 'none'),
            })

# ---- LINE ANALYSIS ----
lines = []
for e in entities:
    if e['type'] != 'Line':
        continue
    nums = [float(x) for x in re.findall(r'[-\d.]+', e['path'])]
    if len(nums) >= 4:
        x1, y1 = norm(nums[0], nums[1])
        x2, y2 = norm(nums[2], nums[3])
        length = math.sqrt((x2-x1)**2 + (y2-y1)**2)
        lines.append({'length': length, 'color': e['color']})

# ---- HATCH (ROOM) ANALYSIS ----
hatches = []
for e in entities:
    if e['type'] != 'Hatch':
        continue
    nums = [float(x) for x in re.findall(r'[-\d.]+', e['path'])]
    pts = []
    for i in range(0, len(nums)-1, 2):
        pts.append(norm(nums[i], nums[i+1]))
    if len(pts) >= 3:
        w = max(p[0] for p in pts) - min(p[0] for p in pts)
        h = max(abs(p[1]) for p in pts) - min(abs(p[1]) for p in pts)
        cx = sum(p[0] for p in pts)/len(pts)
        cy = sum(abs(p[1]) for p in pts)/len(pts)
        hatches.append({'width': w, 'height': h, 'cx': cx, 'cy': cy, 'color': e['color']})

# Cluster hatches into unique room regions (multiple patterns overlap same room)
unique_regions = []
used = set()
for i in range(len(hatches)):
    if i in used:
        continue
    h1 = hatches[i]
    cluster = [i]
    for j in range(i+1, len(hatches)):
        if j in used:
            continue
        h2 = hatches[j]
        if abs(h1['cx'] - h2['cx']) < 100 and abs(h1['cy'] - h2['cy']) < 100:
            cluster.append(j)
            used.add(j)
    used.add(i)
    unique_regions.append({
        'cx': h1['cx'], 'cy': h1['cy'],
        'width': max(hatches[k]['width'] for k in cluster),
        'height': max(hatches[k]['height'] for k in cluster),
        'patterns': len(cluster),
        'colors': list(set(hatches[k]['color'] for k in cluster))
    })

# ---- CLASSIFICATION HEURISTICS ----
wall_lines = [l for l in lines if l['length'] > 100]
feature_lines = [l for l in lines if 20 < l['length'] <= 100]
detail_lines = [l for l in lines if l['length'] <= 20]
door_scale_lines = [l for l in lines if l['color'] == '#0000ff' and 15 < l['length'] < 50]
estimated_doors = len(door_scale_lines) // 2

large_rooms = [r for r in unique_regions if r['width']*r['height'] > 200000]
medium_rooms = [r for r in unique_regions if 50000 < r['width']*r['height'] <= 200000]
small_rooms = [r for r in unique_regions if r['width']*r['height'] <= 50000]

# ---- OUTPUT ----
print(f"=== MATERIAL TAKEOFF ===")
print(f"Total entities: {len(entities)}")
print(f"Lines: {len(lines)} (wall: {len(wall_lines)}, feature: {len(feature_lines)}, detail: {len(detail_lines)})")
print(f"Hatch regions: {len(unique_regions)} ({len(large_rooms)} L, {len(medium_rooms)} M, {len(small_rooms)} S)")
print(f"Estimated doors: {estimated_doors}")
print(f"Hatch colors: {dict(Counter(h['color'] for h in hatches))}")
```

- Confidence: **approximate** — no block names or text labels from SVG export

#### 3c. DXF path

```python
import ezdxf

doc = ezdxf.readfile(file_path)
msp = doc.modelspace()

block_counts = {}
for entity in msp.query("INSERT"):
    block_name = entity.dxf.name.upper()
    layer = entity.dxf.layer
    block_counts[block_name] = block_counts.get(block_name, 0) + 1

# Group by block name → categorize (door/window/outlet/other)
```

- Confidence: **exact** for named blocks

#### 3d. Bluebeam PDF path

```python
import fitz  # pymupdf

doc = fitz.open(pdf_path)
markups = []
for page_num, page in enumerate(doc):
    annots = page.annots()
    if not annots:
        continue
    for annot in annots:
        info = annot.info
        markups.append({
            "subject": info.get("subject", ""),
            "title": info.get("title", ""),
            "content": info.get("content", ""),
            "page": page_num + 1,
        })
# Group by subject → material category
```

- Confidence: **exact**

#### 3e. Raw blueprint PDF (OCR fallback)

Use `ocr_image` tool or pymupdf text extraction. Confidence: **approximate**.

#### 3f. CSV/Excel

Use `vault_csv` or pandas. Auto-detect columns. Confidence: **exact**.

### 4. Normalize to common schema

Every parser outputs rows matching this schema:

| field       | type   | description                          |
|-------------|--------|--------------------------------------|
| item        | text   | Category (Door, Window, Outlet...)   |
| subtype     | text   | Specific model/type if available     |
| quantity    | number | Count                                |
| level       | text   | Floor/level/page                     |
| material    | text   | Material if specified                |
| dimensions  | text   | Size if available                    |
| notes       | text   | Any additional info                  |
| source_file | text   | Original file name                   |
| format      | text   | IFC/DXF/DWG-SVG/PDF-BLUEBEAM/PDF-RAW/CSV |
| confidence  | text   | exact / approximate / manual         |

### 5. Create vault data-table

Create at `takeoffs/<project-slug>.md` using `datatable_manage action=create_table` then `add_rows`.

### 6. Print summary report

- Total items counted, breakdown by category and level
- Confidence note: which rows are exact vs approximate
- Flag unmapped/unknown items
- Link to vault data-table

## Gotchas

- **DWG binary is the hardest format.** ezdxf supports some DWG versions but many fail (especially AC1027+). Reliable fallback: QCAD `dwg2svg` → SVG geometry parser. Always try ezdxf first, then fall back to SVG.
- **SVG export loses text/labels.** MTEXT, TEXT, dimension annotations are NOT in QCAD SVG output. Room names, dimensions, block names — all lost. SVG-derived takeoffs are always "approximate" confidence.
- **SVG coordinate normalization is critical.** The SVG uses absolute coordinates in the millions (e.g., 69221000). You MUST extract the `viewBox` attribute and subtract its origin (x, y) from all parsed coordinates. Without this, area calculations produce trillion-scale nonsense.
- **SVG Y-axis is inverted.** QCAD applies `scale(1,-1)` to the group. Use `abs(p[1])` for Y when computing bounding boxes from polyline/hatch points.
- **Hatch patterns overlap in the same room.** A single room has 2–8 overlapping hatch entities (different fill patterns). Cluster by center-point proximity (<100 units) to count distinct rooms, not individual hatches.
- **Polylines also use absolute coords.** Apply `norm()` to polyline/hatch points too, not just lines. Width/height must be computed from normalized points.
- **Color is the only layer proxy in SVG.** Layer names are lost. Stroke color is your only indicator: blue (#0000ff) = primary structure, green (#00ff00) = secondary, red (#ff0000) = tertiary.
- **IFC type names vary by exporter.** Normalize via `IfcTypeObject`; fall back to class name. Some use `IfcBuildingElementProxy` for everything — keyword-match the `Name` field.
- **DXF block names are firm-specific.** No standard naming. List all blocks on first run, let user map categories. Cache the mapping.
- **Bluebeam annotations can be lost** if PDF was re-saved through non-Bluebeam tool. Check annotation count first; if zero, warn and fall back to OCR.
- **PDF OCR is approximate.** Flag every row. Never trust as final.
- **CSV column names vary wildly.** Use fuzzy matching; confirm with user if ambiguous.
- **Large IFC files (>50MB).** Warn user; consider filtering by floor/zone.
- **Confidence tracking is critical.** IFC/DXF = exact. Bluebeam markups = exact. SVG-derived = approximate. OCR = approximate. Always tag rows.
- **Multiple files per project.** Handle each separately, merge into one table with level breakdown.

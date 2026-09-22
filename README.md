# Stormwater Drainage Plot and Design

A QGIS plugin for stormwater drainage engineering. It plots TUFLOW pipe and pit
layers as a long section, and lets you design new pipe runs directly on that
profile.

Two halves, one dock:

- **Plot** an existing network — trace upstream from a selected pipe and see
  the ground, conduit band, invert line and pits on a long section.
- **Design** a new one — draw pipes with QGIS's normal digitising tools and get
  inverts graded automatically from minimum cover, diameter and slope.

---

## Features

**Plotting**

- Reads TUFLOW-style `1d_nwk` pipe and pit attributes.
- Traces upstream from the selected downstream pipe, branching into one **Path**
  per tributary.
- Multiple pipe layers and multiple pit layers at once, with connectivity
  snapped **across** layers.
- Ground line sampled from a DEM at the raster cell size, along the real
  digitised alignment (not just endpoints).
- Paths are right-aligned on a shared outlet chainage, so the outlet stays put
  when you switch between paths.

**Interaction**

- Hover a pipe for its full attribute popup (ID, type, size, inverts).
- Hover the ground line to track that position on the map with a marker.
- Click a pipe in the chart to highlight and zoom to it, like Identify.
- Optional crosshair with a live X/Y readout that tracks every mouse pixel.
- Toggles for network labels, legend and highlight scope.

**Design**

- **Start pipe layer** creates a temporary layer carrying the design schema,
  pre-styled with flow-direction arrows and `Pipe_ID (Width)` labels.
- **Draw pipes** turns on snapping and starts QGIS's Add Line tool — every line
  you draw is one pipe. No custom map tool, so you keep tracing, vertex editing
  and undo.
- Inverts are graded automatically: the first pipe starts at minimum cover, each
  following pipe starts at its predecessor's outlet or deeper if cover demands
  it, so the line only ever drops.
- A live table stays in sync with the layer — add, move, edit or delete a
  feature anywhere and the table and profile follow.
- Edit `Pipe_ID`, `Pipe_Type`, `Width`, `Height`, `No_of_Pipe`, `Slope_pct`,
  `US_Invert` or `DS_Invert` in the table and the layer updates through QGIS's
  edit buffer (undo works).
- **Recalculate inverts** re-grades the whole chain after you move the alignment
  or change the design settings. A per-feature `Slope_pct` overrides the dialog
  default, so one pipe can run at a steeper grade.

**Export**

- Export the plotted profile (traced *or* designed) to two CSVs:
  - `*_profile.csv` — paired X/Y columns (ground, invert, obvert, pits), ready
    to chart directly in Excel.
  - `*_pipes.csv` — per-pipe band data for long-section drafting: chainage,
    length, surface and invert levels, obverts, fall, grade as % and 1-in-X,
    cover and depth to invert.

---

## Requirements

- QGIS 3.40+ (Qt5 or Qt6).
- Python package **plotly** — install into the QGIS Python environment:

  ```
  python -m pip install plotly
  ```

- A web view: **QtWebKit** (`QWebView`) or **QtWebEngine**. The plugin prefers
  QtWebKit, which is the stable path inside QGIS LTR.
- Plotly's JavaScript is downloaded once on first use and cached under your temp
  folder, so an internet connection is needed for that first plot only.

## Installation

1. Copy the `pipe_plotter` folder into your QGIS plugins directory:

   - Windows — `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - Linux — `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - macOS — `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`

2. Restart QGIS, then enable **Stormwater Drainage Plot and Design** in
   *Plugins → Manage and Install Plugins*.
3. Open it from its toolbar button or *Plugins → Stormwater Drainage Plot and
   Design*.

---

## Usage

### Plotting an existing network

1. **Settings tab** — add your pipe layer(s) with the `+` button and map the
   fields. Do the same for pit layers if you have them. Pick a **Ground (DEM)**
   raster.
2. Choose a **Mode**:
   - *Trace upstream from selected pipe* — select the downstream-most pipe.
   - *Plot selected pipes only* — select an unbroken chain of pipes.
3. Select the pipe(s) on the map with QGIS's normal selection tool.
4. Click the green **Run Plot** button (top right of the panel).
5. Pick a **Path** from the list to plot it.

#### Expected pipe fields

| Field | Purpose |
| --- | --- |
| `ID` | Label shown on the plot and in hover popups |
| `Type` | `C` circular, `R`/`B` box — decides which dimension is the rise |
| `US_Invert` / `DS_Invert` | Upstream and downstream invert levels |
| `Width` | Diameter (circular) or width (box), in metres |
| `Height` | Box height, in metres |
| `Number_of` | Barrel count — shown in the tooltip only |

#### Expected pit fields

| Field | Purpose |
| --- | --- |
| `ID` | Label |
| `Type` | Only `Q` features are used; `node` and others are ignored |
| `US_Invert` | Top of pit — absolute level if `Conn_1D_2D` is `SX`, otherwise an offset from ground |
| `DS_Invert` | Bottom of pit; `-99999` means "follow the downstream pipe's US invert" |
| `Conn_1D_2D` | `SX` switches `US_Invert` from a ground offset to an absolute level |
| `Inlet_Type`, `Number_of` | Tooltip only |

### Designing a new pipe run

1. **Settings tab** — pick a **Ground (DEM)**. No pipe layers are needed.
2. **Design tab** — set **Cover (m)**, **Diameter (mm)** and **Slope (%)**.
3. Click **Start pipe layer**. A styled, empty design layer is created and
   digitising begins.
4. Draw one line per pipe. Snapping is on, so start each pipe on the end of the
   previous one. Each finished pipe is graded and plotted immediately.
5. Adjust anything in the table, or edit the layer in QGIS directly.
6. Click **Recalculate inverts** to re-grade the whole chain, and **Save edits**
   when you're done.

Use the **Design layer** combo to switch between several designs in one project.

#### Design layer schema

| Field | Editable | Notes |
| --- | --- | --- |
| `Seq` | – | Position along the chain |
| `Pipe_ID` | yes | Auto-filled `P1`, `P2`, … when blank |
| `Pipe_Type` | yes | `C` circular, `R` box |
| `Width` | yes | Diameter or box width, in metres |
| `Height` | yes | Box height; `0` for circular pipes |
| `No_of_Pipe` | yes | Barrel count |
| `Slope_pct` | yes | Per-pipe grade; overrides the dialog default |
| `Ch_US`, `Ch_DS`, `Length` | – | Derived from the geometry |
| `US_Invert`, `DS_Invert` | yes | Graded automatically, overridable |
| `Cover_US`, `Cover_DS` | – | Ground level minus obvert |

The plugin table shows exactly these fields, in this order, so it never
disagrees with QGIS's attribute table.

### Exporting

Click the export button beside **Run Plot**, choose a filename, and both CSVs
are written side by side. In Excel, select a chainage/level column pair and
insert a scatter chart with straight lines.

---

## Notes and limitations

- Layers are assumed to share the project CRS; no reprojection is applied.
- The upstream trace expects pipes digitised in the flow direction — first
  vertex upstream, last vertex downstream.
- The design chain is a single unbranched run. Branches and gaps are detected
  and the unreachable pipes are listed last rather than scrambling the chainage.
- Extending a design **upstream** shifts every chainage, so newly added pipes
  are graded in isolation until you press **Recalculate inverts**.
- Hydraulic grade line, pipe capacity and velocity are not calculated yet.
- The design layer is a memory layer. Export it or save it to a real format
  before closing the project.

## Licence

GPL-2.0-or-later, matching QGIS.

# -*- coding: utf-8 -*-
"""Design mode: an editable pipe layer drawn with QGIS's own line tool.

Every line feature is one pipe. Inverts are derived from the sampled ground,
a minimum cover and a grade, then written to the layer as ordinary attributes
- so they can be edited by hand (here or in QGIS's attribute table) and
recalculated whenever the alignment or the design settings change.
"""

from dataclasses import replace

from qgis.core import (
    Qgis,
    QgsDefaultValue,
    QgsEditFormConfig,
    QgsField,
    QgsLineSymbol,
    QgsMarkerLineSymbolLayer,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsSimpleLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

from .chainage import elevation_at_chainage
from .network_graph import PipeRecord

LAYER_NAME = "Design pipes"
LINE_COLOR = "#8c2d4b"

FIELD_SEQ = "Seq"
FIELD_ID = "Pipe_ID"
FIELD_TYPE = "Pipe_Type"
FIELD_WIDTH = "Width"
FIELD_HEIGHT = "Height"
FIELD_NUMBER = "No_of_Pipe"
FIELD_SLOPE = "Slope_pct"
FIELD_CH_US = "Ch_US"
FIELD_CH_DS = "Ch_DS"
FIELD_LENGTH = "Length"
FIELD_US = "US_Invert"
FIELD_DS = "DS_Invert"
FIELD_COVER_US = "Cover_US"
FIELD_COVER_DS = "Cover_DS"

# The design table shows exactly these, in this order, so the attribute table
# and the plugin table never disagree.
COLUMN_HEADERS = [
    FIELD_SEQ, FIELD_ID, FIELD_TYPE, FIELD_WIDTH, FIELD_HEIGHT, FIELD_NUMBER,
    FIELD_SLOPE, FIELD_CH_US, FIELD_CH_DS, FIELD_LENGTH, FIELD_US, FIELD_DS,
    FIELD_COVER_US, FIELD_COVER_DS,
]
REQUIRED_FIELDS = tuple(COLUMN_HEADERS)
# Table column -> layer field. Everything else is derived from the geometry
# and the ground, so it's written by the tool and shown read-only.
EDITABLE_COLUMNS = {
    1: FIELD_ID,
    2: FIELD_TYPE,
    3: FIELD_WIDTH,
    4: FIELD_HEIGHT,
    5: FIELD_NUMBER,
    6: FIELD_SLOPE,
    10: FIELD_US,
    11: FIELD_DS,
}
TEXT_FIELDS = (FIELD_ID, FIELD_TYPE)
INT_FIELDS = (FIELD_NUMBER,)


def enum_attr(owner, *paths):
    """First existing attribute among dotted ``paths``.

    QGIS shuffled several symbology/labelling/snapping enums into scoped
    classes between versions, so look them up by name rather than importing.
    """
    for path in paths:
        value = owner
        for part in path.split("."):
            value = getattr(value, part, None)
            if value is None:
                break
        if value is not None:
            return value
    return None


def _to_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _rise(record):
    """Obvert-above-invert height, matching plot_builder's conduit band."""
    text = str(record.pipe_type or "").strip().upper()
    if (text.startswith("R") or text.startswith("B")) and record.height:
        return record.height
    return record.width_or_diameter or record.height or 0.0


def missing_fields(layer):
    """Design fields absent from ``layer`` - empty means it's usable here."""
    names = {field.name() for field in layer.fields()}
    return [name for name in REQUIRED_FIELDS if name not in names]


def create_design_layer(crs_authid, diameter, slope_pct, name=LAYER_NAME):
    """Empty memory line layer carrying this tool's pipe schema."""
    layer = QgsVectorLayer(f"LineString?crs={crs_authid}", name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes([
        QgsField(FIELD_SEQ, QVariant.Int),
        QgsField(FIELD_ID, QVariant.String),
        QgsField(FIELD_TYPE, QVariant.String),
        QgsField(FIELD_WIDTH, QVariant.Double),
        QgsField(FIELD_HEIGHT, QVariant.Double),
        QgsField(FIELD_NUMBER, QVariant.Int),
        QgsField(FIELD_SLOPE, QVariant.Double),
        QgsField(FIELD_CH_US, QVariant.Double),
        QgsField(FIELD_CH_DS, QVariant.Double),
        QgsField(FIELD_LENGTH, QVariant.Double),
        QgsField(FIELD_US, QVariant.Double),
        QgsField(FIELD_DS, QVariant.Double),
        QgsField(FIELD_COVER_US, QVariant.Double),
        QgsField(FIELD_COVER_DS, QVariant.Double),
    ])
    layer.updateFields()
    configure_for_digitising(layer, diameter, slope_pct)
    apply_design_style(layer)
    return layer


def apply_design_style(layer):
    """Maroon flow-direction line labelled ``ID (Width)``."""
    try:
        _apply_symbology(layer)
        _apply_labels(layer)
    except Exception:  # noqa: BLE001 - styling must never block layer creation
        return
    layer.triggerRepaint()


def _arrow_symbol(color):
    shape = enum_attr(
        QgsSimpleMarkerSymbolLayer, "Shape.ArrowHeadFilled", "ArrowHeadFilled",
    ) or enum_attr(Qgis, "MarkerShape.ArrowHeadFilled")
    marker = QgsSimpleMarkerSymbolLayer(shape, 3.0) if shape is not None \
        else QgsSimpleMarkerSymbolLayer()
    marker.setColor(color)
    marker.setStrokeColor(color)
    return QgsMarkerSymbol([marker])


def _arrow_line(color, placement_name):
    line = QgsMarkerLineSymbolLayer()
    placement = enum_attr(QgsMarkerLineSymbolLayer, placement_name) or enum_attr(
        Qgis, f"MarkerLinePlacement.{placement_name}"
    )
    if placement is not None:
        # Prefer the singular setter: the plural one wants scoped flag types.
        setter = getattr(line, "setPlacement", None) or getattr(line, "setPlacements", None)
        if setter is not None:
            try:
                setter(placement)
            except TypeError:
                line.setSubSymbol(_arrow_symbol(color))
                return line
    line.setSubSymbol(_arrow_symbol(color))
    return line


def _apply_symbology(layer):
    color = QColor(LINE_COLOR)
    symbol = QgsLineSymbol([QgsSimpleLineSymbolLayer(color, 1.0)])
    symbol.appendSymbolLayer(_arrow_line(color, "LastVertex"))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def _apply_labels(layer):
    settings = QgsPalLayerSettings()
    settings.fieldName = (
        f'"{FIELD_ID}" || \' (\' || format_number("{FIELD_WIDTH}", 3) || \')\''
    )
    settings.isExpression = True
    placement = enum_attr(QgsPalLayerSettings, "Placement.Line", "Line")
    if placement is not None:
        settings.placement = placement
    settings.dist = 1.5

    text_format = QgsTextFormat()
    text_format.setSize(9)
    text_format.setColor(QColor("#1a1a1a"))
    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(0.8)
    buffer_settings.setColor(QColor("white"))
    text_format.setBuffer(buffer_settings)
    settings.setFormat(text_format)

    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def configure_for_digitising(layer, diameter, slope_pct):
    """Give new features sensible attributes and skip the attribute form.

    Lets the user keep drawing further segments with QGIS's own line tool
    (and its snapping) without filling in a form for every pipe.
    """
    fields = layer.fields()
    defaults = {
        FIELD_TYPE: "'C'",
        FIELD_WIDTH: repr(float(diameter)),
        # Only meaningful for box culverts (Type R) - left at 0 for circular pipes.
        FIELD_HEIGHT: "0",
        FIELD_NUMBER: "1",
        FIELD_SLOPE: repr(float(slope_pct)),
    }
    for name, expression in defaults.items():
        index = fields.indexOf(name)
        if index >= 0:
            layer.setDefaultValueDefinition(index, QgsDefaultValue(expression))

    suppress = getattr(
        getattr(QgsEditFormConfig, "FeatureFormSuppress", QgsEditFormConfig),
        "SuppressOn", None,
    )
    if suppress is not None:
        form_config = layer.editFormConfig()
        form_config.setSuppress(suppress)
        layer.setEditFormConfig(form_config)


def apply_attribute_changes(layer, updates):
    """Write ``{fid: {index: value}}``, joining any edit session already open.

    ``qgis.core.edit`` asserts on ``startEditing()``, which returns False when
    the layer is already editable - so it can't be used once the user is
    digitising extra segments into this layer.
    """
    if not updates:
        return
    was_editing = layer.isEditable()
    if not was_editing:
        layer.startEditing()
    for fid, values in updates.items():
        layer.changeAttributeValues(fid, values)
    if was_editing:
        layer.triggerRepaint()
    else:
        layer.commitChanges()


def _geometry_vertices(geometry):
    if geometry is None or geometry.isEmpty():
        return []
    if geometry.isMultipart():
        parts = geometry.asMultiPolyline()
        return list(parts[0]) if parts else []
    return list(geometry.asPolyline())


def records_from_layer(layer, tolerance=0.05):
    """PipeRecords for the design layer, ordered upstream -> downstream.

    Features are chained by matching endpoints rather than trusting feature
    order, so extra segments snapped on later (at either end, digitised in
    either direction) still profile as one continuous line.
    """
    records = []
    for index, feature in enumerate(sorted(layer.getFeatures(), key=lambda f: f.id())):
        vertices = _geometry_vertices(feature.geometry())
        if len(vertices) < 2:
            continue
        records.append(
            PipeRecord(
                fid=feature.id(),
                id_label=str(feature[FIELD_ID] or f"P{index + 1}"),
                layer=layer,
                start_node=index,
                end_node=index + 1,
                start_point=vertices[0],
                end_point=vertices[-1],
                vertices=vertices,
                length=feature.geometry().length(),
                us_invert=_to_float(feature[FIELD_US]),
                ds_invert=_to_float(feature[FIELD_DS]),
                pipe_type=str(feature[FIELD_TYPE] or "C"),
                width_or_diameter=_to_float(feature[FIELD_WIDTH]),
                height=_to_float(feature[FIELD_HEIGHT]),
                number_of=_to_float(feature[FIELD_NUMBER]) or 1,
            )
        )
    return order_records(records, tolerance)


def _node_key(point, tolerance):
    return (round(point.x() / tolerance), round(point.y() / tolerance))


def _single_unused(at_node, used):
    """The one unused record meeting at this node, or None at a gap/branch."""
    candidates = [(rec, side) for rec, side in at_node if id(rec) not in used]
    return candidates[0] if len(candidates) == 1 else None


def _flipped(record):
    vertices = list(reversed(record.vertices))
    return replace(
        record, vertices=vertices, start_point=vertices[0], end_point=vertices[-1]
    )


def order_records(records, tolerance=0.05):
    """Walk the chain outward from the first feature, flipping as needed."""
    if len(records) < 2:
        return records

    nodes = {}
    for record in records:
        nodes.setdefault(_node_key(record.start_point, tolerance), []).append((record, "start"))
        nodes.setdefault(_node_key(record.end_point, tolerance), []).append((record, "end"))

    anchor = records[0]
    used = {id(anchor)}

    upstream = []
    node = _node_key(anchor.start_point, tolerance)
    while True:
        found = _single_unused(nodes.get(node, []), used)
        if found is None:
            break
        record, side = found
        used.add(id(record))
        # Arriving at this node via the record's "start" means it was drawn backwards.
        flip = side == "start"
        upstream.append(_flipped(record) if flip else record)
        node = _node_key(record.end_point if flip else record.start_point, tolerance)
    upstream.reverse()

    downstream = []
    node = _node_key(anchor.end_point, tolerance)
    while True:
        found = _single_unused(nodes.get(node, []), used)
        if found is None:
            break
        record, side = found
        used.add(id(record))
        flip = side == "end"
        downstream.append(_flipped(record) if flip else record)
        node = _node_key(record.start_point if flip else record.end_point, tolerance)

    # Anything unreachable (a gap or a branch) keeps feature order, at the end.
    orphans = [record for record in records if id(record) not in used]
    return upstream + [anchor] + downstream + orphans


def recalculate_inverts(layer, records, us_stations, ground_xy, cover,
                        default_slope_pct, only_missing=False):
    """Re-derive US/DS inverts from ground, minimum cover and grade.

    The first pipe starts at minimum cover. Each following pipe starts at its
    predecessor's outlet, or deeper if minimum cover demands it - so the line
    only ever drops, never climbs back towards the surface. With
    ``only_missing`` set, pipes that already have inverts are left alone (used
    while digitising, so newly snapped-on segments grade themselves without
    discarding hand-edited levels).
    """
    fields = layer.fields()
    us_idx = fields.indexOf(FIELD_US)
    ds_idx = fields.indexOf(FIELD_DS)
    id_idx = fields.indexOf(FIELD_ID)
    slope_idx = fields.indexOf(FIELD_SLOPE)
    if us_idx < 0 or ds_idx < 0:
        return

    updates = {}
    previous_ds = None
    for index, (record, us_station) in enumerate(zip(records, us_stations)):
        feature = layer.getFeature(record.fid)
        values = {}
        if id_idx >= 0 and not str(feature[FIELD_ID] or "").strip():
            values[id_idx] = f"P{index + 1}"

        if only_missing and record.us_invert is not None and record.ds_invert is not None:
            previous_ds = record.ds_invert
        else:
            slope = _to_float(feature[FIELD_SLOPE]) if slope_idx >= 0 else None
            if slope is None:
                slope = default_slope_pct
            ground_us = elevation_at_chainage(ground_xy, us_station)
            limit = None if ground_us is None else ground_us - cover - _rise(record)

            if previous_ds is None:
                us_invert = limit if limit is not None else 0.0
            elif limit is None:
                us_invert = previous_ds
            else:
                us_invert = min(previous_ds, limit)
            ds_invert = us_invert - record.length * slope / 100.0

            values[us_idx] = round(us_invert, 3)
            values[ds_idx] = round(ds_invert, 3)
            previous_ds = ds_invert

        if values:
            updates[record.fid] = values

    apply_attribute_changes(layer, updates)


def _cover(ground, invert, rise):
    if ground is None or invert is None:
        return None
    return ground - (invert + rise)


def write_derived(layer, records, us_stations, ds_stations, ground_xy):
    """Push Seq/chainage/length/cover onto the layer so it mirrors the table.

    Only genuinely changed values are written, otherwise every refresh would
    re-dirty the layer straight after a save.
    """
    fields = layer.fields()
    indexes = {
        name: fields.indexOf(name)
        for name in (FIELD_SEQ, FIELD_CH_US, FIELD_CH_DS, FIELD_LENGTH,
                     FIELD_COVER_US, FIELD_COVER_DS)
    }
    if any(index < 0 for index in indexes.values()):
        return

    updates = {}
    for index, (record, us, ds) in enumerate(zip(records, us_stations, ds_stations)):
        feature = layer.getFeature(record.fid)
        rise = _rise(record)
        wanted = {
            FIELD_SEQ: index + 1,
            FIELD_CH_US: round(us, 3),
            FIELD_CH_DS: round(ds, 3),
            FIELD_LENGTH: round(record.length, 3),
            FIELD_COVER_US: _round(_cover(elevation_at_chainage(ground_xy, us), record.us_invert, rise)),
            FIELD_COVER_DS: _round(_cover(elevation_at_chainage(ground_xy, ds), record.ds_invert, rise)),
        }
        changed = {
            indexes[name]: value
            for name, value in wanted.items()
            if not _same(_to_float(feature[name]), value)
        }
        if changed:
            updates[record.fid] = changed
    apply_attribute_changes(layer, updates)


def _round(value, digits=3):
    return None if value is None else round(value, digits)


def _same(current, wanted):
    if current is None or wanted is None:
        return current is None and wanted is None
    return abs(current - wanted) < 1e-6


def table_rows(layer, records, us_stations, ds_stations, ground_xy):
    """Display/export rows, one per pipe, parallel to ``records``."""
    rows = []
    for index, (record, us, ds) in enumerate(zip(records, us_stations, ds_stations)):
        feature = layer.getFeature(record.fid)
        rise = _rise(record)
        cover_us = _cover(elevation_at_chainage(ground_xy, us), record.us_invert, rise)
        cover_ds = _cover(elevation_at_chainage(ground_xy, ds), record.ds_invert, rise)
        number = _to_float(feature[FIELD_NUMBER])

        rows.append([
            str(index + 1),
            record.id_label,
            str(feature[FIELD_TYPE] or ""),
            _text(record.width_or_diameter),
            _text(record.height),
            f"{int(number)}" if number else "1",
            _text(_to_float(feature[FIELD_SLOPE]), 2),
            _text(us, 2),
            _text(ds, 2),
            _text(record.length, 2),
            _text(record.us_invert),
            _text(record.ds_invert),
            _text(cover_us, 2),
            _text(cover_ds, 2),
        ])
    return rows


def _text(value, digits=3):
    return "-" if value is None else f"{value:.{digits}f}"

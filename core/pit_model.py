# -*- coding: utf-8 -*-
"""Reads pit (point) layers into plain :class:`PitRecord` objects.

Only "Q" type pits (actual pits) are kept - "node" type features are plain
junctions and are never drawn, per this plugin's convention.
"""

from dataclasses import dataclass


@dataclass
class PitRecord:
    fid: int
    id_label: str
    layer: object  # source QgsVectorLayer - fid is only unique WITHIN a layer
    point: object  # QgsPointXY
    pit_type: "str | None"
    us_invert: "float | None"
    ds_invert: "float | None"
    inlet_type: "str | None" = None
    number_of: "float | None" = None
    conn_1d_2d: "str | None" = None


def _to_float(value):
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    import math

    if math.isnan(result):
        return None
    return result


def build_pit_records(layer_specs):
    """Return ``(records, issues)`` for one or more pit layers.

    ``layer_specs`` mirrors ``network_graph.build_pipe_graph``'s: a list of
    dicts with ``{"layer", "id_field", "type_field", "us_field", "ds_field",
    "inlet_field", "number_field", "conn_field"}``. Only features whose type
    field equals "Q" (case-insensitive) are kept; "node" (junction) and any
    other value are skipped entirely - this plugin never draws junctions.
    """
    issues = []
    records = []

    for spec in layer_specs:
        layer = spec["layer"]
        fields = layer.fields()
        id_idx = fields.indexOf(spec.get("id_field")) if spec.get("id_field") else -1
        type_idx = fields.indexOf(spec.get("type_field")) if spec.get("type_field") else -1
        us_idx = fields.indexOf(spec.get("us_field")) if spec.get("us_field") else -1
        ds_idx = fields.indexOf(spec.get("ds_field")) if spec.get("ds_field") else -1
        inlet_idx = fields.indexOf(spec.get("inlet_field")) if spec.get("inlet_field") else -1
        number_idx = fields.indexOf(spec.get("number_field")) if spec.get("number_field") else -1
        conn_idx = fields.indexOf(spec.get("conn_field")) if spec.get("conn_field") else -1

        for feature in layer.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                issues.append(f"{layer.name()} fid {feature.id()} has no geometry - skipped.")
                continue

            pit_type = feature.attribute(type_idx) if type_idx >= 0 else None
            if str(pit_type or "").strip().upper() != "Q":
                continue  # "node" (junction) or anything else - never drawn

            point = geometry.asPoint() if not geometry.isMultipart() else None
            if point is None:
                multi = geometry.asMultiPoint()
                point = multi[0] if multi else None
            if point is None:
                issues.append(f"{layer.name()} fid {feature.id()} has invalid geometry - skipped.")
                continue

            id_value = feature.attribute(id_idx) if id_idx >= 0 else feature.id()
            id_label = str(id_value) if id_value not in (None, "") else str(feature.id())

            records.append(
                PitRecord(
                    fid=feature.id(),
                    id_label=id_label,
                    layer=layer,
                    point=point,
                    pit_type=pit_type,
                    us_invert=_to_float(feature.attribute(us_idx)) if us_idx >= 0 else None,
                    ds_invert=_to_float(feature.attribute(ds_idx)) if ds_idx >= 0 else None,
                    inlet_type=feature.attribute(inlet_idx) if inlet_idx >= 0 else None,
                    number_of=_to_float(feature.attribute(number_idx)) if number_idx >= 0 else None,
                    conn_1d_2d=feature.attribute(conn_idx) if conn_idx >= 0 else None,
                )
            )

    return records, issues


DS_INVERT_FOLLOW_PIPE = -99999  # sentinel meaning "use the connected pipe's invert"


@dataclass
class PlottedPit:
    """A pit resolved onto a specific path: real chainage + drawable elevations."""

    id_label: str
    chainage: float
    bottom: float
    top: float
    pit_type: "str | None"
    inlet_type: "str | None"
    number_of: "float | None"


def _pit_top(pit, ground_xy, chainage):
    """Top-of-pit level: absolute for Conn_1D_2D=="SX", else ground + offset."""
    conn = str(pit.conn_1d_2d or "").strip().upper()
    if conn == "SX":
        if pit.us_invert is not None:
            return pit.us_invert
        # SX with no defined level has nothing to fall back to except ground.
    from .chainage import elevation_at_chainage

    ground_elev = elevation_at_chainage(ground_xy, chainage)
    if ground_elev is None:
        return pit.us_invert  # no DEM sampled - best we can do
    offset = pit.us_invert if pit.us_invert is not None else 0.0
    return ground_elev + offset


def _pit_bottom(pit, upstream_pipe, downstream_pipe):
    """Bottom-of-pit level: the pit's own DS_Invert, unless it's the -99999
    "follow the pipe" sentinel (or missing), in which case use whichever
    pipe is actually connected - preferring the downstream pipe (the one
    leaving the pit) at a mid-chain junction."""
    if pit.ds_invert is not None and pit.ds_invert != DS_INVERT_FOLLOW_PIPE:
        return pit.ds_invert
    if downstream_pipe is not None and downstream_pipe.us_invert is not None:
        return downstream_pipe.us_invert
    if upstream_pipe is not None and upstream_pipe.ds_invert is not None:
        return upstream_pipe.ds_invert
    return None


def match_pits_to_path(pipes, us_stations, ds_stations, pit_records, ground_xy, tolerance=0.5):
    """Return ``(plotted_pits, issues)`` for every pit sitting on this path.

    A pit is matched to whichever of the path's node points (headwater,
    every inter-pipe junction, or the outlet) is within ``tolerance`` of it.
    """
    if not pipes or not pit_records:
        return [], []

    nodes = []  # (point, chainage, upstream_pipe, downstream_pipe)
    nodes.append((pipes[0].start_point, us_stations[0], None, pipes[0]))
    for i in range(1, len(pipes)):
        nodes.append((pipes[i].start_point, us_stations[i], pipes[i - 1], pipes[i]))
    nodes.append((pipes[-1].end_point, ds_stations[-1], pipes[-1], None))

    tolerance_sq = tolerance * tolerance
    issues = []
    plotted = []
    for pit in pit_records:
        best = None
        best_dist_sq = tolerance_sq
        for point, chainage, upstream_pipe, downstream_pipe in nodes:
            dist_sq = (pit.point.x() - point.x()) ** 2 + (pit.point.y() - point.y()) ** 2
            if dist_sq <= best_dist_sq:
                best_dist_sq = dist_sq
                best = (chainage, upstream_pipe, downstream_pipe)
        if best is None:
            continue  # not on this path

        chainage, upstream_pipe, downstream_pipe = best
        bottom = _pit_bottom(pit, upstream_pipe, downstream_pipe)
        if bottom is None:
            issues.append(f"Pit {pit.id_label}: no DS_Invert and no connected pipe invert - skipped.")
            continue
        top = _pit_top(pit, ground_xy, chainage)
        if top is None:
            issues.append(f"Pit {pit.id_label}: no US_Invert and no ground sampled - skipped.")
            continue

        plotted.append(
            PlottedPit(
                id_label=pit.id_label,
                chainage=chainage,
                bottom=bottom,
                top=top,
                pit_type=pit.pit_type,
                inlet_type=pit.inlet_type,
                number_of=pit.number_of,
            )
        )
    return plotted, issues

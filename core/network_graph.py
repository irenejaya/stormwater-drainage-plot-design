# -*- coding: utf-8 -*-
"""Builds a directed node/edge graph from one or more pipe (polyline) layers.

Digitised-direction convention (matches the flowtrace plugin already in this
workspace): the first vertex of a line is its upstream end, the last vertex
is its downstream end. Endpoints within ``tolerance`` of each other are
snapped together into a shared integer node id via grid-bucketed union-find,
so slightly-off-snapped networks still connect correctly - including across
different layers, since snapping runs on the combined endpoint set.
"""

from dataclasses import dataclass, field


@dataclass
class PipeRecord:
    fid: int
    id_label: str
    layer: object  # source QgsVectorLayer - fid is only unique WITHIN a layer
    start_node: int
    end_node: int
    start_point: object
    end_point: object
    vertices: list
    length: float
    us_invert: "float | None"
    ds_invert: "float | None"
    pipe_type: "str | None" = None
    width_or_diameter: "float | None" = None
    height: "float | None" = None
    number_of: "float | None" = None


@dataclass
class PipeGraph:
    layers: list = field(default_factory=list)
    records: list = field(default_factory=list)
    records_by_key: dict = field(default_factory=dict)  # (layer_id, fid) -> PipeRecord
    incoming: dict = field(default_factory=dict)  # node_id -> [PipeRecord] arriving at node
    outgoing: dict = field(default_factory=dict)  # node_id -> [PipeRecord] leaving node
    node_points: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)


class _UnionFind:
    def __init__(self, size):
        self._parent = list(range(size))

    def find(self, item):
        parent = self._parent
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(self, first, second):
        first_root = self.find(first)
        second_root = self.find(second)
        if first_root != second_root:
            self._parent[second_root] = first_root


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


def _snap_endpoints(endpoints, tolerance):
    """Grid-bucketed union-find snapping of nearby endpoints to shared node ids."""
    count = len(endpoints)
    union_find = _UnionFind(count)
    if count == 0:
        return []

    tolerance = max(tolerance, 1e-9)
    cell_size = tolerance
    buckets = {}
    for endpoint_id, (x, y) in enumerate(endpoints):
        cell = (int(x // cell_size), int(y // cell_size))
        buckets.setdefault(cell, []).append(endpoint_id)

    tolerance_sq = tolerance * tolerance
    for (cx, cy), ids_here in buckets.items():
        neighbour_ids = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbour_ids.extend(buckets.get((cx + dx, cy + dy), []))
        for i in ids_here:
            xi, yi = endpoints[i]
            for j in neighbour_ids:
                if j <= i:
                    continue
                xj, yj = endpoints[j]
                if (xi - xj) ** 2 + (yi - yj) ** 2 <= tolerance_sq:
                    union_find.union(i, j)

    roots = [union_find.find(i) for i in range(count)]
    remap = {}
    node_ids = []
    for root in roots:
        node_id = remap.setdefault(root, len(remap))
        node_ids.append(node_id)
    return node_ids


def build_pipe_graph(layer_specs, tolerance=0.01):
    """Build a :class:`PipeGraph` from one or more layers.

    ``layer_specs`` is a list of dicts, one per pipe layer:
    ``{"layer": QgsVectorLayer, "id_field": str, "us_field": str,
    "ds_field": str, "type_field": str|None, "width_field": str|None,
    "height_field": str|None, "number_field": str|None}``. Every layer uses
    the same (shared/"common") field *names* - that's resolved by the caller
    - but each layer's field *indices* are looked up independently since
    different layers can order their attribute tables differently.

    Endpoint snapping runs across the COMBINED endpoint set from every layer,
    so pipes in different layers connect to each other exactly like pipes
    within one layer do.
    """
    issues = []
    endpoints = []
    endpoint_meta = []  # ("start"/"end", record_index)
    raw_records = []

    for spec in layer_specs:
        layer = spec["layer"]
        fields = layer.fields()
        id_idx = fields.indexOf(spec.get("id_field")) if spec.get("id_field") else -1
        us_idx = fields.indexOf(spec.get("us_field")) if spec.get("us_field") else -1
        ds_idx = fields.indexOf(spec.get("ds_field")) if spec.get("ds_field") else -1
        type_idx = fields.indexOf(spec.get("type_field")) if spec.get("type_field") else -1
        width_idx = fields.indexOf(spec.get("width_field")) if spec.get("width_field") else -1
        height_idx = fields.indexOf(spec.get("height_field")) if spec.get("height_field") else -1
        number_idx = fields.indexOf(spec.get("number_field")) if spec.get("number_field") else -1

        for feature in layer.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                issues.append(f"{layer.name()} fid {feature.id()} has no geometry - skipped.")
                continue

            polyline = geometry.asPolyline() if not geometry.isMultipart() else None
            if not polyline:
                multi = geometry.asMultiPolyline()
                polyline = multi[0] if multi else None
            if not polyline or len(polyline) < 2:
                issues.append(f"{layer.name()} fid {feature.id()} has invalid geometry - skipped.")
                continue

            start_point = polyline[0]
            end_point = polyline[-1]
            length = geometry.length()
            if length <= 0:
                issues.append(f"{layer.name()} fid {feature.id()} has zero length - skipped.")
                continue

            id_value = feature.attribute(id_idx) if id_idx >= 0 else feature.id()
            id_label = str(id_value) if id_value not in (None, "") else str(feature.id())

            record_index = len(raw_records)
            endpoint_meta.append(("start", record_index))
            endpoints.append((start_point.x(), start_point.y()))
            endpoint_meta.append(("end", record_index))
            endpoints.append((end_point.x(), end_point.y()))

            raw_records.append(
                {
                    "fid": feature.id(),
                    "id_label": id_label,
                    "layer": layer,
                    "start_point": start_point,
                    "end_point": end_point,
                    "vertices": list(polyline),
                    "length": length,
                    "us_invert": _to_float(feature.attribute(us_idx)) if us_idx >= 0 else None,
                    "ds_invert": _to_float(feature.attribute(ds_idx)) if ds_idx >= 0 else None,
                    "pipe_type": feature.attribute(type_idx) if type_idx >= 0 else None,
                    "width_or_diameter": _to_float(feature.attribute(width_idx)) if width_idx >= 0 else None,
                    "height": _to_float(feature.attribute(height_idx)) if height_idx >= 0 else None,
                    "number_of": _to_float(feature.attribute(number_idx)) if number_idx >= 0 else None,
                }
            )

    node_of_endpoint = _snap_endpoints(endpoints, tolerance)

    node_points = {}
    for endpoint_id, (kind, record_index) in enumerate(endpoint_meta):
        node_id = node_of_endpoint[endpoint_id]
        if node_id not in node_points:
            x, y = endpoints[endpoint_id]
            node_points[node_id] = type(raw_records[record_index]["start_point"])(x, y)
        if kind == "start":
            raw_records[record_index]["start_node"] = node_id
        else:
            raw_records[record_index]["end_node"] = node_id

    records = [PipeRecord(**raw) for raw in raw_records]
    # fid is only unique WITHIN a layer, so key on (layer id, fid) across layers.
    records_by_key = {(record.layer.id(), record.fid): record for record in records}

    incoming = {}
    outgoing = {}
    for record in records:
        outgoing.setdefault(record.start_node, []).append(record)
        incoming.setdefault(record.end_node, []).append(record)

    return PipeGraph(
        layers=[spec["layer"] for spec in layer_specs],
        records=records,
        records_by_key=records_by_key,
        incoming=incoming,
        outgoing=outgoing,
        node_points=node_points,
        issues=issues,
    )

# -*- coding: utf-8 -*-
"""Samples a DEM raster along a traced pipe path to build the ground line."""


def sample_ground_along_path(dem_layer, pipes, us_stations, step=1.0):
    """Return a list of (station, elevation) points, upstream -> downstream.

    The DEM is sampled at ~``step`` metre spacing along every segment of every
    pipe (not just the endpoints), so the ground line is smooth and follows the
    real alignment. Points that fail to sample (nodata / outside the raster)
    are skipped.
    """
    provider = dem_layer.dataProvider()
    step = max(step, 0.05)
    points = []
    for pipe, us_station in zip(pipes, us_stations):
        _sample_vertices(provider, pipe.vertices, us_station, step, points)
    return points


def _sample_vertices(provider, vertices, start_station, step, points):
    station = start_station
    prev = None
    for vertex in vertices:
        if prev is None:
            _sample_into(provider, vertex, station, points)
            prev = vertex
            continue

        dx = vertex.x() - prev.x()
        dy = vertex.y() - prev.y()
        seg_len = (dx * dx + dy * dy) ** 0.5
        if seg_len > 0:
            n = max(int(seg_len // step), 1)
            point_cls = type(vertex)
            for k in range(1, n + 1):
                frac = k / n
                ix = prev.x() + dx * frac
                iy = prev.y() + dy * frac
                _sample_into(provider, point_cls(ix, iy), station + seg_len * frac, points)
        station += seg_len
        prev = vertex


def _sample_into(provider, point, station, points):
    value, ok = provider.sample(point, 1)
    if ok and value is not None:
        points.append((station, value))

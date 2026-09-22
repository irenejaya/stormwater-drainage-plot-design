# -*- coding: utf-8 -*-
"""Cumulative-length chainage for an ordered list of pipes."""

import math


def build_chainage(pipes, start_station=0.0):
    """Return (us_stations, ds_stations) parallel to ``pipes``.

    Pipe *n*'s US station equals pipe *n-1*'s DS station, so pipes line up
    end-to-end along the X axis. ``start_station`` offsets the whole path so
    several paths can be right-aligned on a shared outlet chainage.
    """
    us_stations = []
    ds_stations = []
    station = start_station
    for pipe in pipes:
        us_stations.append(station)
        station += pipe.length
        ds_stations.append(station)
    return us_stations, ds_stations


def path_length(pipes):
    return sum(pipe.length for pipe in pipes)


def point_at_distance(vertices, target_dist):
    """Interpolate a map-space point ``target_dist`` along ``vertices``.

    Used to turn a hovered chainage back into a real point on the pipe's
    digitised line (for the map hover marker), the reverse of ``build_chainage``.
    """
    if not vertices:
        return None
    if target_dist <= 0:
        return vertices[0]
    travelled = 0.0
    for p0, p1 in zip(vertices, vertices[1:]):
        seg_len = math.hypot(p1.x() - p0.x(), p1.y() - p0.y())
        if travelled + seg_len >= target_dist:
            t = (target_dist - travelled) / seg_len if seg_len > 0 else 0.0
            return type(p0)(p0.x() + (p1.x() - p0.x()) * t, p0.y() + (p1.y() - p0.y()) * t)
        travelled += seg_len
    return vertices[-1]


def elevation_at_chainage(ground_xy, chainage):
    """Linearly interpolate the ground elevation at ``chainage``.

    ``ground_xy`` is the ``[(station, elevation), ...]`` list from
    ``sample_ground_along_path``, ascending by station. Used to work out a
    pit's top level when it should "follow ground" rather than use a fixed
    invert. Returns ``None`` if there's no ground data at all.
    """
    if not ground_xy:
        return None
    if chainage <= ground_xy[0][0]:
        return ground_xy[0][1]
    if chainage >= ground_xy[-1][0]:
        return ground_xy[-1][1]
    for (s0, z0), (s1, z1) in zip(ground_xy, ground_xy[1:]):
        if s0 <= chainage <= s1:
            if s1 == s0:
                return z0
            t = (chainage - s0) / (s1 - s0)
            return z0 + (z1 - z0) * t
    return ground_xy[-1][1]

# -*- coding: utf-8 -*-
"""CSV export of a plotted long-section, for redrawing in Excel or CAD.

Two files are written side by side:

``*_profile.csv``
    Paired X/Y columns (ground, invert, obvert, pits) - select a pair and
    chart it directly as a scatter/line series.
``*_pipes.csv``
    One row per pipe with the band data a long-section drawing needs:
    chainage, lengths, levels, grade, cover and depth to invert.
"""

import csv
import os
from itertools import zip_longest

from .chainage import elevation_at_chainage

PROFILE_HEADERS = [
    "Ground_Chainage", "Ground_Level",
    "Invert_Chainage", "Invert_Level",
    "Obvert_Chainage", "Obvert_Level",
    "Pit_Chainage", "Pit_Level",
]

PIPE_HEADERS = [
    "Seq", "Pipe_ID", "Type", "Width", "Height", "No_of_Pipe",
    "Ch_US", "Ch_DS", "Length",
    "Ground_US", "Ground_DS",
    "US_Invert", "DS_Invert", "US_Obvert", "DS_Obvert",
    "Fall", "Grade_pct", "Grade_1_in",
    "Cover_US", "Cover_DS", "Depth_to_Invert_US", "Depth_to_Invert_DS",
]


def _is_box(pipe_type):
    text = str(pipe_type or "").strip().upper()
    return text.startswith("R") or text.startswith("B")


def _rise(pipe):
    if _is_box(pipe.pipe_type) and pipe.height:
        return pipe.height
    return pipe.width_or_diameter or pipe.height or 0.0


def _num(value, digits=3):
    return "" if value is None else f"{value:.{digits}f}"


def _profile_columns(profile):
    ground = [(x, z) for x, z in profile.ground_xy]

    invert, obvert = [], []
    for pipe, us, ds in zip(profile.pipes, profile.us_stations, profile.ds_stations):
        if pipe.us_invert is None or pipe.ds_invert is None:
            continue
        rise = _rise(pipe)
        invert += [(us, pipe.us_invert), (ds, pipe.ds_invert)]
        obvert += [(us, pipe.us_invert + rise), (ds, pipe.ds_invert + rise)]

    pits = []
    for pit in profile.pits:
        # Blank row between pits so each one charts as its own vertical line.
        pits += [(pit.chainage, pit.bottom), (pit.chainage, pit.top), (None, None)]

    return [ground, invert, obvert, pits]


def write_profile_csv(profile, path):
    columns = _profile_columns(profile)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(PROFILE_HEADERS)
        for group in zip_longest(*columns, fillvalue=(None, None)):
            row = []
            for x, y in group:
                row += [_num(x), _num(y)]
            writer.writerow(row)


def write_pipes_csv(profile, path):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(PIPE_HEADERS)
        for index, (pipe, us, ds) in enumerate(
            zip(profile.pipes, profile.us_stations, profile.ds_stations)
        ):
            rise = _rise(pipe)
            ground_us = elevation_at_chainage(profile.ground_xy, us)
            ground_ds = elevation_at_chainage(profile.ground_xy, ds)
            us_inv, ds_inv = pipe.us_invert, pipe.ds_invert

            fall = None if (us_inv is None or ds_inv is None) else us_inv - ds_inv
            grade_pct = None if (fall is None or pipe.length <= 0) else fall / pipe.length * 100.0
            one_in = pipe.length / fall if (fall and fall > 0) else None

            writer.writerow([
                index + 1,
                pipe.id_label,
                pipe.pipe_type or "",
                _num(pipe.width_or_diameter),
                _num(pipe.height),
                int(pipe.number_of) if pipe.number_of else 1,
                _num(us), _num(ds), _num(pipe.length),
                _num(ground_us), _num(ground_ds),
                _num(us_inv), _num(ds_inv),
                _num(None if us_inv is None else us_inv + rise),
                _num(None if ds_inv is None else ds_inv + rise),
                _num(fall),
                _num(grade_pct, 3),
                _num(one_in, 1),
                _num(_diff(ground_us, us_inv, rise)),
                _num(_diff(ground_ds, ds_inv, rise)),
                _num(_diff(ground_us, us_inv, 0.0)),
                _num(_diff(ground_ds, ds_inv, 0.0)),
            ])


def _diff(ground, invert, rise):
    if ground is None or invert is None:
        return None
    return ground - (invert + rise)


def export_profile(profile, base_path):
    """Write both CSVs next to ``base_path``; returns the two paths."""
    stem = os.path.splitext(base_path)[0]
    for suffix in ("_profile", "_pipes"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    profile_path = f"{stem}_profile.csv"
    pipes_path = f"{stem}_pipes.csv"
    write_profile_csv(profile, profile_path)
    write_pipes_csv(profile, pipes_path)
    return profile_path, pipes_path

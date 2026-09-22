# -*- coding: utf-8 -*-
"""Data handed from the trace/sampling core to the plot builder."""

from dataclasses import dataclass, field


@dataclass
class ProfileData:
    path_label: str
    pipes: list  # PipeRecord, ordered upstream -> downstream
    us_stations: list
    ds_stations: list
    ground_xy: list = field(default_factory=list)  # [(station, elevation), ...]
    pits: list = field(default_factory=list)  # PlottedPit, resolved onto this path

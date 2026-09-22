# -*- coding: utf-8 -*-
"""Upstream path enumeration for a pipe network graph.

From a chosen pipe, walks backwards (upstream) through the network. Every
junction with more than one incoming pipe forks into separate branches, and
every branch that terminates at a headwater (no incoming pipes) becomes one
``Path`` - ordered headwater -> ... -> the chosen pipe.
"""

from collections import deque
from dataclasses import dataclass

MAX_PATHS = 200
MAX_DEPTH = 2000


@dataclass
class Path:
    label: str
    pipes: list  # PipeRecord, ordered upstream -> downstream


def collect_upstream_network(graph, start_record):
    """Return every :class:`PipeRecord` upstream of (and including) ``start_record``.

    Plain BFS over ``graph.incoming`` (no path/branch enumeration, no MAX_PATHS
    cap) - this is the flat set flowtrace selects on the map: the whole
    upstream network in one go, regardless of how many tributaries it has.
    """
    collected = {start_record.fid: start_record}
    visited_nodes = {start_record.start_node}
    queue = deque([start_record.start_node])
    while queue:
        node_id = queue.popleft()
        for pipe in graph.incoming.get(node_id, []):
            if pipe.fid not in collected:
                collected[pipe.fid] = pipe
            if pipe.start_node not in visited_nodes:
                visited_nodes.add(pipe.start_node)
                queue.append(pipe.start_node)
    return list(collected.values())


def enumerate_upstream_paths(graph, start_record):
    """Return a list of :class:`Path` ending at ``start_record``."""
    branches = _walk_upstream(graph, start_record.start_node, frozenset({start_record.start_node}), 0)

    paths = []
    for branch in branches:
        # ``branch`` is already ordered headwater -> ... -> nearest-upstream of
        # the seed, so just append the seed to finish the upstream->downstream run.
        pipes = list(branch) + [start_record]
        paths.append(Path(label=f"Path {len(paths) + 1}", pipes=pipes))
        if len(paths) >= MAX_PATHS:
            break
    return paths


def order_selected_chain(records):
    """Order a user-selected set of pipes into one upstream->downstream chain.

    Returns the ordered list if ``records`` connect end-to-end (each pipe's
    downstream node feeding exactly the next pipe's upstream node, in the
    digitised direction) with no branching or gaps; returns ``None`` otherwise
    so the caller can warn the user.
    """
    if not records:
        return None
    by_start = {}
    by_end = {}
    for record in records:
        # A start/end node used by more than one selected pipe means a
        # junction/branch is selected, not a simple chain.
        if record.start_node in by_start or record.end_node in by_end:
            return None
        by_start[record.start_node] = record
        by_end[record.end_node] = record

    heads = [r for r in records if r.start_node not in by_end]
    if len(heads) != 1:
        return None

    chain = [heads[0]]
    while len(chain) < len(records):
        next_record = by_start.get(chain[-1].end_node)
        if next_record is None:
            return None
        chain.append(next_record)
    return chain


def _walk_upstream(graph, node_id, visited_nodes, depth):
    """Return a list of branches; each branch is a list of PipeRecord ordered
    upstream -> downstream (headwater first, nearest-to-``node_id`` last)."""
    if depth >= MAX_DEPTH:
        return [[]]

    incoming_pipes = graph.incoming.get(node_id, [])
    if not incoming_pipes:
        return [[]]

    branches = []
    for pipe in incoming_pipes:
        if pipe.start_node in visited_nodes:
            # Cycle guard: stop this branch here rather than looping forever.
            branches.append([pipe])
            continue
        sub_branches = _walk_upstream(
            graph, pipe.start_node, visited_nodes | {pipe.start_node}, depth + 1
        )
        for sub in sub_branches:
            branches.append(sub + [pipe])
        if len(branches) >= MAX_PATHS:
            break
    return branches or [[]]

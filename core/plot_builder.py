# -*- coding: utf-8 -*-
"""Builds the Plotly figure for a traced pipe long-section.

Renders three legend series like the reference tool: a blue **Invert** line,
an orange **Ground** line, and a grey filled **Pipes** band (one conduit
polygon per pipe, from invert up to obvert). Returns ``(figure, boxes)`` where
``boxes`` is the per-pipe bounding geometry the web view uses to draw a red
highlight rectangle on hover.
"""

import plotly.graph_objects as go

GROUND_COLOR = "#ff7f0e"   # orange
INVERT_COLOR = "#1f77b4"   # blue
PIPE_FILL = "rgba(150, 150, 150, 0.35)"
PIPE_LINE = "#666666"
PIT_COLOR = "#b22222"      # firebrick - thick dark vertical line
COVER_COLOR = "#9467bd"    # purple - deepest allowable invert (min cover)


def _is_box(pipe_type):
    text = str(pipe_type or "").strip().upper()
    return text.startswith("R") or text.startswith("B")


def _pipe_rise(pipe):
    """Obvert-above-invert rise used to draw the conduit band."""
    if _is_box(pipe.pipe_type) and pipe.height:
        return pipe.height
    if pipe.width_or_diameter:
        return pipe.width_or_diameter
    return pipe.height or 0.0


def _size_html(pipe, rise):
    if _is_box(pipe.pipe_type) and pipe.height and pipe.width_or_diameter:
        return f"Size: {pipe.width_or_diameter:.2f} x {pipe.height:.2f}"
    return f"Dia: {rise:.3f}"


def _pipe_hover_grid(us, ds, us_inv, ds_inv, rise, n_x=8, n_y=4):
    """Invisible marker points spanning the conduit box, used for hover.

    QtWebKit's bundled engine has no ``SVGGeometryElement.isPointInFill``, so
    Plotly's native ``hoveron="fills"`` hit-test throws and silently kills
    hover for the whole chart. Point/marker hit-testing only needs simple
    pixel-distance math, so it works everywhere - sample a grid across the
    box instead of relying on fill hit-testing.
    """
    xs, ys = [], []
    for i in range(n_x):
        t = i / (n_x - 1) if n_x > 1 else 0.0
        x = us + (ds - us) * t
        invert = us_inv + (ds_inv - us_inv) * t
        for j in range(n_y):
            u = j / (n_y - 1) if n_y > 1 else 0.0
            xs.append(x)
            ys.append(invert + rise * u)
    return xs, ys


def build_profile_figure(profile, label_pipes=False, show_crosshair=False, show_legend=True,
                        design=None):
    """Return ``(plotly.graph_objects.Figure, boxes)`` for ``profile``.

    ``design`` (optional) is ``{"cover":, "rise":}`` and adds the dotted
    minimum-cover envelope used while designing an alignment.
    """
    fig = go.Figure()
    boxes = []

    # ---- Pipes band: one filled conduit polygon per pipe -------------------
    for index, pipe in enumerate(profile.pipes):
        us = profile.us_stations[index]
        ds = profile.ds_stations[index]
        us_inv = pipe.us_invert if pipe.us_invert is not None else 0.0
        ds_inv = pipe.ds_invert if pipe.ds_invert is not None else 0.0
        rise = _pipe_rise(pipe)

        poly_x = [us, ds, ds, us, us]
        poly_y = [us_inv, ds_inv, ds_inv + rise, us_inv + rise, us_inv]

        n_of = int(pipe.number_of) if pipe.number_of else 1
        type_txt = pipe.pipe_type if pipe.pipe_type not in (None, "") else (
            "R" if _is_box(pipe.pipe_type) else "C"
        )
        hover = (
            f"ID: {pipe.id_label}<br>"
            f"Type: {type_txt}<br>"
            f"No. of: {n_of}<br>"
            f"{_size_html(pipe, rise)}<br>"
            f"US Invert: {us_inv:.2f}<br>"
            f"DS Invert: {ds_inv:.2f}"
            "<extra></extra>"
        )

        fig.add_trace(
            go.Scatter(
                x=poly_x,
                y=poly_y,
                mode="lines",
                fill="toself",
                fillcolor=PIPE_FILL,
                line=dict(color=PIPE_LINE, width=1),
                name="Pipes",
                legendgroup="pipes",
                showlegend=False,
                hoverinfo="skip",
            )
        )
        # Hover trigger for this pipe: invisible markers, NOT fill hit-testing
        # (see _pipe_hover_grid docstring for why).
        grid_x, grid_y = _pipe_hover_grid(us, ds, us_inv, ds_inv, rise)
        fig.add_trace(
            go.Scatter(
                x=grid_x,
                y=grid_y,
                mode="markers",
                marker=dict(size=14, opacity=0),
                name="Pipes",
                legendgroup="pipes",
                showlegend=False,
                customdata=[index] * len(grid_x),
                hoverlabel=dict(
                    bgcolor="white",
                    bordercolor="#999",
                    font=dict(family="Segoe UI, Arial", size=11, color="#222"),
                    align="left",
                ),
                hovertemplate=hover,
            )
        )
        boxes.append(
            {
                "i": index,
                "x0": us,
                "x1": ds,
                "y0": min(us_inv, ds_inv),
                "y1": max(us_inv, ds_inv) + rise,
            }
        )
        if label_pipes:
            fig.add_annotation(
                x=(us + ds) / 2.0,
                y=max(us_inv, ds_inv) + rise,
                text=pipe.id_label,
                showarrow=False,
                yshift=8,
                font=dict(size=9, color="#333"),
            )

    # ---- Invert line (blue, stepped where junctions drop) ------------------
    inv_x, inv_y = [], []
    for index, pipe in enumerate(profile.pipes):
        us = profile.us_stations[index]
        ds = profile.ds_stations[index]
        us_inv = pipe.us_invert if pipe.us_invert is not None else 0.0
        ds_inv = pipe.ds_invert if pipe.ds_invert is not None else 0.0
        inv_x += [us, ds]
        inv_y += [us_inv, ds_inv]
    if inv_x:
        fig.add_trace(
            go.Scatter(
                x=inv_x,
                y=inv_y,
                mode="lines",
                name="Invert",
                legendgroup="legend",
                legendrank=2,
                line=dict(color=INVERT_COLOR, width=2),
                hovertemplate="Chainage %{x:.1f} m<br>Invert %{y:.2f} m<extra></extra>",
            )
        )

    # ---- Ground line (orange) ---------------------------------------------
    if profile.ground_xy:
        gx = [p[0] for p in profile.ground_xy]
        gy = [p[1] for p in profile.ground_xy]
        fig.add_trace(
            go.Scatter(
                x=gx,
                y=gy,
                mode="lines",
                name="Ground",
                legendgroup="legend",
                legendrank=1,
                line=dict(color=GROUND_COLOR, width=2),
                hovertemplate="Ground %{y:.2f} m<br>Chainage %{x:.1f} m<extra></extra>",
            )
        )

    # ---- Legend proxy for the Pipes band: a filled square swatch so it reads
    #      as a box (like the conduit band), not a line. legendrank pins the
    #      order to Ground -> Invert -> Pipes regardless of trace add order;
    #      shared legendgroup keeps the three rows tight. This is a dummy
    #      x=[None] trace with hoverinfo="skip" - it never participates in
    #      hover, so it's safe to style purely for the legend.
    if profile.pipes:
        fig.add_trace(
            go.Scatter(
                x=[None],
                y=[None],
                mode="markers",
                name="Pipes",
                legendgroup="legend",
                legendrank=3,
                marker=dict(
                    symbol="square",
                    size=14,
                    color=PIPE_FILL,
                    line=dict(color=PIPE_LINE, width=1),
                ),
                hoverinfo="skip",
                showlegend=True,
            )
        )

    # ---- Pits: one thick vertical line per pit, from bottom to top --------
    for pit in profile.pits:
        n_of = int(pit.number_of) if pit.number_of else 1
        hover = (
            f"ID: {pit.id_label}<br>"
            f"Type: {pit.pit_type}<br>"
            f"Inlet: {pit.inlet_type}<br>"
            f"No. of: {n_of}<br>"
            f"Top: {pit.top:.2f}<br>"
            f"Bottom: {pit.bottom:.2f}"
            "<extra></extra>"
        )
        fig.add_trace(
            go.Scatter(
                x=[pit.chainage, pit.chainage],
                y=[pit.bottom, pit.top],
                mode="lines",
                name="Pits",
                legendgroup="pits",
                showlegend=False,
                line=dict(color=PIT_COLOR, width=5),
                hoverlabel=dict(
                    bgcolor="white",
                    bordercolor="#999",
                    font=dict(family="Segoe UI, Arial", size=11, color="#222"),
                    align="left",
                ),
                hovertemplate=hover,
            )
        )

    # ---- Legend proxy for Pits: a short thick vertical-line marker --------
    if profile.pits:
        fig.add_trace(
            go.Scatter(
                x=[None],
                y=[None],
                mode="markers",
                name="Pits",
                legendgroup="legend",
                legendrank=4,
                marker=dict(symbol="line-ns", size=14, line=dict(color=PIT_COLOR, width=4)),
                hoverinfo="skip",
                showlegend=True,
            )
        )

    # ---- Design mode: deepest allowable invert for the chosen cover -------
    if design is not None and profile.ground_xy:
        cover = design.get("cover", 0.0)
        rise = design.get("rise", 0.0)
        fig.add_trace(
            go.Scatter(
                x=[p[0] for p in profile.ground_xy],
                y=[p[1] - cover - rise for p in profile.ground_xy],
                mode="lines",
                name="Min cover",
                legendgroup="legend",
                legendrank=5,
                line=dict(color=COVER_COLOR, width=1, dash="dot"),
                hoverinfo="skip",
            )
        )

    fig.update_layout(
        title=dict(text=f"<b>{profile.path_label}</b>", font=dict(size=15)),
        xaxis=dict(
            title="Chainage (m)",
            gridcolor="#eee",
            zeroline=False,
            # Full-height crosshair line under the cursor (not snapped to the
            # nearest data point), with the X value labelled at the axis -
            # off by default, only when the user turns it on.
            showspikes=show_crosshair,
            spikemode="across+toaxis",
            spikesnap="cursor",
            spikecolor="#e83e3e",
            spikethickness=1,
            spikedash="dash",
        ),
        yaxis=dict(
            title="Elevation (m)",
            gridcolor="#eee",
            zeroline=False,
            showspikes=show_crosshair,
            spikemode="across+toaxis",
            spikesnap="cursor",
            spikecolor="#e83e3e",
            spikethickness=1,
            spikedash="dash",
        ),
        hovermode="closest",
        # Spikes/hover only fire near an actual trace point by default
        # (hoverdistance=20 px) - that's why the crosshair "only worked over
        # Ground/Pipes". -1 means unlimited: always resolve to the closest
        # point so the crosshair (and its axis value labels) track the
        # cursor anywhere in the plot, not just right on top of a line.
        hoverdistance=-1 if show_crosshair else 20,
        # Ground/Invert don't set their own hoverlabel (only pipes/pits do),
        # so they fell back to Plotly's larger default font - this makes
        # every tooltip the same compact size.
        hoverlabel=dict(font=dict(family="Segoe UI, Arial", size=11)),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="Segoe UI, Arial", color="#333"),
        margin=dict(l=60, r=20, t=50, b=45),
        showlegend=show_legend,
        legend=dict(
            x=0.99,
            y=0.99,
            xanchor="right",
            yanchor="top",
            bgcolor="rgba(255,255,255,0.7)",
            bordercolor="#ccc",
            borderwidth=1,
            font=dict(size=10),
            itemwidth=30,
            tracegroupgap=0,
        ),
    )
    return fig, boxes

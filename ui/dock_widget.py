# -*- coding: utf-8 -*-
"""Main dock widget: layer/field setup, pick-a-pipe, path list, profile plot."""

import csv
import os

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsMapLayerProxyModel,
    QgsProject,
    QgsSnappingConfig,
    QgsTolerance,
)
from qgis.gui import QgsDockWidget, QgsHighlight, QgsMapLayerComboBox, QgsVertexMarker
from qgis.PyQt.QtCore import Qt, QSize, QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..constants import MESSAGE_TAG, PLUGIN_NAME
from ..core import design as design_core
from ..core import field_utils, pit_model, profile_export
from ..core.chainage import build_chainage, path_length, point_at_distance
from ..core.ground_sampler import sample_ground_along_path
from ..core.network_graph import build_pipe_graph
from ..core.pit_model import match_pits_to_path
from ..core.profile_model import ProfileData
from ..core.trace import Path, collect_upstream_network, enumerate_upstream_paths, order_selected_chain
from .layer_list import LayerListWidget
from .plot_view import ProfilePlotView


class StormwaterDrainageDockWidget(QgsDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__(PLUGIN_NAME, parent)
        self.setObjectName("StormwaterDrainageDockWidget")
        self._iface = iface
        self._canvas = iface.mapCanvas()

        self._graph = None
        self._paths = []
        self._ch_max = 0.0
        self._upstream_pipes = []
        self._pit_records = []
        self._highlight = None
        self._highlight_key = None
        self._current_profile = None
        self._chainage_marker = None

        # Design mode: pipes are digitised straight into a design layer.
        self._design_layer = None
        self._design_active = False
        self._design_records = []
        self._design_stations = ([], [])
        self._design_ground = []
        self._updating_table = False
        self._updating_design = False
        # Edit signals arrive in bursts (one per feature/attribute) - coalesce
        # them into a single rebuild.
        self._design_refresh_timer = QTimer(self)
        self._design_refresh_timer.setSingleShot(True)
        self._design_refresh_timer.setInterval(150)
        self._design_refresh_timer.timeout.connect(self._refresh_design_from_layer)

        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QWidget(self)
        self.setWidget(root)
        root_layout = QVBoxLayout(root)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_settings_tab(), "Settings")
        self._plot_tab = self._build_plot_tab()
        self._tabs.addTab(self._plot_tab, "Plot")
        self._design_tab = self._build_design_tab()
        self._tabs.addTab(self._design_tab, "Design")
        # Taller tab row gives the corner Run Plot button (and its margins)
        # more room to breathe - the corner widget is squeezed to whatever
        # height the tab bar itself has.
        self._tabs.tabBar().setStyleSheet("QTabBar::tab { height: 28px; }")
        root_layout.addWidget(self._tabs)

        # Corner widget (not inside either tab) so Run Plot is always
        # reachable without switching away from the Plot tab first. Wrapped in
        # a margin container - a corner widget otherwise sits flush against
        # the panel edge with no breathing room.
        self._run_button = QToolButton()
        self._run_button.setIcon(QgsApplication.getThemeIcon("mActionStart.svg"))
        self._run_button.setToolTip("Run Plot")
        self._run_button.setAutoRaise(True)
        self._run_button.setIconSize(QSize(20, 20))
        self._run_button.clicked.connect(self._on_run)
        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(6, 4, 10, 4)
        corner_layout.addStretch(1)
        corner_layout.addWidget(self._icon_button(
            "mActionSharingExport.svg",
            "Export the plotted profile to CSV (ground/invert/obvert series "
            "plus a per-pipe band table)",
            self._on_export_profile,
            fallback_text="Export CSV",
        ))
        # Run Plot is added last so it always stays hard right.
        corner_layout.addWidget(self._run_button)
        self._tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

    def _build_settings_tab(self):
        content = QWidget()
        outer = QVBoxLayout(content)

        columns = QHBoxLayout()

        # ---- Left column: pipe layers + shared field mapping ----
        left_box = QGroupBox("Pipe Input")
        left_layout = QVBoxLayout(left_box)
        self._pipe_layers = LayerListWidget(
            QgsMapLayerProxyModel.Filter.LineLayer, validator=self._validate_pipe_layer
        )
        self._pipe_layers.changed.connect(self._on_pipe_layers_changed)
        left_layout.addWidget(self._pipe_layers)

        left_form = QFormLayout()
        left_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._id_combo = QComboBox()
        left_form.addRow("ID field:", self._id_combo)
        self._us_combo = QComboBox()
        left_form.addRow("US_Invert field:", self._us_combo)
        self._ds_combo = QComboBox()
        left_form.addRow("DS_Invert field:", self._ds_combo)
        self._type_combo = QComboBox()
        left_form.addRow("Type field:", self._type_combo)
        self._width_combo = QComboBox()
        left_form.addRow("Width/Diameter field:", self._width_combo)
        self._height_combo = QComboBox()
        left_form.addRow("Height field:", self._height_combo)
        self._number_combo = QComboBox()
        left_form.addRow("Number_of field:", self._number_combo)
        left_layout.addLayout(left_form)
        left_layout.addStretch(1)

        # ---- Right column: pit layers + shared field mapping ----
        right_box = QGroupBox("Pits Input")
        right_layout = QVBoxLayout(right_box)
        self._pit_layers = LayerListWidget(
            QgsMapLayerProxyModel.Filter.PointLayer, validator=self._validate_pit_layer
        )
        self._pit_layers.changed.connect(self._on_pit_layers_changed)
        right_layout.addWidget(self._pit_layers)

        right_form = QFormLayout()
        right_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._pit_id_combo = QComboBox()
        right_form.addRow("ID field:", self._pit_id_combo)
        self._pit_type_combo = QComboBox()
        right_form.addRow("Type field (Q/node):", self._pit_type_combo)
        self._pit_us_combo = QComboBox()
        right_form.addRow("US_Invert field:", self._pit_us_combo)
        self._pit_ds_combo = QComboBox()
        right_form.addRow("DS_Invert field:", self._pit_ds_combo)
        self._pit_inlet_combo = QComboBox()
        right_form.addRow("Inlet_Type field:", self._pit_inlet_combo)
        self._pit_number_combo = QComboBox()
        right_form.addRow("Number_of field:", self._pit_number_combo)
        self._pit_conn_combo = QComboBox()
        right_form.addRow("Conn_1D_2D field:", self._pit_conn_combo)
        right_layout.addLayout(right_form)
        right_layout.addStretch(1)

        # Make every field widget fill its column instead of collapsing to its
        # icon/content width.
        for combo in (
            self._id_combo, self._us_combo, self._ds_combo, self._type_combo,
            self._width_combo, self._height_combo, self._number_combo,
            self._pit_id_combo, self._pit_type_combo, self._pit_us_combo,
            self._pit_ds_combo, self._pit_inlet_combo, self._pit_number_combo,
            self._pit_conn_combo,
        ):
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.setMinimumWidth(140)

        # Equal-width columns that grow/shrink together with the dock.
        columns.addWidget(left_box, 1)
        columns.addWidget(right_box, 1)
        outer.addLayout(columns)

        # ---- Elevation input + Run settings (below both columns) ----
        dem_box = QGroupBox("Elevation input")
        dem_layout = QVBoxLayout(dem_box)
        dem_form = QFormLayout()
        dem_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._dem_combo = QgsMapLayerComboBox()
        self._dem_combo.setFilters(QgsMapLayerProxyModel.Filter.RasterLayer)
        self._dem_combo.setAllowEmptyLayer(True)
        self._dem_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._dem_combo.setMinimumWidth(140)
        dem_form.addRow("Ground (DEM):", self._dem_combo)
        dem_layout.addLayout(dem_form)
        outer.addWidget(dem_box)

        run_box = QGroupBox("Run settings")
        run_layout = QVBoxLayout(run_box)
        run_form = QFormLayout()
        run_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Trace upstream from selected pipe")
        self._mode_combo.addItem("Plot selected pipes only")
        self._mode_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        run_form.addRow("Mode:", self._mode_combo)
        run_layout.addLayout(run_form)

        hint = QLabel(
            "<b>Trace upstream</b> mode: select the downstream-most pipe on "
            "the map. <b>Selected pipes only</b> mode: select every pipe you "
            "want plotted (they must form one connected, unbroken line). "
            "Then click <b>Run Plot</b> (top-right of the panel)."
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)
        run_layout.addWidget(hint)
        outer.addWidget(run_box)
        outer.addStretch(1)

        # Scrollable so the dock doesn't need to grow taller than before to
        # fit pipe fields + pits + elevation + run settings all at once.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    def _build_plot_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._plot_view = ProfilePlotView()
        self._plot_view.pipeClicked.connect(self._on_plot_pipe_clicked)
        self._plot_view.pipeHovered.connect(self._on_plot_pipe_hovered)
        self._plot_view.pipeUnhovered.connect(self._on_plot_pipe_unhovered)
        self._plot_view.groundHovered.connect(self._on_plot_ground_hovered)
        splitter.addWidget(self._plot_view)

        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(4, 0, 0, 0)
        self._highlight_check = QCheckBox("Highlight only selected path")
        self._highlight_check.setChecked(True)
        self._highlight_check.toggled.connect(self._on_highlight_toggled)
        side_layout.addWidget(self._highlight_check)
        self._label_network_check = QCheckBox("Label network")
        self._label_network_check.toggled.connect(self._on_label_network_toggled)
        side_layout.addWidget(self._label_network_check)
        self._crosshair_check = QCheckBox("Show crosshair")
        self._crosshair_check.setChecked(False)
        self._crosshair_check.toggled.connect(self._on_crosshair_toggled)
        side_layout.addWidget(self._crosshair_check)
        self._legend_check = QCheckBox("Show legend")
        self._legend_check.setChecked(True)
        self._legend_check.toggled.connect(self._on_legend_toggled)
        side_layout.addWidget(self._legend_check)
        side_layout.addWidget(QLabel("Paths"))
        self._path_list = QListWidget()
        self._path_list.currentRowChanged.connect(self._on_path_selected)
        side_layout.addWidget(self._path_list)
        splitter.addWidget(side_panel)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        return tab

    def _build_design_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        controls = QGroupBox("Design layer")
        controls_layout = QVBoxLayout(controls)

        layer_row = QHBoxLayout()
        start_button = QPushButton("Start pipe layer")
        start_button.setToolTip(
            "Create an empty line layer carrying this tool's pipe schema and "
            "begin digitising into it."
        )
        start_button.clicked.connect(self._on_start_design_layer)
        layer_row.addWidget(start_button)
        layer_row.addWidget(QLabel("Design layer:"))
        self._design_layer_combo = QgsMapLayerComboBox()
        self._design_layer_combo.setFilters(QgsMapLayerProxyModel.Filter.LineLayer)
        self._design_layer_combo.setAllowEmptyLayer(True)
        self._design_layer_combo.setLayer(None)
        self._design_layer_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._design_layer_combo.setMinimumWidth(160)
        self._design_layer_combo.layerChanged.connect(self._on_design_layer_selected)
        layer_row.addWidget(self._design_layer_combo, 1)
        self._draw_button = self._icon_button(
            "mActionToggleEditing.svg",
            "Draw pipes - put the design layer into edit mode with snapping on "
            "and start QGIS's Add Line tool. Every line you draw becomes one pipe.",
            self._on_design_draw,
            fallback_text="Draw pipes",
        )
        layer_row.addWidget(self._draw_button)
        layer_row.addWidget(self._icon_button(
            "mActionSaveEdits.svg", "Save edits", self._on_design_save_edits,
            fallback_text="Save edits",
        ))
        controls_layout.addLayout(layer_row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Cover (m):"))
        self._cover_spin = QDoubleSpinBox()
        self._cover_spin.setRange(0.0, 20.0)
        self._cover_spin.setSingleStep(0.05)
        self._cover_spin.setDecimals(2)
        self._cover_spin.setValue(0.60)
        self._cover_spin.valueChanged.connect(self._on_design_options_changed)
        row.addWidget(self._cover_spin)
        row.addWidget(QLabel("Diameter (mm):"))
        self._dia_spin = QDoubleSpinBox()
        self._dia_spin.setRange(0.0, 5000.0)
        self._dia_spin.setSingleStep(75.0)
        self._dia_spin.setDecimals(0)
        self._dia_spin.setValue(375.0)
        self._dia_spin.valueChanged.connect(self._on_design_options_changed)
        row.addWidget(self._dia_spin)
        row.addWidget(QLabel("Slope (%):"))
        self._slope_spin = QDoubleSpinBox()
        self._slope_spin.setRange(0.0, 50.0)
        self._slope_spin.setSingleStep(0.1)
        self._slope_spin.setDecimals(2)
        self._slope_spin.setValue(1.00)
        self._slope_spin.valueChanged.connect(self._on_design_options_changed)
        row.addWidget(self._slope_spin)
        row.addStretch(1)
        controls_layout.addLayout(row)

        hint = QLabel(
            "1. Pick a <b>Ground (DEM)</b> on the Settings tab. "
            "2. <b>Start pipe layer</b> (or choose an existing design layer), "
            "then <b>Draw pipes</b> - digitise with QGIS's normal Add Line "
            "tool, snapping on, one line per pipe. Each finished pipe is "
            "graded from the settings above and plotted straight away. "
            "3. Edit any white cell below, or edit the layer in QGIS, then hit "
            "<b>Recalculate inverts</b> to re-grade the whole chain."
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)
        controls_layout.addWidget(hint)
        layout.addWidget(controls)

        self._design_table = QTableWidget(0, len(design_core.COLUMN_HEADERS))
        self._design_table.setHorizontalHeaderLabels(design_core.COLUMN_HEADERS)
        self._design_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._design_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._design_table.itemChanged.connect(self._on_design_table_edited)
        self._design_table.itemSelectionChanged.connect(self._on_design_row_selected)
        layout.addWidget(self._design_table)

        buttons = QHBoxLayout()
        buttons.addWidget(self._icon_button(
            "mActionDeleteSelectedFeatures.svg", "Delete selected pipe",
            self._on_design_delete_selected, fallback_text="Delete selected pipe",
        ))
        buttons.addWidget(self._icon_button(
            "mActionCalculateField.svg",
            "Recalculate inverts - re-derive every US/DS invert from the current "
            "alignment geometry, ground cover, diameter and slope.",
            self._on_design_recalculate, fallback_text="Recalculate inverts",
        ))
        show_button = QPushButton("Show design plot")
        show_button.clicked.connect(lambda: self._reload_design_plot(switch_tab=True))
        buttons.addWidget(show_button)
        buttons.addStretch(1)
        csv_button = QPushButton("Export CSV...")
        csv_button.clicked.connect(self._on_design_export_csv)
        buttons.addWidget(csv_button)
        layout.addLayout(buttons)
        return tab

    # --------------------------------------------------------------- state
    @staticmethod
    def _icon_button(icon_name, tooltip, slot, fallback_text=""):
        """Flat QGIS-themed tool button, falling back to text if the icon is
        missing from the active theme."""
        button = QToolButton()
        icon = QgsApplication.getThemeIcon(icon_name)
        if icon.isNull():
            button.setText(fallback_text or tooltip)
        else:
            button.setIcon(icon)
            button.setIconSize(QSize(20, 20))
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        button.clicked.connect(slot)
        return button

    def _validate_pipe_layer(self, layer):
        existing = self._pipe_layers.layers()
        if not existing:
            return True, ""
        chosen = [
            c.currentText()
            for c in (
                self._id_combo, self._us_combo, self._ds_combo, self._type_combo,
                self._width_combo, self._height_combo, self._number_combo,
            )
            if c.currentText()
        ]
        layer_fields_lower = {f.name().lower() for f in layer.fields()}
        missing = [name for name in chosen if name.lower() not in layer_fields_lower]
        if missing:
            return False, (
                f"'{layer.name()}' is missing field(s) already mapped for the "
                f"pipe layers: {', '.join(missing)}."
            )
        return True, ""

    def _validate_pit_layer(self, layer):
        existing = self._pit_layers.layers()
        if not existing:
            return True, ""
        chosen = [
            c.currentText()
            for c in (
                self._pit_id_combo, self._pit_type_combo, self._pit_us_combo,
                self._pit_ds_combo, self._pit_inlet_combo, self._pit_number_combo,
                self._pit_conn_combo,
            )
            if c.currentText()
        ]
        layer_fields_lower = {f.name().lower() for f in layer.fields()}
        missing = [name for name in chosen if name.lower() not in layer_fields_lower]
        if missing:
            return False, (
                f"'{layer.name()}' is missing field(s) already mapped for the "
                f"pit layers: {', '.join(missing)}."
            )
        return True, ""

    def _on_pipe_layers_changed(self):
        layers = self._pipe_layers.layers()
        field_utils.populate_common_field_combo(self._id_combo, layers, field_utils.ID_FIELD_CANDIDATES)
        field_utils.populate_common_field_combo(self._us_combo, layers, field_utils.US_INVERT_CANDIDATES)
        field_utils.populate_common_field_combo(self._ds_combo, layers, field_utils.DS_INVERT_CANDIDATES)
        field_utils.populate_common_field_combo(self._type_combo, layers, field_utils.TYPE_FIELD_CANDIDATES)
        field_utils.populate_common_field_combo(self._width_combo, layers, field_utils.WIDTH_FIELD_CANDIDATES)
        field_utils.populate_common_field_combo(self._height_combo, layers, field_utils.HEIGHT_FIELD_CANDIDATES)
        field_utils.populate_common_field_combo(self._number_combo, layers, field_utils.NUMBER_FIELD_CANDIDATES)
        self._graph = None

    def _on_pit_layers_changed(self):
        layers = self._pit_layers.layers()
        field_utils.populate_common_field_combo(self._pit_id_combo, layers, field_utils.PIT_ID_FIELD_CANDIDATES)
        field_utils.populate_common_field_combo(self._pit_type_combo, layers, field_utils.PIT_TYPE_FIELD_CANDIDATES)
        field_utils.populate_common_field_combo(self._pit_us_combo, layers, field_utils.US_INVERT_CANDIDATES)
        field_utils.populate_common_field_combo(self._pit_ds_combo, layers, field_utils.DS_INVERT_CANDIDATES)
        field_utils.populate_common_field_combo(self._pit_inlet_combo, layers, field_utils.PIT_INLET_FIELD_CANDIDATES)
        field_utils.populate_common_field_combo(self._pit_number_combo, layers, field_utils.NUMBER_FIELD_CANDIDATES)
        field_utils.populate_common_field_combo(self._pit_conn_combo, layers, field_utils.CONN_1D_2D_CANDIDATES)
        self._pit_records = []

    def _on_run(self):
        pipe_layers = self._pipe_layers.layers()
        if not pipe_layers:
            QMessageBox.warning(self, MESSAGE_TAG, "Add at least one pipe layer first.")
            return

        selected_only = self._mode_combo.currentIndex() == 1
        selected_ids_by_layer = {layer: layer.selectedFeatureIds() for layer in pipe_layers}
        if not any(selected_ids_by_layer.values()):
            QMessageBox.warning(
                self,
                MESSAGE_TAG,
                "Select one or more connected pipes on the map first, then "
                "click Run Plot." if selected_only else
                "Select the downstream pipe on the map first, then click Run Plot.",
            )
            return

        layer_specs = [
            {
                "layer": layer,
                "id_field": self._id_combo.currentText() or None,
                "us_field": self._us_combo.currentText() or None,
                "ds_field": self._ds_combo.currentText() or None,
                "type_field": self._type_combo.currentText() or None,
                "width_field": self._width_combo.currentText() or None,
                "height_field": self._height_combo.currentText() or None,
                "number_field": self._number_combo.currentText() or None,
            }
            for layer in pipe_layers
        ]
        try:
            self._graph = build_pipe_graph(layer_specs)
        except Exception as exc:  # noqa: BLE001 - surface any build error to the user
            QMessageBox.critical(self, MESSAGE_TAG, f"Failed to build pipe network: {exc}")
            return

        seeds = []
        for layer, ids in selected_ids_by_layer.items():
            for fid in ids:
                record = self._graph.records_by_key.get((layer.id(), fid))
                if record is not None:
                    seeds.append(record)
        if not seeds:
            QMessageBox.warning(
                self, MESSAGE_TAG, "Selected feature(s) are not part of the mapped pipe network."
            )
            return

        if selected_only:
            chain = order_selected_chain(seeds)
            if chain is None:
                QMessageBox.warning(
                    self,
                    MESSAGE_TAG,
                    "Selected pipes are not one continuous, connected line "
                    "(in the digitised flow direction). Select an unbroken "
                    "chain of pipes with no gaps or branches.",
                )
                return
            self._paths = [Path(label="Selected pipes", pipes=chain)]
            self._upstream_pipes = chain
        else:
            # Enumerate plottable long-section paths from the first selected pipe.
            self._paths = enumerate_upstream_paths(self._graph, seeds[0])
            # Whole upstream network (used when the highlight toggle is off).
            collected = {}
            for seed in seeds:
                for pipe in collect_upstream_network(self._graph, seed):
                    collected[(pipe.layer.id(), pipe.fid)] = pipe
            self._upstream_pipes = list(collected.values())

        # Pit records (Q-type only) from every configured pit layer.
        pit_layers = self._pit_layers.layers()
        if pit_layers:
            pit_specs = [
                {
                    "layer": layer,
                    "id_field": self._pit_id_combo.currentText() or None,
                    "type_field": self._pit_type_combo.currentText() or None,
                    "us_field": self._pit_us_combo.currentText() or None,
                    "ds_field": self._pit_ds_combo.currentText() or None,
                    "inlet_field": self._pit_inlet_combo.currentText() or None,
                    "number_field": self._pit_number_combo.currentText() or None,
                    "conn_field": self._pit_conn_combo.currentText() or None,
                }
                for layer in pit_layers
            ]
            self._pit_records, _pit_issues = pit_model.build_pit_records(pit_specs)
        else:
            self._pit_records = []

        # Right-align every path on a shared outlet chainage, so left -> right
        # always reads upstream -> downstream and the outlet stays put when
        # switching paths (matches the TUFLOW long-plot behaviour).
        self._ch_max = max((path_length(p.pipes) for p in self._paths), default=0.0)
        self._path_list.blockSignals(True)
        self._path_list.clear()
        for path in self._paths:
            self._path_list.addItem(f"{path.label} ({len(path.pipes)} pipes)")
        self._path_list.blockSignals(False)
        if self._paths:
            self._path_list.setCurrentRow(0)
            self._tabs.setCurrentWidget(self._plot_tab)
        message = (
            f"Plotting {len(chain)} selected pipe(s)." if selected_only else
            f"{len(self._paths)} upstream path(s) found from the selected pipe."
        )
        self._iface.messageBar().pushMessage(
            MESSAGE_TAG,
            message,
            level=Qgis.MessageLevel.Success,
            duration=6,
        )

    def _on_path_selected(self, row):
        if row < 0 or row >= len(self._paths):
            return
        # Plotting a traced path takes over the view from design mode.
        self._design_active = False
        path = self._paths[row]
        profile = self._build_profile(path)
        self._current_profile = profile
        self._plot_view.load_profile(
            profile,
            label_pipes=self._label_network_check.isChecked(),
            show_crosshair=self._crosshair_check.isChecked(),
            show_legend=self._legend_check.isChecked(),
        )
        self._apply_highlight()

    def _on_label_network_toggled(self, _checked):
        self._reload_plot()

    def _on_crosshair_toggled(self, _checked):
        self._reload_plot()

    def _on_legend_toggled(self, _checked):
        self._reload_plot()

    def _on_highlight_toggled(self, _checked):
        self._apply_highlight()

    def _apply_highlight(self):
        """Select only the current path when the toggle is on, else the whole
        traced upstream network."""
        row = self._path_list.currentRow()
        if self._highlight_check.isChecked() and 0 <= row < len(self._paths):
            self._select_pipes_on_map(self._paths[row].pipes)
        elif self._upstream_pipes:
            self._select_pipes_on_map(self._upstream_pipes)

    def _build_profile(self, path):
        offset = max(self._ch_max - path_length(path.pipes), 0.0)
        us_stations, ds_stations = build_chainage(path.pipes, start_station=offset)
        dem_layer = self._dem_combo.currentLayer()
        ground_xy = []
        if dem_layer:
            # Sample at the DEM cell size (like TUFLOW's DrapeData) - finer than a
            # cell just stair-steps and looks jagged.
            cell = max(dem_layer.rasterUnitsPerPixelX(), dem_layer.rasterUnitsPerPixelY())
            ground_xy = sample_ground_along_path(dem_layer, path.pipes, us_stations, step=cell)
        pits = []
        if self._pit_records:
            pits, _pit_issues = match_pits_to_path(
                path.pipes, us_stations, ds_stations, self._pit_records, ground_xy
            )
        return ProfileData(
            path_label=path.label,
            pipes=path.pipes,
            us_stations=us_stations,
            ds_stations=ds_stations,
            ground_xy=ground_xy,
            pits=pits,
        )

    # ---------------------------------------------------------- design mode
    def _design_rise(self):
        return self._dia_spin.value() / 1000.0

    def _reload_plot(self):
        """Redraw whichever mode is currently showing (design or traced path)."""
        if self._design_active:
            self._reload_design_plot()
            return
        row = self._path_list.currentRow()
        if 0 <= row < len(self._paths):
            self._on_path_selected(row)

    def _on_start_design_layer(self):
        existing = [
            layer.name() for layer in QgsProject.instance().mapLayers().values()
        ]
        name = design_core.LAYER_NAME
        suffix = 2
        while name in existing:
            name = f"{design_core.LAYER_NAME} {suffix}"
            suffix += 1
        layer = design_core.create_design_layer(
            QgsProject.instance().crs().authid(),
            self._design_rise(),
            self._slope_spin.value(),
            name=name,
        )
        QgsProject.instance().addMapLayer(layer)
        self._design_layer_combo.setLayer(layer)
        self._on_design_draw()

    def _on_design_layer_selected(self, layer):
        if layer is None:
            self._set_design_layer(None)
            return
        missing = design_core.missing_fields(layer)
        if missing:
            QMessageBox.warning(
                self, MESSAGE_TAG,
                f"'{layer.name()}' is missing the design field(s): "
                f"{', '.join(missing)}.\n\n"
                "Use 'Start pipe layer' to create a layer with the right schema.",
            )
            self._design_layer_combo.setLayer(None)
            return
        self._set_design_layer(layer)
        self._design_active = True
        self._reload_design_plot()

    def _set_design_layer(self, layer):
        if self._design_layer is not None:
            for signal, slot in self._design_signal_slots(self._design_layer):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self._design_layer = layer
        self._design_records = []
        self._design_stations = ([], [])
        self._design_ground = []
        if layer is None:
            self._design_active = False
            self._updating_table = True
            self._design_table.setRowCount(0)
            self._updating_table = False
            return
        # Any edit made in QGIS itself keeps the table and plot in step.
        for signal, slot in self._design_signal_slots(layer):
            signal.connect(slot)

    def _design_signal_slots(self, layer):
        return (
            (layer.featureAdded, self._schedule_design_refresh),
            (layer.featuresDeleted, self._schedule_design_refresh),
            (layer.geometryChanged, self._schedule_design_refresh),
            (layer.attributeValueChanged, self._schedule_design_refresh),
            (layer.editCommandEnded, self._schedule_design_refresh),
            (layer.afterCommitChanges, self._schedule_design_refresh),
            (layer.afterRollBack, self._schedule_design_refresh),
        )

    def _schedule_design_refresh(self, *_args):
        """Coalesce edit signals - one refresh per burst, not per feature."""
        if self._updating_design or not self._design_active:
            return
        self._design_refresh_timer.start()

    def _refresh_design_from_layer(self):
        if not self._design_layer_alive():
            return
        self._grade_new_segments()
        self._reload_design_plot()

    def _design_layer_alive(self):
        layer = self._design_layer
        if layer is None:
            return False
        try:
            return QgsProject.instance().mapLayer(layer.id()) is not None
        except RuntimeError:  # layer already deleted underneath us
            self._design_layer = None
            return False

    def _grade_new_segments(self):
        """Give freshly digitised pipes inverts without touching existing ones."""
        if not self._design_layer_alive() or self._dem_combo.currentLayer() is None:
            return
        records, us_stations, _ds, ground_xy = self._design_profile_parts()
        if not records:
            return
        self._updating_design = True
        try:
            design_core.recalculate_inverts(
                self._design_layer, records, us_stations, ground_xy,
                self._cover_spin.value(), self._slope_spin.value(), only_missing=True,
            )
        finally:
            self._updating_design = False

    def _on_design_draw(self):
        if not self._design_layer_alive():
            QMessageBox.information(
                self, MESSAGE_TAG,
                "Start a pipe layer (or pick one) before drawing.",
            )
            return
        if self._dem_combo.currentLayer() is None:
            QMessageBox.warning(
                self, MESSAGE_TAG,
                "Choose a Ground (DEM) raster on the Settings tab first - the "
                "design inverts are derived from it.",
            )
            return
        layer = self._design_layer
        design_core.configure_for_digitising(
            layer, self._design_rise(), self._slope_spin.value()
        )
        self._enable_snapping(layer)
        self._iface.setActiveLayer(layer)
        if not layer.isEditable():
            layer.startEditing()
        self._design_active = True
        self._iface.actionAddFeature().trigger()
        self._iface.messageBar().pushMessage(
            MESSAGE_TAG,
            "Draw one line per pipe. Snapping is on, so start each pipe on the "
            "end of the last one - every finished line is graded and plotted.",
            level=Qgis.MessageLevel.Info,
            duration=8,
        )

    def _enable_snapping(self, layer, tolerance_px=12.0):
        project = QgsProject.instance()
        config = project.snappingConfig()
        config.setEnabled(True)
        enum_attr = design_core.enum_attr
        mode = enum_attr(QgsSnappingConfig, "SnappingMode.AllLayers", "AllLayers") or enum_attr(
            Qgis, "SnappingMode.AllLayers"
        )
        if mode is not None:
            config.setMode(mode)
        snap_type = enum_attr(
            QgsSnappingConfig, "SnappingTypes.VertexFlag", "VertexFlag", "Vertex"
        ) or enum_attr(Qgis, "SnappingType.Vertex")
        if snap_type is not None:
            setter = getattr(config, "setTypeFlag", None) or getattr(config, "setType", None)
            if setter is not None:
                setter(snap_type)
        config.setTolerance(tolerance_px)
        units = enum_attr(QgsTolerance, "UnitType.Pixels", "Pixels")
        if units is not None:
            config.setUnits(units)
        project.setSnappingConfig(config)

    def _on_design_save_edits(self):
        if not self._design_layer_alive():
            return
        layer = self._design_layer
        if layer.isEditable():
            layer.commitChanges()
        self._reload_design_plot()

    def _on_design_row_selected(self):
        """Mirror the table selection onto the map."""
        if self._updating_table or not self._design_layer_alive():
            return
        rows = {item.row() for item in self._design_table.selectedItems()}
        fids = [
            self._design_records[row].fid
            for row in rows
            if 0 <= row < len(self._design_records)
        ]
        self._updating_design = True
        try:
            self._design_layer.selectByIds(fids)
        finally:
            self._updating_design = False

    def _on_design_delete_selected(self):
        if not self._design_layer_alive():
            return
        rows = sorted({item.row() for item in self._design_table.selectedItems()})
        fids = [
            self._design_records[row].fid
            for row in rows
            if 0 <= row < len(self._design_records)
        ]
        if not fids:
            QMessageBox.information(self, MESSAGE_TAG, "Select a row first.")
            return
        layer = self._design_layer
        was_editing = layer.isEditable()
        if not was_editing:
            layer.startEditing()
        layer.deleteFeatures(fids)
        if not was_editing:
            layer.commitChanges()
        self._reload_design_plot()

    def _set_design_layer(self, layer):
        if self._design_layer is not None:
            for signal, slot in self._design_signal_slots(self._design_layer):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self._design_layer = layer
        self._design_records = []
        self._design_stations = ([], [])
        self._design_ground = []
        if layer is None:
            self._design_active = False
            self._updating_table = True
            self._design_table.setRowCount(0)
            self._updating_table = False
            return
        # Any edit made in QGIS itself keeps the table and plot in step.
        for signal, slot in self._design_signal_slots(layer):
            signal.connect(slot)

    def _design_profile_parts(self):
        """(records, us_stations, ds_stations, ground_xy) for the design layer."""
        layer = self._design_layer
        records = design_core.records_from_layer(layer)
        us_stations, ds_stations = build_chainage(records)
        ground_xy = []
        dem_layer = self._dem_combo.currentLayer()
        if dem_layer is not None and records:
            cell = max(dem_layer.rasterUnitsPerPixelX(), dem_layer.rasterUnitsPerPixelY())
            ground_xy = sample_ground_along_path(dem_layer, records, us_stations, step=cell)
        return records, us_stations, ds_stations, ground_xy

    def _reload_design_plot(self, switch_tab=False):
        if not self._design_layer_alive():
            return
        records, us_stations, ds_stations, ground_xy = self._design_profile_parts()
        self._updating_design = True
        try:
            design_core.write_derived(
                self._design_layer, records, us_stations, ds_stations, ground_xy
            )
        finally:
            self._updating_design = False
        self._design_records = records
        self._design_stations = (us_stations, ds_stations)
        self._design_ground = ground_xy
        self._design_active = True

        profile = ProfileData(
            path_label="Design alignment",
            pipes=records,
            us_stations=us_stations,
            ds_stations=ds_stations,
            ground_xy=ground_xy,
            pits=[],
        )
        self._current_profile = profile
        self._plot_view.load_profile(
            profile,
            label_pipes=self._label_network_check.isChecked(),
            show_crosshair=self._crosshair_check.isChecked(),
            show_legend=self._legend_check.isChecked(),
            design={"cover": self._cover_spin.value(), "rise": self._design_rise()},
        )
        self._refresh_design_table()
        if switch_tab:
            self._tabs.setCurrentWidget(self._plot_tab)

    def _on_design_options_changed(self, *_args):
        # The page redraws the min-cover line itself, so no reload is needed.
        self._plot_view.design_set_options(self._cover_spin.value(), self._design_rise())

    def _on_design_recalculate(self, switch_tab=False):
        if not self._design_layer_alive():
            QMessageBox.information(
                self, MESSAGE_TAG, "Draw an alignment on the map first."
            )
            return
        if self._dem_combo.currentLayer() is None:
            QMessageBox.warning(
                self, MESSAGE_TAG,
                "Choose a Ground (DEM) raster on the Settings tab - inverts "
                "are derived from ground level.",
            )
            return
        records, us_stations, _ds_stations, ground_xy = self._design_profile_parts()
        if not records:
            return
        self._updating_design = True
        try:
            design_core.recalculate_inverts(
                self._design_layer, records, us_stations, ground_xy,
                self._cover_spin.value(), self._slope_spin.value(),
            )
        finally:
            self._updating_design = False
        self._reload_design_plot(switch_tab=switch_tab)

    def _refresh_design_table(self):
        if not self._design_layer_alive():
            self._design_table.setRowCount(0)
            return
        us_stations, ds_stations = self._design_stations
        rows = design_core.table_rows(
            self._design_layer, self._design_records, us_stations, ds_stations,
            self._design_ground,
        )
        self._updating_table = True
        self._design_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                item = QTableWidgetItem(text)
                if c not in design_core.EDITABLE_COLUMNS:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setBackground(QColor(245, 245, 245))
                self._design_table.setItem(r, c, item)
        self._updating_table = False

    def _on_design_table_edited(self, item):
        if self._updating_table or not self._design_layer_alive():
            return
        field = design_core.EDITABLE_COLUMNS.get(item.column())
        if field is None or item.row() >= len(self._design_records):
            return
        record = self._design_records[item.row()]
        text = item.text().strip()

        if field in design_core.TEXT_FIELDS:
            value = text
        else:
            try:
                value = float(text)
            except ValueError:
                self._refresh_design_table()
                return
            if field in design_core.INT_FIELDS:
                value = int(value)

        layer = self._design_layer
        index = layer.fields().indexOf(field)
        if index < 0:
            return
        self._updating_design = True
        try:
            design_core.apply_attribute_changes(layer, {record.fid: {index: value}})
        finally:
            self._updating_design = False
        self._reload_design_plot()

    def _on_design_export_csv(self):
        if not self._design_layer_alive() or not self._design_records:
            QMessageBox.information(self, MESSAGE_TAG, "Nothing designed yet.")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export design", "pipe_design.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        us_stations, ds_stations = self._design_stations
        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(design_core.COLUMN_HEADERS)
                writer.writerows(design_core.table_rows(
                    self._design_layer, self._design_records, us_stations,
                    ds_stations, self._design_ground,
                ))
        except OSError as exc:
            QMessageBox.critical(self, MESSAGE_TAG, f"Could not write the CSV: {exc}")
            return
        self._iface.messageBar().pushMessage(
            MESSAGE_TAG, f"Design exported to {path}",
            level=Qgis.MessageLevel.Success, duration=6,
        )

    # ---------------------------------------------------------- map linking
    def _on_export_profile(self):
        """Export whatever is currently plotted - traced path or design."""
        profile = self._current_profile
        if profile is None or not profile.pipes:
            QMessageBox.information(
                self, MESSAGE_TAG, "Plot a profile first, then export it."
            )
            return
        safe_label = "".join(
            char if char.isalnum() or char in "-_" else "_"
            for char in profile.path_label
        ) or "profile"
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export profile", f"{safe_label}.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            profile_path, pipes_path = profile_export.export_profile(profile, path)
        except OSError as exc:
            QMessageBox.critical(self, MESSAGE_TAG, f"Could not write the CSV: {exc}")
            return
        self._iface.messageBar().pushMessage(
            MESSAGE_TAG,
            f"Exported {os.path.basename(profile_path)} and "
            f"{os.path.basename(pipes_path)}.",
            level=Qgis.MessageLevel.Success,
            duration=8,
        )

    def _select_pipes_on_map(self, pipes):
        # Clear every configured pipe layer first - the new selection might
        # not touch all of them (e.g. switching to a path that only uses one
        # of several layers).
        for layer in self._pipe_layers.layers():
            layer.removeSelection()
        by_layer = {}
        for pipe in pipes:
            by_layer.setdefault(pipe.layer, []).append(pipe.fid)
        for layer, fids in by_layer.items():
            layer.selectByIds(fids)


    def _on_plot_pipe_clicked(self, index):
        pipe = self._pipe_at(index)
        if pipe is not None:
            self._highlight_pipe(pipe, zoom=True)

    def _on_plot_pipe_hovered(self, index):
        pipe = self._pipe_at(index)
        if pipe is not None:
            self._highlight_pipe(pipe, zoom=False)

    def _on_plot_pipe_unhovered(self):
        if self._highlight is not None:
            self._highlight.hide()
            self._highlight = None
        self._highlight_key = None
        if self._chainage_marker is not None:
            self._chainage_marker.hide()

    def _on_plot_ground_hovered(self, chainage):
        point = self._point_at_chainage(chainage)
        if point is None:
            return
        if self._chainage_marker is None:
            self._chainage_marker = QgsVertexMarker(self._canvas)
            self._chainage_marker.setIconType(QgsVertexMarker.IconType.ICON_CIRCLE)
            self._chainage_marker.setIconSize(10)
            self._chainage_marker.setPenWidth(2)
            self._chainage_marker.setColor(QColor(255, 0, 0))
            self._chainage_marker.setFillColor(QColor(255, 0, 0))
        self._chainage_marker.setCenter(point)
        self._chainage_marker.show()
        self._canvas.refresh()

    def _point_at_chainage(self, chainage):
        """Map a hovered Ground-line chainage back to a point on the pipe line."""
        profile = self._current_profile
        if profile is None:
            return None
        for pipe, us, ds in zip(profile.pipes, profile.us_stations, profile.ds_stations):
            if us <= chainage <= ds:
                span = ds - us
                fraction = (chainage - us) / span if span > 0 else 0.0
                return point_at_distance(pipe.vertices, fraction * pipe.length)
        return None

    def _pipe_at(self, index):
        if self._design_active:
            pipes = self._design_records
        else:
            row = self._path_list.currentRow()
            if row < 0 or row >= len(self._paths):
                return None
            pipes = self._paths[row].pipes
        if index < 0 or index >= len(pipes):
            return None
        return pipes[index]

    def _highlight_pipe(self, pipe, zoom=False):
        layer = pipe.layer
        # Hover fires repeatedly - skip re-highlighting the same feature.
        # fid alone isn't globally unique across layers, so key on both.
        key = (layer.id(), pipe.fid)
        if key == self._highlight_key and not zoom:
            return
        feature = layer.getFeature(pipe.fid)
        if not feature.isValid():
            return
        geometry = feature.geometry()

        if self._highlight is not None:
            self._highlight.hide()
        highlight = QgsHighlight(self._canvas, geometry, layer)
        highlight.setColor(QColor(255, 0, 0))
        highlight.setWidth(4)
        highlight.setFillColor(QColor(255, 0, 0, 80))
        highlight.show()
        self._highlight = highlight
        self._highlight_key = key

        if zoom:
            extent = geometry.boundingBox()
            extent.scale(1.8)
            self._canvas.setExtent(extent)
        self._canvas.refresh()

    # ------------------------------------------------------------- cleanup
    def closeEvent(self, event):
        if self._highlight is not None:
            self._highlight.hide()
        if self._chainage_marker is not None:
            self._chainage_marker.hide()
        self._plot_view.cleanup_temp_files()
        super().closeEvent(event)

# -*- coding: utf-8 -*-
"""A small "+"/"-" growable table of map layers, used for the Pipe fields and
Pits panels so either can combine features from several layers.

Layers are stored by id (not object reference) and re-resolved from
QgsProject on every ``layers()`` call, so a layer removed from the project
later just silently drops out of the table instead of raising.
"""

from qgis.core import QgsProject
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..constants import MESSAGE_TAG

try:
    from qgis.core import QgsApplication
except ImportError:  # pragma: no cover - always available in QGIS
    QgsApplication = None


class LayerListWidget(QWidget):
    """Pick a layer in the combo, click "+" to add it to the table below.

    ``validator(layer) -> (bool, reason)`` is called before a layer is
    actually added (both via the "+" button and ``add_layer``); if it
    returns False the layer is rejected and ``reason`` is shown as a warning.
    """

    changed = pyqtSignal()

    def __init__(self, layer_filter, validator=None, parent=None):
        super().__init__(parent)
        self._validator = validator
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        self._combo = QgsMapLayerComboBox()
        self._combo.setFilters(layer_filter)
        self._combo.setShowCrs(True)
        toolbar.addWidget(self._combo, 1)

        self._add_btn = QToolButton()
        self._add_btn.setToolTip("Add layer")
        self._add_btn.setAutoRaise(True)
        self._remove_btn = QToolButton()
        self._remove_btn.setToolTip("Remove selected layer")
        self._remove_btn.setAutoRaise(True)
        if QgsApplication is not None:
            self._add_btn.setIcon(QgsApplication.getThemeIcon("symbologyAdd.svg"))
            self._remove_btn.setIcon(QgsApplication.getThemeIcon("symbologyRemove.svg"))
        self._add_btn.clicked.connect(self._on_add)
        self._remove_btn.clicked.connect(self._on_remove)
        toolbar.addWidget(self._add_btn)
        toolbar.addWidget(self._remove_btn)
        layout.addLayout(toolbar)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Layer", "Features", "EPSG"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setMaximumHeight(110)
        layout.addWidget(self._table)

    # -------------------------------------------------------------- public
    def layers(self):
        """Return the currently-valid layers in table order."""
        project = QgsProject.instance()
        result = []
        for row in range(self._table.rowCount()):
            layer_id = self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            layer = project.mapLayer(layer_id)
            if layer is not None:
                result.append(layer)
        return result

    def clear(self):
        self._table.setRowCount(0)
        self.changed.emit()

    def add_layer(self, layer):
        """Add ``layer`` programmatically (e.g. auto-seeding the active layer),
        subject to the same validator/duplicate checks as the "+" button."""
        if layer is None:
            return
        for row in range(self._table.rowCount()):
            if self._table.item(row, 0).data(Qt.ItemDataRole.UserRole) == layer.id():
                return  # already added
        if self._validator is not None:
            ok, reason = self._validator(layer)
            if not ok:
                QMessageBox.warning(self, MESSAGE_TAG, reason)
                return

        row = self._table.rowCount()
        self._table.insertRow(row)
        name_item = QTableWidgetItem(layer.name())
        name_item.setData(Qt.ItemDataRole.UserRole, layer.id())
        self._table.setItem(row, 0, name_item)
        try:
            feature_count = layer.featureCount()
        except Exception:  # noqa: BLE001 - just show 0 if the provider can't answer
            feature_count = 0
        self._table.setItem(row, 1, QTableWidgetItem(str(feature_count)))
        try:
            epsg = layer.crs().authid()
        except Exception:  # noqa: BLE001 - blank if the layer has no valid CRS
            epsg = ""
        self._table.setItem(row, 2, QTableWidgetItem(epsg))
        self.changed.emit()

    # ------------------------------------------------------------- events
    def _on_add(self):
        self.add_layer(self._combo.currentLayer())

    def _on_remove(self):
        row = self._table.currentRow()
        if row < 0:
            return
        self._table.removeRow(row)
        self.changed.emit()

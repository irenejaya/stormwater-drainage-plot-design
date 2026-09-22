# -*- coding: utf-8 -*-
"""Stormwater Drainage Plot and Design: dockable long-section plotter/designer."""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .constants import PLUGIN_NAME
from .ui.dock_widget import StormwaterDrainageDockWidget


class StormwaterDrainagePlugin:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        self._iface = iface
        self._plugin_dir = os.path.dirname(__file__)
        self._action = None
        self._dock = None
        self._toolbar = None

    def initGui(self):
        icon = QIcon(os.path.join(self._plugin_dir, "icon.svg"))
        self._action = QAction(icon, PLUGIN_NAME, self._iface.mainWindow())
        self._action.setCheckable(True)
        self._action.toggled.connect(self._on_toggled)

        # Its own toolbar (not the shared Plugins toolbar) so it appears as a
        # separate, distinctly grouped section.
        self._toolbar = self._iface.addToolBar(PLUGIN_NAME)
        self._toolbar.setObjectName("StormwaterDrainageToolbar")
        self._toolbar.addAction(self._action)

        self._iface.addPluginToMenu(f"&{PLUGIN_NAME}", self._action)

    def unload(self):
        self._iface.removePluginMenu(f"&{PLUGIN_NAME}", self._action)
        if self._toolbar is not None:
            self._iface.mainWindow().removeToolBar(self._toolbar)
            self._toolbar.deleteLater()
            self._toolbar = None
        if self._dock is not None:
            self._dock.close()  # triggers closeEvent's temp-file cleanup
            self._iface.removeDockWidget(self._dock)
            self._dock.deleteLater()
            self._dock = None

    def _on_toggled(self, checked):
        if checked:
            if self._dock is None:
                self._dock = StormwaterDrainageDockWidget(self._iface)
                self._dock.visibilityChanged.connect(self._on_dock_visibility_changed)
                self._iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._dock)
                # The dock otherwise defaults to a large chunk of the window -
                # shrink its initial height (the user can still drag it bigger).
                self._iface.mainWindow().resizeDocks(
                    [self._dock], [320], Qt.Orientation.Vertical
                )
            self._dock.show()
        elif self._dock is not None:
            self._dock.hide()

    def _on_dock_visibility_changed(self, visible):
        if self._action is not None and self._action.isChecked() != visible:
            self._action.blockSignals(True)
            self._action.setChecked(visible)
            self._action.blockSignals(False)

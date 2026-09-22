# -*- coding: utf-8 -*-
def classFactory(iface):
    from .pipe_plotter import StormwaterDrainagePlugin

    return StormwaterDrainagePlugin(iface)

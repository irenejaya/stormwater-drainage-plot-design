# -*- coding: utf-8 -*-
"""Small helpers for populating field-mapping combo boxes.

Mirrors the pattern already used in Adv_Pipe_PostProcess/ui/field_utils.py so
the two plugins behave the same way when guessing field names.
"""


def populate_field_combo(combo, layer, preferred_names=(), allow_none=True):
    """Fill ``combo`` with the field names of ``layer``.

    Pre-selects the first field whose name matches (case-insensitively) one
    of ``preferred_names``, checked in order.
    """
    combo.blockSignals(True)
    combo.clear()
    if allow_none:
        combo.addItem("")
    if layer is None:
        combo.blockSignals(False)
        return

    field_names = [f.name() for f in layer.fields()]
    for name in field_names:
        combo.addItem(name)

    lower_lookup = {name.lower(): name for name in field_names}
    for preferred in preferred_names:
        match = lower_lookup.get(preferred.lower())
        if match is not None:
            index = combo.findText(match)
            if index >= 0:
                combo.setCurrentIndex(index)
                break
    combo.blockSignals(False)


# Common TUFLOW 1d_nwk field name candidates, most-preferred first.
ID_FIELD_CANDIDATES = ("ID", "Pipe_ID", "Name")
TYPE_FIELD_CANDIDATES = ("Type",)
US_INVERT_CANDIDATES = ("US_Invert", "USInvert", "US_INVERT", "Invert_US")
DS_INVERT_CANDIDATES = ("DS_Invert", "DSInvert", "DS_INVERT", "Invert_DS")
WIDTH_FIELD_CANDIDATES = ("Width_or_D", "Width", "Diameter", "D")
HEIGHT_FIELD_CANDIDATES = ("Height_or_", "Height")
NUMBER_FIELD_CANDIDATES = ("Number_of", "Number_o", "No_of", "NumberOf", "N_of", "Num")

# Pit (1d_pit-style) field name candidates.
PIT_ID_FIELD_CANDIDATES = ID_FIELD_CANDIDATES
PIT_TYPE_FIELD_CANDIDATES = TYPE_FIELD_CANDIDATES
PIT_INLET_FIELD_CANDIDATES = ("Inlet_Type", "InletType", "Inlet_Typ", "Inlet")
CONN_1D_2D_CANDIDATES = ("Conn_1D_2D", "Conn_1D_2", "Connection")


def common_field_names(layers):
    """Return field names present (case-insensitively) on EVERY layer in
    ``layers``, preserving the first layer's original casing/order."""
    if not layers:
        return []
    common_lower = None
    for layer in layers:
        names_lower = {f.name().lower() for f in layer.fields()}
        common_lower = names_lower if common_lower is None else (common_lower & names_lower)
    if not common_lower:
        return []
    first_names = [f.name() for f in layers[0].fields()]
    return [name for name in first_names if name.lower() in common_lower]


def populate_common_field_combo(combo, layers, preferred_names=(), allow_none=True):
    """Like ``populate_field_combo``, but the options are the fields common
    to every layer in ``layers`` (case-insensitive name match)."""
    combo.blockSignals(True)
    combo.clear()
    if allow_none:
        combo.addItem("")
    names = common_field_names(layers)
    for name in names:
        combo.addItem(name)

    lower_lookup = {name.lower(): name for name in names}
    for preferred in preferred_names:
        match = lower_lookup.get(preferred.lower())
        if match is not None:
            index = combo.findText(match)
            if index >= 0:
                combo.setCurrentIndex(index)
                break
    combo.blockSignals(False)

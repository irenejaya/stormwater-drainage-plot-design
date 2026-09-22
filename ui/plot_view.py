# -*- coding: utf-8 -*-
"""Plotly profile plot embedded in a Qt web view.

Web-view detection mirrors this workspace's TUFLOW_Sentinel plugin exactly:
prefer QtWebKit's ``QWebView`` (the stable path inside QGIS) and only fall
back to ``QWebEngineView``. The chart is written to an on-disk HTML file with
a locally-cached plotly.js and loaded via ``QUrl.fromLocalFile`` - which is far
more robust inside QGIS than matplotlib's Qt backend.

Clicking a pipe in the chart is relayed back to Python through a small bridge
object, wired differently per backend (WebKit: addToJavaScriptWindowObject;
WebEngine: QWebChannel). Bridge failures never block rendering.
"""

import glob
import hashlib
import html as html_escape
import json
import os
import tempfile
import traceback

from qgis.PyQt.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from qgis.PyQt.QtWidgets import QLabel, QVBoxLayout, QWidget

try:
    import plotly.io as pio
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

# Prefer QtWebKit (Qt5, stable in QGIS); fall back to QtWebEngine (Qt6).
HAS_WEBKIT = False
HAS_WEBENGINE = False
QWebView = None
QWebEngineView = None
try:
    from qgis.PyQt.QtWebKitWidgets import QWebView
    HAS_WEBKIT = True
except ImportError:
    try:
        from qgis.PyQt.QtWebEngineWidgets import QWebEngineView
        HAS_WEBENGINE = True
    except ImportError:
        QWebEngineView = None
HAS_WEB = HAS_WEBKIT or HAS_WEBENGINE

# QWebChannel is only needed for the WebEngine backend's click bridge.
QWebChannel = None
if HAS_WEBENGINE:
    try:
        from qgis.PyQt.QtWebChannel import QWebChannel
    except ImportError:
        QWebChannel = None

from ..constants import MESSAGE_TAG, TEMP_DIR_NAME
from ..core.plot_builder import build_profile_figure

PLOTLY_JS_VERSION = "2.30.0"
PLOTLY_JS_URL = f"https://cdn.plot.ly/plotly-{PLOTLY_JS_VERSION}.min.js"


def _ensure_plotly_assets():
    """Ensure plotly.js is cached locally; return (out_dir, js_filename).

    Downloaded through QGIS's own network manager so the user's proxy and
    SSL settings are honoured.
    """
    out_dir = os.path.join(tempfile.gettempdir(), TEMP_DIR_NAME)
    os.makedirs(out_dir, exist_ok=True)
    js_name = f"plotly-{PLOTLY_JS_VERSION}.min.js"
    js_path = os.path.join(out_dir, js_name)
    if not os.path.exists(js_path) or os.path.getsize(js_path) < 100_000:
        _download(PLOTLY_JS_URL, js_path)
    return out_dir, js_name


def _download(url, destination):
    from qgis.core import QgsNetworkAccessManager
    from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

    reply = QgsNetworkAccessManager.instance().blockingGet(QNetworkRequest(QUrl(url)))
    if reply.error() != QNetworkReply.NetworkError.NoError:
        raise RuntimeError(f"Could not download {url}: {reply.errorString()}")
    with open(destination, "wb") as handle:
        handle.write(bytes(reply.content()))


class _PlotBridge(QObject):
    """Exposed to the page's JS as ``bridge``; relays pipe clicks/hovers to Python."""

    pipeClicked = pyqtSignal(int)
    pipeHovered = pyqtSignal(int)
    pipeUnhovered = pyqtSignal()
    groundHovered = pyqtSignal(float)
    jsError = pyqtSignal(str)

    @pyqtSlot(int)
    def notifyHover(self, pipe_index):
        self.pipeHovered.emit(pipe_index)

    @pyqtSlot()
    def notifyUnhover(self):
        self.pipeUnhovered.emit()

    @pyqtSlot(int)
    def notifyClick(self, pipe_index):
        self.pipeClicked.emit(pipe_index)

    @pyqtSlot(float)
    def notifyGroundHover(self, chainage):
        self.groundHovered.emit(chainage)

    @pyqtSlot(str)
    def notifyError(self, message):
        self.jsError.emit(message)


class ProfilePlotView(QWidget):
    """Embeds the Plotly profile chart; emits ``pipeClicked(index)``."""

    pipeClicked = pyqtSignal(int)
    pipeHovered = pyqtSignal(int)
    pipeUnhovered = pyqtSignal()
    groundHovered = pyqtSignal(float)
    jsError = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._view = None
        self._bridge = None

        if not HAS_PLOTLY or not HAS_WEB:
            missing = []
            if not HAS_PLOTLY:
                missing.append("plotly")
            if not HAS_WEB:
                missing.append("QtWebKit / QtWebEngine")
            message = QLabel(
                "Plot unavailable. Missing: " + ", ".join(missing) + ".\n"
                "Install with: pip install plotly"
            )
            message.setWordWrap(True)
            layout.addWidget(message)
            return

        self._bridge = _PlotBridge()
        self._bridge.pipeClicked.connect(self.pipeClicked.emit)
        self._bridge.pipeHovered.connect(self.pipeHovered.emit)
        self._bridge.pipeUnhovered.connect(self.pipeUnhovered.emit)
        self._bridge.groundHovered.connect(self.groundHovered.emit)
        self._bridge.jsError.connect(self.jsError.emit)
        self._bridge.jsError.connect(self._show_js_error)

        if HAS_WEBKIT:
            self._view = QWebView()
            self._setup_webkit_bridge()
        else:
            self._view = QWebEngineView()
            self._setup_webengine_bridge()
        layout.addWidget(self._view)

    # ------------------------------------------------------------- bridges
    def _setup_webkit_bridge(self):
        frame = self._view.page().mainFrame()
        frame.addToJavaScriptWindowObject("bridge", self._bridge)
        # The window object is wiped on every navigation - re-register it.
        frame.javaScriptWindowObjectCleared.connect(
            lambda: self._view.page().mainFrame().addToJavaScriptWindowObject("bridge", self._bridge)
        )
        # Let right-click > Inspect open WebKit's inspector, so JS errors that
        # break hover/click can actually be seen (WebKit has no devtools by default).
        try:
            from qgis.PyQt.QtWebKit import QWebSettings
            QWebSettings.globalSettings().setAttribute(
                QWebSettings.DeveloperExtrasEnabled, True
            )
        except (ImportError, AttributeError):  # purely a debugging nicety
            return

    def _setup_webengine_bridge(self):
        if QWebChannel is None:
            return
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("bridge", self._bridge)
        self._view.page().setWebChannel(self._channel)

    # -------------------------------------------------------------- public
    def is_available(self):
        return self._view is not None

    def load_profile(self, profile, label_pipes=False, show_crosshair=False, show_legend=True,
                     design=None):
        """Build and display the Plotly figure for ``profile`` (ProfileData)."""
        if self._view is None:
            return
        try:
            fig, boxes = build_profile_figure(
                profile, label_pipes=label_pipes, show_crosshair=show_crosshair,
                show_legend=show_legend, design=design,
            )
            html_path = self._write_html(fig, boxes, profile, show_crosshair, design)
        except Exception as exc:  # noqa: BLE001 - show the real cause, not a blank view
            self._show_error(exc)
            return
        self._view.load(QUrl.fromLocalFile(html_path))

    def design_set_options(self, cover, rise):
        self._run_js(
            f"if(window.ppDesignOptions) ppDesignOptions({float(cover)},{float(rise)});"
        )

    def _show_error(self, exc):
        details = html_escape.escape(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        )
        self._view.setHtml(
            "<html><body style='font-family:Segoe UI,Arial;color:#900;'>"
            "<h3>Plot failed to render</h3>"
            f"<pre style='white-space:pre-wrap'>{details}</pre>"
            "</body></html>"
        )

    def clear(self):
        if self._view is not None:
            self._view.setHtml("")

    def cleanup_temp_files(self):
        """Delete every cached profile_*.html - called when the dock closes.

        The cached plotly.js is left alone; it's meant to persist and be
        reused next time, not re-downloaded every session.
        """
        out_dir = os.path.join(tempfile.gettempdir(), TEMP_DIR_NAME)
        for path in glob.glob(os.path.join(out_dir, "profile_*.html")):
            try:
                os.remove(path)
            except OSError:
                continue

    def _show_js_error(self, message):
        # Surfaced from window.onerror inside the page (see _bridge_js) -
        # WebKit has no devtools console by default, so this is otherwise
        # a silent failure that would just look like "hover doesn't work".
        try:
            from qgis.core import Qgis, QgsMessageLog
            QgsMessageLog.logMessage(message, MESSAGE_TAG, Qgis.MessageLevel.Warning)
        except ImportError:  # logging is best-effort only
            return

    def _run_js(self, js):
        if self._view is None:
            return
        page = self._view.page()
        if HAS_WEBKIT:
            page.mainFrame().evaluateJavaScript(js)
        else:
            page.runJavaScript(js)

    def resizeEvent(self, event):
        # Keep Plotly's internal size (and thus hover hit-testing) in sync with
        # the panel as it resizes.
        super().resizeEvent(event)
        self._resize_plot()

    def showEvent(self, event):
        # Switching to the Plot tab can reveal the view at its final size
        # without ever firing resizeEvent - refit Plotly whenever we're shown.
        super().showEvent(event)
        self._resize_plot()

    def _resize_plot(self):
        self._run_js(
            "(function(){var gd=document.querySelector('.js-plotly-plot');"
            "if(gd&&window.Plotly){try{Plotly.Plots.resize(gd);}catch(e){}}})();"
        )

    def _bridge_js(self, boxes, show_crosshair=False, design=None):
        """Return the JS that wires plotly_click -> the Python bridge and draws a
        red highlight rectangle around the hovered pipe.

        WebEngine needs qwebchannel.js to obtain the bridge object; WebKit's
        addToJavaScriptWindowObject exposes ``window.bridge`` directly.
        """
        boxes_json = json.dumps(boxes)
        wire = (
            "  var gd = document.querySelector('.js-plotly-plot');\n"
            "  if (!gd) return;\n"
            # Recompute plot size once the embedded view actually has dimensions.
            # Without this, Plotly.newPlot runs against a 0-size container and its
            # hover hit-testing coordinate system is broken (no tooltips fire).
            "  function fitPlot() { if (window.Plotly && gd) { try { Plotly.Plots.resize(gd); } catch (e) {} } }\n"
            "  fitPlot();\n"
            "  setTimeout(fitPlot, 150);\n"
            "  setTimeout(fitPlot, 500);\n"
            "  window.addEventListener('resize', fitPlot);\n"
            f"  var BOXES = {boxes_json};\n"
            "  function boxAt(x) {\n"
            "    for (var k = 0; k < BOXES.length; k++) {\n"
            "      var b = BOXES[k];\n"
            "      if (x >= Math.min(b.x0, b.x1) && x <= Math.max(b.x0, b.x1)) return b;\n"
            "    }\n"
            "    return null;\n"
            "  }\n"
            "  function pipeIndex(pt) {\n"
            "    if (pt.customdata !== undefined && pt.customdata !== null) return pt.customdata;\n"
            "    var b = boxAt(pt.x); return b ? b.i : null;\n"
            "  }\n"
            # Hover -> tell Python to highlight the pipe on the MAP. We do NOT
            # call Plotly.relayout here (that would wipe the native tooltip).
            # Ground-line hover is reported separately (by chainage, not pipe
            # index) so the map can show a point marker along the path.
            "  gd.on('plotly_hover', function (data) {\n"
            "    if (!data.points || !data.points.length) return;\n"
            "    var pt = data.points[0];\n"
            "    if (pt.data && pt.data.name === 'Ground') {\n"
            "      if (window.bridge && window.bridge.notifyGroundHover) window.bridge.notifyGroundHover(pt.x);\n"
            "      return;\n"
            "    }\n"
            "    var idx = pipeIndex(pt);\n"
            "    if (idx === null || idx === undefined) return;\n"
            "    if (window.bridge) window.bridge.notifyHover(idx);\n"
            "  });\n"
            "  gd.on('plotly_unhover', function () {\n"
            "    if (window.bridge) window.bridge.notifyUnhover();\n"
            "  });\n"
            "  gd.on('plotly_click', function (data) {\n"
            "    if (!data.points || !data.points.length) return;\n"
            "    var idx = pipeIndex(data.points[0]);\n"
            "    if (idx === null || idx === undefined) return;\n"
            "    if (window.bridge) window.bridge.notifyClick(idx);\n"
            "  });\n"
        )
        if show_crosshair:
            # Piggybacking on plotly_hover was unreliable - it only fires
            # when Plotly's OWN hit-testing resolves a "closest point", so
            # the readout dropped out intermittently. Instead, track every
            # raw mousemove and convert pixel -> data coordinates ourselves
            # via the axes' own p2d() (pixel-to-data) converters - this is
            # independent of hoverdistance/spikesnap entirely, so it's
            # accurate and continuous anywhere over the plot area (needed
            # for this to double as a real coordinate-readout design tool).
            wire += (
                "  var __ppReadout = document.createElement('div');\n"
                # Sizing lives in the page stylesheet under this id - inline
                # styles lose to the "body > div { width/height:100% !important }"
                # Plotly-container rule, which is what used to blow this box up
                # to the full width/height of the plot.
                "  __ppReadout.id = 'pp-readout';\n"
                "  __ppReadout.style.cssText = 'pointer-events:none;"
                "display:none;background:rgba(255,255,255,0.85);border:1px solid #e83e3e;"
                "color:#333;font:9px Segoe UI,Arial;"
                "z-index:99998;white-space:nowrap;';\n"
                "  document.body.appendChild(__ppReadout);\n"
                "  function __ppHideReadout() { __ppReadout.style.display = 'none'; }\n"
                "  gd.addEventListener('mousemove', function (evt) {\n"
                "   try {\n"
                "    var fl = gd._fullLayout;\n"
                "    if (!fl || !fl.xaxis || !fl.yaxis || !fl._size) { __ppHideReadout(); return; }\n"
                "    var rect = gd.getBoundingClientRect();\n"
                "    var size = fl._size;\n"
                "    var xPix = evt.clientX - rect.left - size.l;\n"
                "    var yPix = evt.clientY - rect.top - size.t;\n"
                "    if (xPix < 0 || xPix > size.w || yPix < 0 || yPix > size.h) { __ppHideReadout(); return; }\n"
                "    var xData = fl.xaxis.p2d(xPix);\n"
                "    var yData = fl.yaxis.p2d(yPix);\n"
                "    __ppReadout.textContent = 'X: ' + Number(xData).toFixed(2) + '  Y: ' + Number(yData).toFixed(2);\n"
                "    __ppReadout.style.display = 'block';\n"
                "    __ppReadout.style.left = (evt.clientX + 10) + 'px';\n"
                "    __ppReadout.style.top = (evt.clientY + 10) + 'px';\n"
                "   } catch (e) { __ppHideReadout(); }\n"
                "  });\n"
                "  gd.addEventListener('mouseleave', __ppHideReadout);\n"
            )
        if design is not None:
            wire += self._design_js(design)
        if HAS_WEBENGINE and QWebChannel is not None:
            return (
                "\n<script src=\"qrc:///qtwebchannel/qwebchannel.js\"></script>\n"
                "<script>\n"
                "document.addEventListener('DOMContentLoaded', function () {\n"
                "  new QWebChannel(qt.webChannelTransport, function (channel) {\n"
                "    window.bridge = channel.objects.bridge;\n"
                f"{wire}"
                "  });\n"
                "});\n"
                "</script>"
            )
        return (
            "\n<script>\n"
            "document.addEventListener('DOMContentLoaded', function () {\n"
            f"{wire}"
            "});\n"
            "</script>"
        )

    @staticmethod
    def _design_js(design):
        """Keep the minimum-cover line in step with the design settings.

        Redrawing goes through ``Plotly.restyle`` on the existing "Min cover"
        trace rather than rewriting the HTML, so changing cover or diameter
        updates the chart instantly with no reload.
        """
        ground_json = json.dumps(design.get("ground") or [])
        cover = float(design.get("cover", 0.0))
        rise = float(design.get("rise", 0.0))
        return (
            f"  var __ppGround = {ground_json};\n"
            f"  var __ppOpt = {{cover: {cover}, rise: {rise}}};\n"
            "  window.ppDesignOptions = function (cover, rise) {\n"
            "    __ppOpt.cover = cover; __ppOpt.rise = rise;\n"
            "    __ppCoverDraw();\n"
            "  };\n"
            "  function __ppCoverDraw() {\n"
            "    var i = -1;\n"
            "    for (var t = 0; t < gd.data.length; t++) { if (gd.data[t].name === 'Min cover') { i = t; break; } }\n"
            "    if (i < 0) return;\n"
            "    var ys = [];\n"
            "    for (var k = 0; k < __ppGround.length; k++) { ys.push(__ppGround[k][1] - __ppOpt.cover - __ppOpt.rise); }\n"
            "    try { Plotly.restyle(gd, {y: [ys]}, [i]); } catch (e) {}\n"
            "  }\n"
        )

    @staticmethod
    def _error_banner_js():
        """Catch otherwise-silent JS failures and surface them on the page.

        QtWebKit has no devtools console, so a broken Plotly load (e.g. an
        unsupported JS feature) used to just look like "hover doesn't work"
        with zero clue why. This shows the real error text as a red banner
        and forwards it to Python (QgsMessageLog) when the bridge is ready.
        """
        return (
            "\n<script>\n"
            "(function () {\n"
            "  function report(msg) {\n"
            "    var b = document.getElementById('__ppErrorBanner');\n"
            "    if (!b) {\n"
            "      b = document.createElement('div');\n"
            "      b.id = '__ppErrorBanner';\n"
            "      b.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#c0392b;"
            "color:#fff;font:12px Segoe UI,Arial;padding:4px 8px;z-index:99999;"
            "white-space:pre-wrap;';\n"
            "      document.body.appendChild(b);\n"
            "    }\n"
            "    b.textContent = 'Stormwater Drainage JS error: ' + msg;\n"
            "    if (window.bridge && window.bridge.notifyError) { try { window.bridge.notifyError(msg); } catch (e) {} }\n"
            "  }\n"
            "  window.addEventListener('error', function (e) {\n"
            "    report(e.message + ' (' + e.filename + ':' + e.lineno + ')');\n"
            "  });\n"
            "  window.addEventListener('unhandledrejection', function (e) {\n"
            "    report('' + (e.reason && e.reason.message ? e.reason.message : e.reason));\n"
            "  });\n"
            "})();\n"
            "</script>\n"
        )

    def _write_html(self, fig, boxes, profile, show_crosshair=False, design=None):
        out_dir, js_name = _ensure_plotly_assets()
        fingerprint = hashlib.sha256(
            (profile.path_label + repr([p.fid for p in profile.pipes])
             + repr(profile.ground_xy[:1]) + ("design" if design else "")).encode()
        ).hexdigest()[:10]
        if design is not None:
            # The page snaps inverts itself, so it needs the same sampled
            # ground the Python side uses.
            design = dict(design, ground=[[float(s), float(z)] for s, z in profile.ground_xy])
        plot_div = pio.to_html(
            fig,
            include_plotlyjs=False,
            full_html=False,
            config={"responsive": True, "displaylogo": False, "scrollZoom": True},
        )
        html = (
            "<!DOCTYPE html>\n<html>\n<head>\n"
            '  <meta charset="utf-8">\n'
            f"{self._error_banner_js()}"
            f'  <script src="{js_name}"></script>\n'
            # Chain height:100% (NOT 100vh - QtWebKit miscomputes vh in an
            # embedded view, which breaks Plotly's mouse hit-testing/hover).
            "  <style>html, body { margin:0; padding:0; height:100%; width:100%; }\n"
            # :not(#pp-readout) keeps the crosshair readout out of this rule -
            # it is also a direct child of <body> and was being forced to the
            # full plot width/height by the !important flags.
            "  body > div:not(#pp-readout), .plotly-graph-div { height:100% !important; width:100% !important; }\n"
            # Force a normal arrow cursor over the plot (no zoom crosshair).
            "  .js-plotly-plot .nsewdrag,\n"
            "  .js-plotly-plot .cursor-crosshair,\n"
            "  .js-plotly-plot .drag { cursor: default !important; }\n"
            # Crosshair mode: show only our plain X/Y readout, not the
            # per-trace tooltip boxes (pipe/ground/pit info). Target
            # ".hovertext" specifically, NOT the whole ".hoverlayer" - the
            # spike/crosshair lines (".spikeline") live in that SAME layer,
            # so hiding the whole layer was also hiding the crosshair itself.
            + ("  .hoverlayer .hovertext { display: none !important; }\n" if show_crosshair else "")
            # Explicit px box: this engine does not size auto-width fixed
            # overlays reliably, and id specificity + !important also beats
            # any stray container rule.
            + ("  #pp-readout { position:fixed !important; box-sizing:border-box !important;"
               " width:104px !important; height:14px !important;"
               " min-width:0 !important; min-height:0 !important;"
               " max-width:104px !important; max-height:14px !important;"
               " padding:0 3px !important; margin:0 !important;"
               " line-height:12px !important; overflow:hidden !important; }\n"
               if show_crosshair else "")
            + "  </style>\n"
            "</head>\n<body>\n"
            f"{plot_div}\n"
            f"{self._bridge_js(boxes, show_crosshair, design)}\n"
            "</body>\n</html>"
        )
        # A distinct filename per profile: some web views won't re-render when
        # asked to (re)load a URL they already navigated to.
        html_path = os.path.join(out_dir, f"profile_{fingerprint}.html")
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(html)
        # Every profile switch/path click writes a new hashed file that was
        # never cleaned up otherwise - only keep the one we just wrote.
        for old_path in glob.glob(os.path.join(out_dir, "profile_*.html")):
            if old_path != html_path:
                try:
                    os.remove(old_path)
                except OSError:
                    continue
        return html_path

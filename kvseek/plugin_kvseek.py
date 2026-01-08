# -*- coding: utf-8 -*-
"""
Søk - QGIS-plugin

Adresse:
  - Kartverkets Adresse REST-API: https://ws.geonorge.no/adresser/v1

Eiendom:
  - Kartverkets Eiendom REST-API for lokalisering/geokoding:
    https://api.kartverket.no/eiendom/v1/geokoding

Stedsnavn:
  - Kartverkets Stedsnavn API:
    https://api.kartverket.no/stedsnavn/v1/navn

UI:
  - Dockbart panel (QDockWidget) + meny/toolbar "Kartverket"
  - Søkeområde fast høyde; resultatlista tar resten

Memory-lag:
  - søkte_adresser   (Point)
  - søkte_eiendommer (Polygon/MultiPolygon)
  - søkte_stedsnavn  (Point)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from qgis.PyQt.QtCore import Qt, QEventLoop, QUrl, QVariant
from qgis.PyQt.QtGui import QIcon, QIntValidator
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QAction,
    QComboBox,
    QDockWidget,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
    QToolBar,
)

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsMessageLog,
    QgsNetworkAccessManager,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsVertexMarker, QgsRubberBand


# -----------------------------
# Konfig
# -----------------------------
PLUGIN_NAME = "kvseek"
LOG_TAG = "kvseek"
MENU_NAME = "&Kartverket"
TOOLBAR_NAME = "Kartverket"

# Ikon
ICON_FILE = "icon_kvseek.svg"

# Adresse-API
ADDR_API_BASE = "https://ws.geonorge.no/adresser/v1"
ADDR_API_SOK = f"{ADDR_API_BASE}/sok"

# Eiendom-API
PROP_API_BASE = "https://api.kartverket.no/eiendom/v1"
PROP_API_GEOKODING = f"{PROP_API_BASE}/geokoding"

# Kommuneinfo
KOMMUNEINFO_PRIMARY = "https://api.kartverket.no/kommuneinfo/v1/kommuner"
KOMMUNEINFO_FALLBACK = "https://ws.geonorge.no/kommuneinfo/v1/kommuner"

# Stedsnavn
PLACE_API_BASE = "https://api.kartverket.no/stedsnavn/v1"
PLACE_API_NAVN = f"{PLACE_API_BASE}/navn"

# Memory layer-navn
LAYER_ADDR = "søkte_adresser"
LAYER_PROP = "søkte_eiendommer"
LAYER_PLACE = "søkte_stedsnavn"


def log(msg: str, level=Qgis.Info) -> None:
    QgsMessageLog.logMessage(msg, LOG_TAG, level)


# -----------------------------
# Datamodell
# -----------------------------
@dataclass
class HitPoint:
    x: float
    y: float
    epsg: int


@dataclass
class SearchHit:
    kind: str  # "adresse" | "eiendom" | "stedsnavn"
    label: str

    # felles
    epsg: Optional[int]
    raw: Dict[str, Any]

    # adresse/stedsnavn: punkt
    point: Optional[HitPoint]

    # eiendom: flate
    geom: Optional[QgsGeometry]
    geom_epsg: Optional[int]

    # adresse: felt
    objtype: str
    kommunenavn: str
    kommunenummer: str
    postnummer: str
    poststed: str
    eiendom_ref: str  # gnr/bnr/...

    # eiendom: matrikkel
    gnr: Optional[int]
    bnr: Optional[int]
    fnr: Optional[int]
    snr: Optional[int]
    teig_id: Optional[int]  # lokalid
    objekttype_eiendom: str  # Teig / osv


# -----------------------------
# Plugin-klasse
# -----------------------------
class KvSeekPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action: Optional[QAction] = None
        self.toolbar: Optional[QToolBar] = None
        self.dock: Optional[QDockWidget] = None
        self.widget: Optional[KvSeekWidget] = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), ICON_FILE)
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self.action = QAction(icon, "Søk – Adresser, eiendommer og stedsnavn", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.toggled.connect(self._toggle_dock)

        # meny
        self.iface.addPluginToMenu(MENU_NAME, self.action)

        # toolbar "Kartverket" (gjenbruk hvis finnes)
        mw = self.iface.mainWindow()
        existing = None
        for tb in mw.findChildren(QToolBar):
            if tb.objectName() == TOOLBAR_NAME or tb.windowTitle() == TOOLBAR_NAME:
                existing = tb
                break
        if existing is None:
            existing = self.iface.addToolBar(TOOLBAR_NAME)
            existing.setObjectName(TOOLBAR_NAME)
        self.toolbar = existing
        self.toolbar.addAction(self.action)

    def unload(self):
        if self.action:
            try:
                self.iface.removePluginMenu(MENU_NAME, self.action)
            except Exception:
                pass
            try:
                if self.toolbar:
                    self.toolbar.removeAction(self.action)
            except Exception:
                pass

        if self.dock:
            try:
                self.iface.removeDockWidget(self.dock)
            except Exception:
                pass
            self.dock.deleteLater()

        self.action = None
        self.toolbar = None
        self.dock = None
        self.widget = None

    def _toggle_dock(self, checked: bool):
        if checked:
            if self.dock is None:
                self.widget = KvSeekWidget(self.iface)
                self.dock = QDockWidget("Søk", self.iface.mainWindow())
                self.dock.setObjectName("KvSeekDock")
                self.dock.setWidget(self.widget)
                self.dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
                self.dock.visibilityChanged.connect(self._on_dock_visibility_changed)
                self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
            self.dock.show()
            self.dock.raise_()
        else:
            if self.dock:
                self.dock.hide()

    def _on_dock_visibility_changed(self, visible: bool):
        # synk action-check med faktisk synlighet
        if self.action:
            self.action.blockSignals(True)
            self.action.setChecked(bool(visible))
            self.action.blockSignals(False)


# -----------------------------
# Dock-widget innhold
# -----------------------------
class KvSeekWidget(QWidget):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface

        self._hits: List[SearchHit] = []
        self._temp_point_marker: Optional[QgsVertexMarker] = None
        self._temp_poly_band: Optional[QgsRubberBand] = None

        self._build_ui()
        self._wire_signals()

        self._load_municipalities()
        self._set_mode_headers("adresse")

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Tabs (søkeområde) - fast høyde
        self.tabs = QTabWidget(self)
        self.tabs.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.tab_addr = QWidget(self)
        self.tab_prop = QWidget(self)
        self.tab_place = QWidget(self)

        self.tabs.addTab(self.tab_addr, "Adresse")
        self.tabs.addTab(self.tab_prop, "Eiendom")
        self.tabs.addTab(self.tab_place, "Stedsnavn")

        self._build_tab_addr()
        self._build_tab_prop()
        self._build_tab_place()

        # Resultater
        result_box = QGroupBox("Treff", self)
        result_layout = QVBoxLayout(result_box)
        result_layout.setContentsMargins(6, 6, 6, 6)

        self.tree = QTreeWidget(self)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)

        btn_row = QHBoxLayout()
        self.btn_zoom = QPushButton("Zoom til valgt", self)
        self.btn_add_layer = QPushButton("Legg til i lag", self)
        self.btn_clear_results = QPushButton("Tøm treff", self)
        btn_row.addWidget(self.btn_zoom)
        btn_row.addWidget(self.btn_add_layer)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_clear_results)

        result_layout.addWidget(self.tree)
        result_layout.addLayout(btn_row)

        # Status
        status_row = QHBoxLayout()
        self.lbl_status = QLabel("Klar.", self)
        self.progress = QProgressBar(self)
        self.progress.setMinimum(0)
        self.progress.setMaximum(100)
        self.progress.setValue(0)
        self.progress.setFixedWidth(180)
        self.progress.setTextVisible(False)
        status_row.addWidget(self.lbl_status, 1)
        status_row.addWidget(self.progress, 0)

        # Stretch: resultatlista vokser, søkeområde ikke
        root.addWidget(self.tabs, 0)
        root.addWidget(result_box, 1)
        root.addLayout(status_row, 0)

        root.setStretchFactor(result_box, 1)

    def _build_tab_addr(self):
        layout = QVBoxLayout(self.tab_addr)
        layout.setContentsMargins(6, 6, 6, 6)

        box = QGroupBox("Søk vegadresse", self.tab_addr)
        form = QFormLayout(box)

        self.inp_adressenavn = QLineEdit(self)
        self.inp_adressenavn.setPlaceholderText("F.eks. Munkegata")

        self.inp_nummer = QLineEdit(self)
        self.inp_nummer.setPlaceholderText("F.eks. 1")
        self.inp_nummer.setValidator(QIntValidator(0, 999999, self))

        self.inp_bokstav = QLineEdit(self)
        self.inp_bokstav.setPlaceholderText("F.eks. B")
        self.inp_bokstav.setMaxLength(2)

        form.addRow("Adressenavn:", self.inp_adressenavn)
        form.addRow("Husnr:", self.inp_nummer)
        form.addRow("Bokstav:", self.inp_bokstav)

        row = QHBoxLayout()
        self.btn_search_addr = QPushButton("Søk", self)
        self.btn_clear_addr = QPushButton("Tøm", self)
        row.addStretch(1)
        row.addWidget(self.btn_search_addr)
        row.addWidget(self.btn_clear_addr)

        layout.addWidget(box)
        layout.addLayout(row)

    def _build_tab_prop(self):
        layout = QVBoxLayout(self.tab_prop)
        layout.setContentsMargins(6, 6, 6, 6)

        box = QGroupBox("Søk eiendom", self.tab_prop)
        grid = QGridLayout(box)

        self.cmb_kommune = QComboBox(self)
        self.cmb_kommune.setEditable(True)
        self.cmb_kommune.setInsertPolicy(QComboBox.NoInsert)
        if self.cmb_kommune.lineEdit():
            self.cmb_kommune.lineEdit().setPlaceholderText("Velg / skriv kommunenavn…")

        def mk_spin():
            s = QSpinBox(self)
            s.setRange(0, 999999)
            s.setSpecialValueText("")
            s.setValue(0)
            return s

        self.sp_gnr = mk_spin()
        self.sp_bnr = mk_spin()
        self.sp_fnr = mk_spin()
        self.sp_snr = mk_spin()

        grid.addWidget(QLabel("Kommune:"), 0, 0)
        grid.addWidget(self.cmb_kommune, 0, 1, 1, 3)

        grid.addWidget(QLabel("Gnr:"), 1, 0)
        grid.addWidget(self.sp_gnr, 1, 1)
        grid.addWidget(QLabel("Bnr:"), 1, 2)
        grid.addWidget(self.sp_bnr, 1, 3)

        grid.addWidget(QLabel("Fnr:"), 2, 0)
        grid.addWidget(self.sp_fnr, 2, 1)
        grid.addWidget(QLabel("Snr:"), 2, 2)
        grid.addWidget(self.sp_snr, 2, 3)

        row = QHBoxLayout()
        self.btn_search_prop = QPushButton("Søk", self)
        self.btn_clear_prop = QPushButton("Tøm", self)
        row.addStretch(1)
        row.addWidget(self.btn_search_prop)
        row.addWidget(self.btn_clear_prop)

        layout.addWidget(box)
        layout.addLayout(row)

    def _build_tab_place(self):
        layout = QVBoxLayout(self.tab_place)
        layout.setContentsMargins(6, 6, 6, 6)

        box = QGroupBox("Søk stedsnavn", self.tab_place)
        form = QFormLayout(box)

        self.inp_place = QLineEdit(self)
        self.inp_place.setPlaceholderText("F.eks. Ringkollen")
        form.addRow("Navn:", self.inp_place)

        row = QHBoxLayout()
        self.btn_search_place = QPushButton("Søk", self)
        self.btn_clear_place = QPushButton("Tøm", self)
        row.addStretch(1)
        row.addWidget(self.btn_search_place)
        row.addWidget(self.btn_clear_place)

        layout.addWidget(box)
        layout.addLayout(row)

    # ---------- Signals ----------
    def _wire_signals(self):
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Adresse
        self.btn_search_addr.clicked.connect(self.on_search_addr)
        self.btn_clear_addr.clicked.connect(self.on_clear_addr_fields)
        self.inp_adressenavn.returnPressed.connect(self.on_search_addr)
        self.inp_nummer.returnPressed.connect(self.on_search_addr)
        self.inp_bokstav.returnPressed.connect(self.on_search_addr)

        # Eiendom
        self.btn_search_prop.clicked.connect(self.on_search_prop)
        self.btn_clear_prop.clicked.connect(self.on_clear_prop_fields)
        if self.cmb_kommune.lineEdit():
            self.cmb_kommune.lineEdit().returnPressed.connect(self.on_search_prop)

        # Stedsnavn
        self.btn_search_place.clicked.connect(self.on_search_place)
        self.btn_clear_place.clicked.connect(self.on_clear_place_fields)
        self.inp_place.returnPressed.connect(self.on_search_place)

        # Resultater
        self.btn_clear_results.clicked.connect(self.on_clear_results)
        self.btn_zoom.clicked.connect(self.on_zoom_selected)
        self.btn_add_layer.clicked.connect(self.on_add_to_layer_selected)

        self.tree.itemDoubleClicked.connect(lambda *_: self.on_zoom_selected())
        self.tree.currentItemChanged.connect(self.on_tree_current_changed)

    # ---------- Mode / headers ----------
    def _on_tab_changed(self, idx: int):
        if idx == 0:
            self._set_mode_headers("adresse")
        elif idx == 1:
            self._set_mode_headers("eiendom")
        else:
            self._set_mode_headers("stedsnavn")

    def _set_mode_headers(self, mode: str):
        self.tree.clear()
        self._hits = []
        self._clear_temp_highlights()

        if mode == "adresse":
            self.tree.setColumnCount(8)
            self.tree.setHeaderLabels(["Adresse", "Objtype", "Kommune", "Gnr/Bnr", "Postnr", "Poststed", "EPSG", "X/Y"])
        elif mode == "eiendom":
            self.tree.setColumnCount(9)
            self.tree.setHeaderLabels(["Eiendom", "Objekt", "Gnr", "Bnr", "Fnr", "Snr", "TeigID", "EPSG", "Geom"])
        else:
            self.tree.setColumnCount(6)
            self.tree.setHeaderLabels(["Navn", "Type", "Kommune", "EPSG", "X", "Y"])

        for i in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(i)

    # ---------- Busy ----------
    def _set_busy(self, busy: bool, text: str = ""):
        if busy:
            self.progress.setValue(25)
            self.lbl_status.setText(text or "Jobber…")
        else:
            self.progress.setValue(0)
            self.lbl_status.setText(text or "Klar.")

        for w in (
            self.btn_search_addr,
            self.btn_clear_addr,
            self.btn_search_prop,
            self.btn_clear_prop,
            self.btn_search_place,
            self.btn_clear_place,
            self.btn_zoom,
            self.btn_add_layer,
            self.btn_clear_results,
        ):
            w.setEnabled(not busy)

    # ---------- CRS ----------
    def _project_crs(self) -> QgsCoordinateReferenceSystem:
        return self.iface.mapCanvas().mapSettings().destinationCrs()

    def _project_epsg(self) -> int:
        crs = self._project_crs()
        try:
            srid = int(crs.postgisSrid()) if crs.isValid() else 0
        except Exception:
            srid = 0
        return srid if srid > 0 else 25833  # default i Norge-prosjekter

    def _crs_from_epsg(self, epsg: int) -> QgsCoordinateReferenceSystem:
        crs = QgsCoordinateReferenceSystem(f"EPSG:{epsg}")
        if not crs.isValid():
            crs = QgsCoordinateReferenceSystem("EPSG:4258")
        return crs

    def _transform_point_xy(self, x: float, y: float, src_epsg: int, dest_crs: QgsCoordinateReferenceSystem) -> QgsPointXY:
        src_crs = self._crs_from_epsg(src_epsg)
        pt = QgsPointXY(x, y)
        if src_crs.authid() != dest_crs.authid():
            xform = QgsCoordinateTransform(src_crs, dest_crs, QgsProject.instance())
            pt = xform.transform(pt)
        return QgsPointXY(pt.x(), pt.y())

    def _transform_geometry(self, geom: QgsGeometry, src_epsg: int, dest_crs: QgsCoordinateReferenceSystem) -> QgsGeometry:
        src_crs = self._crs_from_epsg(src_epsg)
        g = QgsGeometry(geom)
        if src_crs.authid() != dest_crs.authid():
            xform = QgsCoordinateTransform(src_crs, dest_crs, QgsProject.instance())
            g.transform(xform)
        return g

    # ---------- HTTP ----------
    def _api_get_json(self, url: str, params: Dict[str, Any]) -> Any:
        from qgis.PyQt.QtCore import QUrlQuery

        qurl = QUrl(url)
        query = QUrlQuery()
        for k, v in params.items():
            if v is None:
                continue
            query.addQueryItem(str(k), str(v))
        qurl.setQuery(query)

        req = QNetworkRequest(qurl)
        req.setHeader(QNetworkRequest.UserAgentHeader, f"{PLUGIN_NAME}/0.9 (QGIS)")
        nam = QgsNetworkAccessManager.instance()

        reply = nam.get(req)
        loop = QEventLoop()
        reply.finished.connect(loop.quit)
        loop.exec()

        if reply.error():
            raise RuntimeError(f"HTTP-feil: {reply.errorString()}")

        raw = bytes(reply.readAll()).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except Exception as e:
            raise RuntimeError(f"Kunne ikke parse JSON: {e}")

    # ---------- Kommune-liste ----------
    def _load_municipalities(self):
        self.cmb_kommune.clear()
        self.cmb_kommune.addItem("", None)

        endpoints = [KOMMUNEINFO_PRIMARY, KOMMUNEINFO_FALLBACK]
        for ep in endpoints:
            try:
                data = self._api_get_json(ep, {})
                kommuner = self._parse_kommuner_payload(data)
                if kommuner:
                    kommuner.sort(key=lambda x: x[1].lower())
                    for kommunenummer, kommunenavn in kommuner:
                        self.cmb_kommune.addItem(f"{kommunenavn} ({kommunenummer})", kommunenummer)
                    log(f"Lastet {len(kommuner)} kommuner fra {ep}", Qgis.Info)
                    return
            except Exception as e:
                log(f"Klarte ikke laste kommuner fra {ep}: {e}", Qgis.Warning)

        self.lbl_status.setText("Obs: Klarte ikke laste kommuneliste.")

    def _parse_kommuner_payload(self, data: Any) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("kommuner") or data.get("data") or data.get("content") or []
        else:
            items = []
        if not isinstance(items, list):
            return out

        for it in items:
            if not isinstance(it, dict):
                continue
            nr = it.get("kommunenummer") or it.get("kommuneNr") or it.get("nummer") or it.get("kode")
            navn = it.get("kommunenavn") or it.get("kommuneNavn") or it.get("navn") or it.get("name")
            if nr is None or navn is None:
                continue
            nr_s = str(nr).strip()
            navn_s = str(navn).strip()
            if nr_s and navn_s:
                out.append((nr_s, navn_s))
        return out

    def _selected_kommune(self) -> Tuple[Optional[str], Optional[str]]:
        txt = (self.cmb_kommune.currentText() or "").strip()
        nr = self.cmb_kommune.currentData()

        if isinstance(nr, str) and nr.strip():
            navn = txt
            if "(" in navn and navn.endswith(")"):
                navn = navn.rsplit("(", 1)[0].strip()
            return nr.strip(), (navn or None)

        # fallback: bruk 4 første sifre hvis bruker skrev
        if len(txt) >= 4 and txt[:4].isdigit():
            return txt[:4], (txt[4:].strip() or None)

        return None, (txt or None)

    # ---------- GeoJSON helpers ----------
    def _parse_epsg_from_crs(self, data: Any, fallback_epsg: int) -> int:
        # Eiendom API kan ha "crs": {"properties":{"name":"urn:ogc:def:crs:EPSG::4258"}}
        try:
            if isinstance(data, dict) and "crs" in data:
                crs = data["crs"]
                if isinstance(crs, dict):
                    name = crs.get("properties", {}).get("name") or crs.get("name")
                    if isinstance(name, str):
                        m = re.search(r"EPSG[:/]{1,2}(\d+)", name.upper())
                        if m:
                            return int(m.group(1))
                elif isinstance(crs, str):
                    m = re.search(r"(\d+)", crs)
                    if m:
                        return int(m.group(1))
        except Exception:
            pass
        return fallback_epsg

    def _geojson_to_qgsgeometry(self, gj: Dict[str, Any]) -> Optional[QgsGeometry]:
        try:
            t = gj.get("type")
            coords = gj.get("coordinates")
            if not isinstance(t, str) or not isinstance(coords, list):
                return None

            def ring_to_points(ring: List[List[float]]) -> List[QgsPointXY]:
                return [QgsPointXY(float(p[0]), float(p[1])) for p in ring if isinstance(p, list) and len(p) >= 2]

            if t == "Polygon":
                rings_xy = []
                for ring in coords:
                    if isinstance(ring, list):
                        rings_xy.append(ring_to_points(ring))
                if not rings_xy or not rings_xy[0]:
                    return None
                return QgsGeometry.fromPolygonXY(rings_xy)

            if t == "MultiPolygon":
                mp: List[List[List[QgsPointXY]]] = []
                for poly in coords:
                    if not isinstance(poly, list):
                        continue
                    rings_xy = []
                    for ring in poly:
                        if isinstance(ring, list):
                            rings_xy.append(ring_to_points(ring))
                    if rings_xy and rings_xy[0]:
                        mp.append(rings_xy)
                if not mp:
                    return None
                return QgsGeometry.fromMultiPolygonXY(mp)

        except Exception:
            return None
        return None

    # ---------- Adresse parsing ----------
    def _extract_point_from_address_obj(self, obj: Dict[str, Any]) -> Optional[HitPoint]:
        rp = obj.get("representasjonspunkt") or obj.get("adresseringspunkt") or obj.get("punkt")
        if not isinstance(rp, dict):
            return None

        epsg_raw = rp.get("epsg")

        lon = rp.get("lon")
        lat = rp.get("lat")
        x = rp.get("x")
        y = rp.get("y")
        ost = rp.get("ost") or rp.get("øst")
        nord = rp.get("nord")

        try:
            if epsg_raw is None:
                epsg = 4258
            else:
                s = str(epsg_raw).upper().replace("EPSG:", "").strip()
                epsg = int(s) if s.isdigit() else 4258

            if lat is not None and lon is not None:
                return HitPoint(x=float(lon), y=float(lat), epsg=epsg)
            if x is not None and y is not None:
                return HitPoint(x=float(x), y=float(y), epsg=epsg)
            if ost is not None and nord is not None:
                return HitPoint(x=float(ost), y=float(nord), epsg=epsg)
        except Exception:
            return None
        return None

    def _fmt_eiendom_ref_from_address_obj(self, obj: Dict[str, Any]) -> str:
        gnr = obj.get("gardsnummer")
        bnr = obj.get("bruksnummer")
        fnr = obj.get("festenummer")
        snr = obj.get("undernummer")
        if gnr is None or bnr is None:
            return ""
        base = f"{gnr}/{bnr}"
        if fnr:
            base += f"/{fnr}"
        if snr:
            base += f"-{snr}"
        return base

    def _address_obj_to_hit(self, obj: Dict[str, Any]) -> SearchHit:
        label = (obj.get("adressetekst") or obj.get("adresseTekst") or "").strip()
        objtype = (obj.get("objtype") or "Vegadresse").strip()
        kommunenavn = (obj.get("kommunenavn") or "").strip()
        kommunenummer = (obj.get("kommunenummer") or "").strip()
        postnummer = (obj.get("postnummer") or "").strip()
        poststed = (obj.get("poststed") or "").strip()
        eiendom_ref = self._fmt_eiendom_ref_from_address_obj(obj)
        pt = self._extract_point_from_address_obj(obj)
        epsg = pt.epsg if pt else None

        return SearchHit(
            kind="adresse",
            label=label,
            epsg=epsg,
            raw=obj,
            point=pt,
            geom=None,
            geom_epsg=None,
            objtype=objtype,
            kommunenavn=kommunenavn,
            kommunenummer=kommunenummer,
            postnummer=postnummer,
            poststed=poststed,
            eiendom_ref=eiendom_ref,
            gnr=None, bnr=None, fnr=None, snr=None, teig_id=None,
            objekttype_eiendom="",
        )

    # ---------- Stedsnavn parsing ----------
    def _place_obj_to_hit(self, obj: Dict[str, Any]) -> Optional[SearchHit]:
        navn = (obj.get("stedsnavn") or obj.get("skrivemåte") or obj.get("navn") or "").strip()
        if not navn:
            return None
        ntype = (obj.get("navnetype") or obj.get("type") or "").strip()
        kommunenavn = (obj.get("kommunenavn") or "").strip()

        # representasjonspunkt: noen payloads har "representasjonspunkt": {"koordinatsystem":25833,"x":...,"y":...}
        rp = obj.get("representasjonspunkt") or obj.get("punkt") or {}
        epsg = None
        x = y = None
        if isinstance(rp, dict):
            epsg = rp.get("epsg") or rp.get("koordinatsystem")
            x = rp.get("x") or rp.get("ost") or rp.get("øst") or rp.get("lon")
            y = rp.get("y") or rp.get("nord") or rp.get("lat")

        try:
            epsg_i = int(str(epsg).upper().replace("EPSG:", "").strip()) if epsg is not None else 4258
            if x is None or y is None:
                return None
            pt = HitPoint(x=float(x), y=float(y), epsg=epsg_i)
        except Exception:
            return None

        return SearchHit(
            kind="stedsnavn",
            label=navn,
            epsg=pt.epsg,
            raw=obj,
            point=pt,
            geom=None,
            geom_epsg=None,
            objtype=ntype,
            kommunenavn=kommunenavn,
            kommunenummer="",
            postnummer="",
            poststed="",
            eiendom_ref="",
            gnr=None, bnr=None, fnr=None, snr=None, teig_id=None,
            objekttype_eiendom="",
        )

    # ---------- Eiendom parsing (VIKTIG: ingen deduplisering) ----------
    def _feature_to_property_hit(self, feature: Dict[str, Any], src_epsg: int) -> Optional[SearchHit]:
        if not isinstance(feature, dict):
            return None

        props = feature.get("properties") or {}
        geom_gj = feature.get("geometry") or {}

        geom = self._geojson_to_qgsgeometry(geom_gj) if isinstance(geom_gj, dict) else None
        if not geom or geom.isEmpty():
            return None

        try:
            gnr = int(props.get("gardsnummer") or 0)
            bnr = int(props.get("bruksnummer") or 0)
            fnr = int(props.get("festenummer") or 0)
            snr = int(props.get("seksjonsnummer") or 0)
        except Exception:
            gnr = bnr = fnr = snr = 0

        teig_id = props.get("lokalid")
        try:
            teig_id_i = int(teig_id) if teig_id is not None else None
        except Exception:
            teig_id_i = None

        objekttype = str(props.get("objekttype") or "Teig").strip()

        # Visningsstreng: bruk matrikkelnummertekst hvis den finnes, men legg på /fnr og /snr der det er relevant
        mtxt = str(props.get("matrikkelnummertekst") or "").strip()
        if not mtxt and gnr and bnr:
            mtxt = f"{gnr}/{bnr}"

        # sikre at fnr/snr faktisk synes i lista, selv om matrikkelnummertekst bare er "100/1"
        eiendom_vis = mtxt
        if fnr and fnr > 0 and f"/{fnr}" not in eiendom_vis:
            eiendom_vis = f"{eiendom_vis}/{fnr}"
        if snr and snr > 0:
            # mange bruker "-snr" visuelt; her lager vi tydelig egen kolonne uansett
            pass

        # "Objekt" kolonne (det du ønsker å se tydelig)
        # - festetomt: fnr>0
        # - seksjon: snr>0
        # - ellers: grunneiendom
        if snr and snr > 0:
            obj_label = "Seksjon"
        elif fnr and fnr > 0:
            obj_label = "Festetomt"
        else:
            obj_label = "Eiendom"

        label = eiendom_vis

        return SearchHit(
            kind="eiendom",
            label=label,
            epsg=src_epsg,
            raw={"feature": feature},
            point=None,
            geom=geom,
            geom_epsg=src_epsg,
            objtype=obj_label,
            kommunenavn="",
            kommunenummer=str(props.get("kommunenummer") or ""),
            postnummer="",
            poststed="",
            eiendom_ref=eiendom_vis,
            gnr=gnr or None,
            bnr=bnr or None,
            fnr=fnr or None,
            snr=snr or None,
            teig_id=teig_id_i,
            objekttype_eiendom=objekttype,
        )

    def _parse_property_featurecollection(self, data: Any, fallback_epsg: int) -> Tuple[List[SearchHit], int]:
        if not isinstance(data, dict):
            return [], fallback_epsg

        src_epsg = self._parse_epsg_from_crs(data, fallback_epsg)
        feats = data.get("features") or []
        if not isinstance(feats, list):
            return [], src_epsg

        hits: List[SearchHit] = []
        for f in feats:
            hit = self._feature_to_property_hit(f, src_epsg)
            if hit:
                hits.append(hit)

        # Ingen deduplisering: én rad per feature (teig/festetomt/seksjon)
        return hits, src_epsg

    # ---------- Rendering ----------
    def _render_hits(self, hits: List[SearchHit]):
        self.tree.clear()
        self._hits = hits

        self.lbl_status.setText(f"Fant {len(hits)} treff." if hits else "Ingen treff.")
        self._clear_temp_highlights()

        for idx, h in enumerate(hits):
            if h.kind == "adresse":
                xy = ""
                if h.point:
                    xy = f"{h.point.x:.3f}, {h.point.y:.3f}"
                item = QTreeWidgetItem([
                    h.label,
                    h.objtype,
                    h.kommunenavn or h.kommunenummer,
                    h.eiendom_ref or "",
                    h.postnummer or "",
                    h.poststed or "",
                    str(h.point.epsg) if h.point else "",
                    xy,
                ])

            elif h.kind == "eiendom":
                item = QTreeWidgetItem([
                    h.eiendom_ref or h.label,
                    h.objtype or "",
                    str(h.gnr or ""),
                    str(h.bnr or ""),
                    str(h.fnr or 0),
                    str(h.snr or 0),
                    str(h.teig_id or ""),
                    str(h.geom_epsg or ""),
                    h.objekttype_eiendom or "",
                ])

            else:  # stedsnavn
                item = QTreeWidgetItem([
                    h.label,
                    h.objtype or "",
                    h.kommunenavn or "",
                    str(h.point.epsg) if h.point else "",
                    f"{h.point.x:.3f}" if h.point else "",
                    f"{h.point.y:.3f}" if h.point else "",
                ])

            item.setData(0, Qt.UserRole, idx)
            self.tree.addTopLevelItem(item)

        for i in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(i)

        if hits:
            first = self.tree.topLevelItem(0)
            if first:
                self.tree.setCurrentItem(first)
            self._preview_hit(hits[0])

    def _selected_hit(self) -> Optional[SearchHit]:
        items = self.tree.selectedItems()
        if not items:
            return None
        idx = items[0].data(0, Qt.UserRole)
        try:
            return self._hits[int(idx)]
        except Exception:
            return None

    # ---------- Temporary highlight ----------
    def _clear_temp_highlights(self):
        if self._temp_point_marker is not None:
            self._temp_point_marker.setVisible(False)
            self._temp_point_marker = None
        if self._temp_poly_band is not None:
            try:
                self._temp_poly_band.reset(QgsWkbTypes.PolygonGeometry)
            except Exception:
                pass
            self._temp_poly_band = None

    def _preview_hit(self, hit: SearchHit):
        self._clear_temp_highlights()
        canvas = self.iface.mapCanvas()
        dest_crs = self._project_crs()

        if hit.kind in ("adresse", "stedsnavn") and hit.point:
            try:
                pt = self._transform_point_xy(hit.point.x, hit.point.y, hit.point.epsg, dest_crs)
            except Exception:
                return
            m = QgsVertexMarker(canvas)
            m.setCenter(pt)
            m.setIconType(QgsVertexMarker.ICON_CROSS)
            m.setIconSize(18)
            m.setPenWidth(3)
            self._temp_point_marker = m
            return

        if hit.kind == "eiendom" and hit.geom and hit.geom_epsg:
            try:
                g = self._transform_geometry(hit.geom, hit.geom_epsg, dest_crs)
            except Exception:
                return
            rb = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
            rb.setToGeometry(g, None)
            rb.setWidth(2)
            self._temp_poly_band = rb
            return

    # ---------- Map actions ----------
    def _zoom_to_hit(self, hit: SearchHit):
        canvas = self.iface.mapCanvas()
        dest_crs = self._project_crs()

        if hit.kind in ("adresse", "stedsnavn") and hit.point:
            try:
                pt = self._transform_point_xy(hit.point.x, hit.point.y, hit.point.epsg, dest_crs)
            except Exception as e:
                QMessageBox.warning(self, "Søk", f"Kunne ikke transformere punkt:\n{e}")
                return
            buf = 200.0
            rect = QgsRectangle(pt.x() - buf, pt.y() - buf, pt.x() + buf, pt.y() + buf)
            canvas.setExtent(rect)
            canvas.refresh()
            return

        if hit.kind == "eiendom" and hit.geom and hit.geom_epsg:
            try:
                g = self._transform_geometry(hit.geom, hit.geom_epsg, dest_crs)
            except Exception as e:
                QMessageBox.warning(self, "Søk", f"Kunne ikke transformere flate:\n{e}")
                return
            rect = g.boundingBox()
            rect.grow(rect.width() * 0.10 if rect.width() > 0 else 50.0)
            canvas.setExtent(rect)
            canvas.refresh()
            return

        QMessageBox.information(self, "Søk", "Valgt treff mangler geometri.")

    # ---------- Memory layers ----------
    def _get_or_create_address_layer(self) -> QgsVectorLayer:
        prj = QgsProject.instance()
        for lyr in prj.mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.name() == LAYER_ADDR and lyr.providerType() == "memory":
                return lyr

        epsg = self._project_epsg()
        layer = QgsVectorLayer(f"Point?crs=EPSG:{epsg}", LAYER_ADDR, "memory")
        prov = layer.dataProvider()
        prov.addAttributes([
            QgsField("label", QVariant.String),
            QgsField("objtype", QVariant.String),
            QgsField("kommunenavn", QVariant.String),
            QgsField("kommunenr", QVariant.String),
            QgsField("eiendom", QVariant.String),
            QgsField("postnr", QVariant.String),
            QgsField("poststed", QVariant.String),
        ])
        layer.updateFields()
        prj.addMapLayer(layer)
        return layer

    def _get_or_create_property_layer(self) -> QgsVectorLayer:
        prj = QgsProject.instance()
        for lyr in prj.mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.name() == LAYER_PROP and lyr.providerType() == "memory":
                return lyr

        epsg = self._project_epsg()
        layer = QgsVectorLayer(f"Polygon?crs=EPSG:{epsg}", LAYER_PROP, "memory")
        prov = layer.dataProvider()
        prov.addAttributes([
            QgsField("eiendom", QVariant.String),
            QgsField("objekt", QVariant.String),      # Eiendom/Festetomt/Seksjon
            QgsField("gnr", QVariant.Int),
            QgsField("bnr", QVariant.Int),
            QgsField("fnr", QVariant.Int),
            QgsField("snr", QVariant.Int),
            QgsField("teig_id", QVariant.LongLong),
            QgsField("objekttype", QVariant.String),  # Teig
            QgsField("kommunenr", QVariant.String),
        ])
        layer.updateFields()
        prj.addMapLayer(layer)
        return layer

    def _get_or_create_place_layer(self) -> QgsVectorLayer:
        prj = QgsProject.instance()
        for lyr in prj.mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.name() == LAYER_PLACE and lyr.providerType() == "memory":
                return lyr

        epsg = self._project_epsg()
        layer = QgsVectorLayer(f"Point?crs=EPSG:{epsg}", LAYER_PLACE, "memory")
        prov = layer.dataProvider()
        prov.addAttributes([
            QgsField("navn", QVariant.String),
            QgsField("type", QVariant.String),
            QgsField("kommune", QVariant.String),
        ])
        layer.updateFields()
        prj.addMapLayer(layer)
        return layer

    def _add_hit_to_layer(self, hit: SearchHit):
        if hit.kind == "adresse":
            if not hit.point:
                QMessageBox.information(self, "Søk", "Adresse-treff mangler punkt.")
                return
            layer = self._get_or_create_address_layer()
            try:
                pt = self._transform_point_xy(hit.point.x, hit.point.y, hit.point.epsg, layer.crs())
            except Exception as e:
                QMessageBox.warning(self, "Søk", f"Kunne ikke transformere punkt:\n{e}")
                return

            feat = QgsFeature(layer.fields())
            feat.setGeometry(QgsGeometry.fromPointXY(pt))
            feat["label"] = hit.label
            feat["objtype"] = hit.objtype
            feat["kommunenavn"] = hit.kommunenavn
            feat["kommunenr"] = hit.kommunenummer
            feat["eiendom"] = hit.eiendom_ref
            feat["postnr"] = hit.postnummer
            feat["poststed"] = hit.poststed

            layer.startEditing()
            ok = layer.addFeature(feat)
            layer.commitChanges()
            layer.triggerRepaint()
            if not ok:
                QMessageBox.warning(self, "Søk", "Kunne ikke legge til feature i søkte_adresser.")
            return

        if hit.kind == "eiendom":
            if not hit.geom or not hit.geom_epsg:
                QMessageBox.information(self, "Søk", "Eiendom-treff mangler flate.")
                return
            layer = self._get_or_create_property_layer()
            try:
                g = self._transform_geometry(hit.geom, hit.geom_epsg, layer.crs())
            except Exception as e:
                QMessageBox.warning(self, "Søk", f"Kunne ikke transformere flate:\n{e}")
                return

            feat = QgsFeature(layer.fields())
            feat.setGeometry(g)
            feat["eiendom"] = hit.eiendom_ref or hit.label
            feat["objekt"] = hit.objtype
            feat["gnr"] = int(hit.gnr or 0)
            feat["bnr"] = int(hit.bnr or 0)
            feat["fnr"] = int(hit.fnr or 0)
            feat["snr"] = int(hit.snr or 0)
            feat["teig_id"] = int(hit.teig_id or 0)
            feat["objekttype"] = hit.objekttype_eiendom
            feat["kommunenr"] = hit.kommunenummer

            layer.startEditing()
            ok = layer.addFeature(feat)
            layer.commitChanges()
            layer.triggerRepaint()
            if not ok:
                QMessageBox.warning(self, "Søk", "Kunne ikke legge til feature i søkte_eiendommer.")
            return

        if hit.kind == "stedsnavn":
            if not hit.point:
                QMessageBox.information(self, "Søk", "Stedsnavn mangler punkt.")
                return
            layer = self._get_or_create_place_layer()
            try:
                pt = self._transform_point_xy(hit.point.x, hit.point.y, hit.point.epsg, layer.crs())
            except Exception as e:
                QMessageBox.warning(self, "Søk", f"Kunne ikke transformere punkt:\n{e}")
                return

            feat = QgsFeature(layer.fields())
            feat.setGeometry(QgsGeometry.fromPointXY(pt))
            feat["navn"] = hit.label
            feat["type"] = hit.objtype
            feat["kommune"] = hit.kommunenavn

            layer.startEditing()
            ok = layer.addFeature(feat)
            layer.commitChanges()
            layer.triggerRepaint()
            if not ok:
                QMessageBox.warning(self, "Søk", "Kunne ikke legge til feature i søkte_stedsnavn.")
            return

    # ---------- UI callbacks ----------
    def on_tree_current_changed(self, current, previous):
        hit = self._selected_hit()
        if hit:
            self._preview_hit(hit)

    def on_clear_addr_fields(self):
        self.inp_adressenavn.clear()
        self.inp_nummer.clear()
        self.inp_bokstav.clear()
        self.inp_adressenavn.setFocus()

    def on_clear_prop_fields(self):
        self.cmb_kommune.setCurrentIndex(0)
        self.sp_gnr.setValue(0)
        self.sp_bnr.setValue(0)
        self.sp_fnr.setValue(0)
        self.sp_snr.setValue(0)
        if self.cmb_kommune.lineEdit():
            self.cmb_kommune.lineEdit().setText("")
            self.cmb_kommune.lineEdit().setFocus()

    def on_clear_place_fields(self):
        self.inp_place.clear()
        self.inp_place.setFocus()

    def on_clear_results(self):
        self.tree.clear()
        self._hits = []
        self.lbl_status.setText("Klar.")
        self.progress.setValue(0)
        self._clear_temp_highlights()

    def on_zoom_selected(self):
        hit = self._selected_hit()
        if hit:
            self._zoom_to_hit(hit)

    def on_add_to_layer_selected(self):
        hit = self._selected_hit()
        if hit:
            self._add_hit_to_layer(hit)

    # ---------- Searches ----------
    def on_search_addr(self):
        adressenavn = self.inp_adressenavn.text().strip()
        nummer_txt = self.inp_nummer.text().strip()
        bokstav = self.inp_bokstav.text().strip()

        if not adressenavn and not nummer_txt and not bokstav:
            QMessageBox.information(self, "Søk", "Fyll inn minst ett felt for å søke.")
            return

        params: Dict[str, Any] = {
            "objtype": "Vegadresse",
            "adressenavn": adressenavn or None,
            "nummer": int(nummer_txt) if nummer_txt.isdigit() else None,
            "bokstav": bokstav or None,
            "treffPerSide": 100,
            "side": 0,
            "utkoordsys": self._project_epsg(),
            "asciiKompatibel": True,
        }

        self._set_busy(True, "Søker adresser…")
        try:
            data = self._api_get_json(ADDR_API_SOK, params)
            hits_raw = data.get("adresser") or []
            hits = [self._address_obj_to_hit(o) for o in hits_raw if isinstance(o, dict)]
            self._set_mode_headers("adresse")
            self._render_hits(hits)
        except Exception as e:
            log(f"Adresse-søk feilet: {e}", Qgis.Critical)
            QMessageBox.warning(self, "Søk", f"Adresse-søk feilet:\n{e}")
        finally:
            self._set_busy(False, "Klar.")

    def on_search_prop(self):
        kommunenummer, kommunenavn = self._selected_kommune()
        gnr = self.sp_gnr.value()
        bnr = self.sp_bnr.value()
        fnr = self.sp_fnr.value()
        snr = self.sp_snr.value()

        if not kommunenummer:
            QMessageBox.information(self, "Søk", "Velg kommune (kommunenummer) for eiendomssøk.")
            return
        if gnr <= 0 or bnr <= 0:
            QMessageBox.information(self, "Søk", "Fyll inn minst gnr og bnr.")
            return

        out_epsg = self._project_epsg()

        # Viktig: send bare fnr/snr hvis bruker faktisk spesifiserer dem.
        params: Dict[str, Any] = {
            "omrade": "true",
            "kommunenummer": kommunenummer,
            "gardsnummer": gnr,
            "bruksnummer": bnr,
            "utkoordsys": out_epsg,
        }
        if fnr > 0:
            params["festenummer"] = fnr
        if snr > 0:
            params["seksjonsnummer"] = snr

        log(f"Eiendom søk: params={params}", Qgis.Info)

        self._set_busy(True, "Søker eiendom…")
        try:
            data = self._api_get_json(PROP_API_GEOKODING, params)
            keys = list(data.keys()) if isinstance(data, dict) else []
            log(f"Eiendom respons keys={keys}", Qgis.Info)

            hits, src_epsg = self._parse_property_featurecollection(data, fallback_epsg=out_epsg)
            log(f"Eiendom parse: total={len(hits)}, src_epsg={src_epsg}", Qgis.Info)

            self._set_mode_headers("eiendom")
            self._render_hits(hits)

            if not hits:
                QMessageBox.information(self, "Søk", "Ingen eiendomstreff (ingen flater i responsen).")

        except Exception as e:
            log(f"Eiendom-søk feilet: {e}", Qgis.Critical)
            QMessageBox.warning(self, "Søk", f"Eiendom-søk feilet:\n{e}")
        finally:
            self._set_busy(False, "Klar.")

    def on_search_place(self):
        s = self.inp_place.text().strip()
        if len(s) < 2:
            QMessageBox.information(self, "Søk", "Skriv minst 2 tegn.")
            return

        # OBS: Stedsnavn-API kan være streng på parametre, hold det enkelt.
        params = {"sok": s, "treffPerSide": 200, "side": 0}

        self._set_busy(True, "Søker stedsnavn…")
        try:
            data = self._api_get_json(PLACE_API_NAVN, params)

            # data kan være {"navn":[...]} eller liste – håndter begge
            items = []
            if isinstance(data, dict):
                items = data.get("navn") or data.get("data") or data.get("resultater") or []
            elif isinstance(data, list):
                items = data

            hits: List[SearchHit] = []
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    h = self._place_obj_to_hit(it)
                    if h:
                        hits.append(h)

            self._set_mode_headers("stedsnavn")
            self._render_hits(hits)

        except Exception as e:
            log(f"Feil ved stedsnavnsøk: {e}", Qgis.Warning)
            QMessageBox.warning(self, "Søk", f"Feil ved stedsnavnsøk:\n{e}")
        finally:
            self._set_busy(False, "Klar.")


# QGIS entrypoint
def classFactory(iface):
    return KvSeekPlugin(iface)

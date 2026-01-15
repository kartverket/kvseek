# -*- coding: utf-8 -*-
"""
Søk - QGIS-plugin

Adresse:
  - Kartverkets Adresse REST-API:
    https://api.geonorge.no/adresser/v1

Eiendom:
  - Kartverkets Eiendom REST-API for lokalisering/geokoding:
    https://api.kartverket.no/eiendom/v1/geokoding

Fylke:
  - Kartverkets REST-API for administrative enheter:
    https://api.kartverket.no/kommuneinfo/v1/fylker/{fylkesnummer}/omrade

Kommune:
  - Kartverkets REST-API for administrative enheter:
    https://api.kartverket.no/kommuneinfo/v1/kommuner/{kommunenummer}/omrade    

Stedsnavn:
  - Kartverkets Stedsnavn REST-API:
    https://api.kartverket.no/stedsnavn/v1/navn

UI:
  - Dockbart panel (QDockWidget) + meny/toolbar "Kartverket"
  - Søkeområde fast høyde; resultatlista tar resten

Memory-lag:
  - søkte_adresser   (Point)
  - søkte_eiendommer (Polygon/MultiPolygon)
  - søkte_stedsnavn  (Point)
  - søkte_fylker (Polygon/MultiPolygon)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from qgis.PyQt.QtCore import Qt, QEventLoop, QUrl, QVariant
try:
    from qgis.PyQt.QtCore import QMetaType  # QGIS 4 / Qt6
except Exception:
    QMetaType = None  # QGIS 3.x / Qt5
from qgis.PyQt.QtGui import QIcon, QIntValidator, QColor
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

# Fylker (Kommuneinfo)
ADMIN_API_BASE = "https://api.kartverket.no/kommuneinfo/v1"
ADMIN_API_BASE_FALLBACK = "https://ws.geonorge.no/kommuneinfo/v1"  # proxy

COUNTY_API_LIST_PRIMARY = f"{ADMIN_API_BASE}/fylker"
COUNTY_API_LIST_FALLBACK = f"{ADMIN_API_BASE_FALLBACK}/fylker"

COUNTY_API_OMRADE_PRIMARY = f"{ADMIN_API_BASE}/fylker/{{fylkesnummer}}/omrade"
COUNTY_API_OMRADE_FALLBACK = f"{ADMIN_API_BASE_FALLBACK}/fylker/{{fylkesnummer}}/omrade"

# Kommuner (Kommuneinfo)
MUNICI_API_LIST_PRIMARY = f"{ADMIN_API_BASE}/kommuner"
MUNICI_API_LIST_FALLBACK = f"{ADMIN_API_BASE_FALLBACK}/kommuner"

MUNICI_API_OMRADE_PRIMARY = f"{ADMIN_API_BASE}/kommuner/{{kommunenummer}}/omrade"
MUNICI_API_OMRADE_FALLBACK = f"{ADMIN_API_BASE_FALLBACK}/kommuner/{{kommunenummer}}/omrade"

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
LAYER_COUNTY = "søkte_fylker"
LAYER_MUNICI = "søkte_kommuner"

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
    kind: str
    label: str

    epsg: Optional[int]
    raw: Dict[str, Any]

    point: Optional[HitPoint]
    geom: Optional[QgsGeometry]
    geom_epsg: Optional[int]

    objtype: str
    kommunenavn: str
    kommunenummer: str
    postnummer: str
    poststed: str
    eiendom_ref: str

    gnr: Optional[int]
    bnr: Optional[int]
    fnr: Optional[int]
    snr: Optional[int]
    teig_id: Optional[int]
    objekttype_eiendom: str

    fylkesnavn: str = ""
    fylkesnummer: str = ""

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

        self.action = QAction(icon, "Søk i Norges offisielle APIer", self.iface.mainWindow())
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
    def _qgs_field_type(self, variant_type):
        """
        Returnerer riktig type-argument til QgsField på tvers av QGIS 3.40 (QVariant)
        og QGIS 4 (QMetaType).
        """
        if QMetaType is None:
            return variant_type  # QGIS 3.40/Qt5

        mapping = {
            QVariant.String: QMetaType.Type.QString,
            QVariant.Int: QMetaType.Type.Int,
            QVariant.LongLong: QMetaType.Type.LongLong,
            QVariant.Double: QMetaType.Type.Double,
            QVariant.Bool: QMetaType.Type.Bool,
        }
        return mapping.get(variant_type, QMetaType.Type.QString)

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface

        self._hits: List[SearchHit] = []
        self._temp_point_marker: Optional[QgsVertexMarker] = None
        self._temp_poly_band: Optional[QgsRubberBand] = None

        self._build_ui()
        self._wire_signals()

        self._load_municipalities()
        self._load_counties()
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
        self.tab_county = QWidget(self)
        self.tab_munici = QWidget(self)
        self.tab_place = QWidget(self)

        self.tabs.addTab(self.tab_addr, "Adresse")
        self.tabs.addTab(self.tab_prop, "Eiendom")
        self.tabs.addTab(self.tab_county, "Fylke")
        self.tabs.addTab(self.tab_munici, "Kommune")
        self.tabs.addTab(self.tab_place, "Stedsnavn")

        self._build_tab_addr()
        self._build_tab_prop()
        self._build_tab_county()
        self._build_tab_munici()
        self._build_tab_place()

        # Resultater
        result_box = QGroupBox("Treff", self)
        result_layout = QVBoxLayout(result_box)
        result_layout.setContentsMargins(6, 6, 6, 6)

        self.tree = QTreeWidget(self)
        self.tree.setRootIsDecorated(False)   # fjerner plass til “tre-pil”/dekorasjon i første kolonne
        self.tree.setIndentation(0)           # fjerner innrykk helt
        self.tree.setItemsExpandable(False)   # hindrer at items blir “tre-noder”
        self.tree.setExpandsOnDoubleClick(False)

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

        self.cmb_kommune_prop = QComboBox(self)
        self.cmb_kommune_prop.setEditable(True)
        self.cmb_kommune_prop.setInsertPolicy(QComboBox.NoInsert)
        if self.cmb_kommune_prop.lineEdit():
            self.cmb_kommune_prop.lineEdit().setPlaceholderText("Velg / skriv kommunenavn…")

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
        grid.addWidget(self.cmb_kommune_prop, 0, 1, 1, 3)

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

    def _build_tab_county(self):
        layout = QVBoxLayout(self.tab_county)
        layout.setContentsMargins(6, 6, 6, 6)

        box = QGroupBox("Søk fylker", self.tab_county)
        grid = QGridLayout(box)

        self.cmb_fylke = QComboBox(self)
        self.cmb_fylke.setEditable(True)
        self.cmb_fylke.setInsertPolicy(QComboBox.NoInsert)
        if self.cmb_fylke.lineEdit():
            self.cmb_fylke.lineEdit().setPlaceholderText("Velg / skriv fylkesnavn…")

        def mk_spin():
            s = QSpinBox(self)
            s.setRange(0, 999999)
            s.setSpecialValueText("")
            s.setValue(0)
            return s

        grid.addWidget(QLabel("Fylke:"), 0, 0)
        grid.addWidget(self.cmb_fylke, 0, 1, 1, 3)

        row = QHBoxLayout()
        self.btn_search_county = QPushButton("Søk", self)
        self.btn_clear_county = QPushButton("Tøm", self)
        row.addStretch(1)
        row.addWidget(self.btn_search_county)
        row.addWidget(self.btn_clear_county)

        layout.addWidget(box)
        layout.addLayout(row)    

    def _build_tab_munici(self):
        layout = QVBoxLayout(self.tab_munici)
        layout.setContentsMargins(6, 6, 6, 6)

        box = QGroupBox("Søk kommuner", self.tab_munici)
        grid = QGridLayout(box)

        self.cmb_kommune_munici = QComboBox(self)
        self.cmb_kommune_munici.setEditable(True)
        self.cmb_kommune_munici.setInsertPolicy(QComboBox.NoInsert)
        if self.cmb_kommune_munici.lineEdit():
            self.cmb_kommune_munici.lineEdit().setPlaceholderText("Velg / skriv kommunenavn…")

        def mk_spin():
            s = QSpinBox(self)
            s.setRange(0, 999999)
            s.setSpecialValueText("")
            s.setValue(0)
            return s

        grid.addWidget(QLabel("Kommune:"), 0, 0)
        grid.addWidget(self.cmb_kommune_munici, 0, 1, 1, 3)

        row = QHBoxLayout()
        self.btn_search_munici = QPushButton("Søk", self)
        self.btn_clear_munici = QPushButton("Tøm", self)
        row.addStretch(1)
        row.addWidget(self.btn_search_munici)
        row.addWidget(self.btn_clear_munici)

        layout.addWidget(box)
        layout.addLayout(row)        

    def _geometry_dict_to_qgsgeometry(self, geom_dict: Dict[str, Any]) -> Optional[QgsGeometry]:
        """
        Tar en GeoJSON Geometry dict (type/coordinates) og lager QgsGeometry.
        Støtter Polygon/MultiPolygon (kan utvides senere).
        """
        if not isinstance(geom_dict, dict):
            return None

        gtype = geom_dict.get("type")
        coords = geom_dict.get("coordinates")

        if not isinstance(gtype, str) or coords is None:
            return None

        # Enkel normalisering: forvent coords som list
        if not isinstance(coords, list):
            return None

        return self._geojson_to_qgsgeometry({"type": gtype, "coordinates": coords})

    def _extract_geom_and_epsg_from_county_payload(self, data: Any, fallback_epsg: int) -> Tuple[Optional[QgsGeometry], int]:
        """
        Returnerer (geom, epsg) fra enten FeatureCollection eller {omrade:{...}}-formatet.
        """
        if not isinstance(data, dict):
            return None, fallback_epsg

        # 1) Hvis det er FeatureCollection
        if isinstance(data.get("features"), list):
            src_epsg = self._parse_epsg_from_crs(data, fallback_epsg)
            for f in data["features"]:
                if not isinstance(f, dict):
                    continue
                gdict = f.get("geometry")
                if isinstance(gdict, dict):
                    geom = self._geometry_dict_to_qgsgeometry(gdict)
                    if geom and not geom.isEmpty():
                        return geom, src_epsg
            return None, src_epsg

        # 2) Hvis det er { omrade: {type, coordinates, crs?} }
        omrade = data.get("omrade")
        if isinstance(omrade, dict):
            src_epsg = self._parse_epsg_from_crs(omrade, fallback_epsg)  # omrade kan ha crs
            geom = self._geometry_dict_to_qgsgeometry(omrade)
            if geom and not geom.isEmpty():
                return geom, src_epsg
            return None, src_epsg

        return None, fallback_epsg
    
    def _extract_geom_and_epsg_from_munici_payload(self, data: Any, fallback_epsg: int) -> Tuple[Optional[QgsGeometry], int]:
        """
        Returnerer (geom, epsg) fra enten FeatureCollection eller {omrade:{...}}-formatet.
        """
        if not isinstance(data, dict):
            return None, fallback_epsg

        # 1) Hvis det er FeatureCollection
        if isinstance(data.get("features"), list):
            src_epsg = self._parse_epsg_from_crs(data, fallback_epsg)
            for f in data["features"]:
                if not isinstance(f, dict):
                    continue
                gdict = f.get("geometry")
                if isinstance(gdict, dict):
                    geom = self._geometry_dict_to_qgsgeometry(gdict)
                    if geom and not geom.isEmpty():
                        return geom, src_epsg
            return None, src_epsg

        # 2) Hvis det er { omrade: {type, coordinates, crs?} }
        omrade = data.get("omrade")
        if isinstance(omrade, dict):
            src_epsg = self._parse_epsg_from_crs(omrade, fallback_epsg)  # omrade kan ha crs
            geom = self._geometry_dict_to_qgsgeometry(omrade)
            if geom and not geom.isEmpty():
                return geom, src_epsg
            return None, src_epsg

        return None, fallback_epsg

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
        if self.cmb_kommune_prop.lineEdit():
            self.cmb_kommune_prop.lineEdit().returnPressed.connect(self.on_search_prop)

        # Fylker
        self.btn_search_county.clicked.connect(self.on_search_county)
        self.btn_clear_county.clicked.connect(self.on_clear_county_fields)
        if self.cmb_fylke.lineEdit():
            self.cmb_fylke.lineEdit().returnPressed.connect(self.on_search_county)    

        # Kommuner
        self.btn_search_munici.clicked.connect(self.on_search_munici)
        self.btn_clear_munici.clicked.connect(self.on_clear_munici_fields)
        if self.cmb_kommune_munici.lineEdit():
            self.cmb_kommune_munici.lineEdit().returnPressed.connect(self.on_search_munici)

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
        elif idx == 2:
            self._set_mode_headers("fylke") 
        elif idx == 3:
            self._set_mode_headers("kommune")    
        else:
            self._set_mode_headers("stedsnavn")

    def _set_mode_headers(self, mode: str):
        self.tree.clear()
        self._hits = []
        self._clear_temp_highlights()

        if mode == "adresse":
            self.tree.setColumnCount(6)
            self.tree.setHeaderLabels(["Adresse", "Objtype", "Kommune", "Gnr/Bnr", "Postnr", "Poststed"])
        elif mode == "eiendom":
            self.tree.setColumnCount(8)
            self.tree.setHeaderLabels(["Eiendom", "Objekt", "Gnr", "Bnr", "Fnr", "Snr", "TeigID", "Geom"])
        elif mode == "fylke":
            self.tree.setColumnCount(2)
            self.tree.setHeaderLabels(["Navn", "Nummer"])
        elif mode == "kommune":
            self.tree.setColumnCount(2)
            self.tree.setHeaderLabels(["Navn", "Nummer"])    
        else:
            self.tree.setColumnCount(3)
            self.tree.setHeaderLabels(["Navn", "Type", "Kommune"])

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
            self.btn_search_county,
            self.btn_clear_county,
            self.btn_search_munici,
            self.btn_clear_munici,
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
        # fyll begge comboboxer hvis de finnes
        combos = []
        if hasattr(self, "cmb_kommune_prop"):
            combos.append(self.cmb_kommune_prop)
        if hasattr(self, "cmb_kommune_munici"):
            combos.append(self.cmb_kommune_munici)

        for cmb in combos:
            cmb.clear()
            cmb.addItem("", None)

        endpoints = [KOMMUNEINFO_PRIMARY, KOMMUNEINFO_FALLBACK]
        for ep in endpoints:
            try:
                data = self._api_get_json(ep, {})
                kommuner = self._parse_kommuner_payload(data)
                if kommuner:
                    kommuner.sort(key=lambda x: x[1].lower())
                    for kommunenummer, kommunenavn in kommuner:
                        for cmb in combos:
                            cmb.addItem(f"{kommunenavn} ({kommunenummer})", kommunenummer)
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

    def _selected_kommune_from(self, cmb: QComboBox) -> Tuple[Optional[str], Optional[str]]:
        txt = (cmb.currentText() or "").strip()
        nr = cmb.currentData()

        if isinstance(nr, str) and nr.strip():
            navn = txt
            if "(" in navn and navn.endswith(")"):
                navn = navn.rsplit("(", 1)[0].strip()
            return nr.strip(), (navn or None)

        if len(txt) >= 4 and txt[:4].isdigit():
            return txt[:4], (txt[4:].strip() or None)

        return None, (txt or None)

    
    # ---------- Fylke-liste ----------
    def _load_counties(self):
        self.cmb_fylke.clear()
        self.cmb_fylke.addItem("", None)

        endpoints = [COUNTY_API_LIST_PRIMARY, COUNTY_API_LIST_FALLBACK]
        for ep in endpoints:
            try:
                data = self._api_get_json(ep, {})
                fylker = self._parse_fylker_payload(data)
                if fylker:
                    fylker.sort(key=lambda x: x[1].lower())
                    for fylkesnummer, fylkesnavn in fylker:
                        self.cmb_fylke.addItem(f"{fylkesnavn} ({fylkesnummer})", fylkesnummer)
                    log(f"Lastet {len(fylker)} fylker fra {ep}", Qgis.Info)
                    return
            except Exception as e:
                log(f"Klarte ikke laste fylker fra {ep}: {e}", Qgis.Warning)

        self.lbl_status.setText("Obs: Klarte ikke laste fylkesliste.")


    def _parse_fylker_payload(self, data: Any) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("fylker") or data.get("data") or data.get("content") or []
        else:
            items = []

        if not isinstance(items, list):
            return out

        for it in items:
            if not isinstance(it, dict):
                continue
            nr = it.get("fylkesnummer") or it.get("nummer") or it.get("kode")
            navn = it.get("fylkesnavn") or it.get("navn") or it.get("name")
            if nr and navn:
                out.append((str(nr).strip(), str(navn).strip()))
        return out

    def _selected_fylke(self) -> Tuple[Optional[str], Optional[str]]:
        txt = (self.cmb_fylke.currentText() or "").strip()
        nr = self.cmb_fylke.currentData()

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

        def parse_float(v) -> Optional[float]:
            if v is None:
                return None
            try:
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    s = v.strip().replace(",", ".")
                    if not s:
                        return None
                    return float(s)
            except Exception:
                return None
            return None

        # EPSG parsing (tåler "EPSG:25833" osv)
        epsg_raw = rp.get("epsg")
        epsg_i = 4258
        try:
            if epsg_raw is not None:
                s = str(epsg_raw).upper().replace("EPSG:", "").strip()
                m = re.search(r"(\d+)", s)
                if m:
                    epsg_i = int(m.group(1))
        except Exception:
            epsg_i = 4258

        # Kandidat 1: x/y eller øst/nord (meter)
        x_raw = rp.get("x")
        y_raw = rp.get("y")
        ost_raw = rp.get("ost") or rp.get("øst")
        nord_raw = rp.get("nord")

        x_xy = parse_float(x_raw)
        y_xy = parse_float(y_raw)
        if x_xy is None or y_xy is None:
            x_xy = parse_float(ost_raw)
            y_xy = parse_float(nord_raw)

        # Kandidat 2: lon/lat (grader)
        lon = parse_float(rp.get("lon"))
        lat = parse_float(rp.get("lat"))

        # Velg felt:
        # - Hvis vi har x/y (eller øst/nord) og de ser ut som meter, bruk dem.
        # - Ellers bruk lon/lat.
        chosen_x = None
        chosen_y = None

        def looks_like_degrees(x: float, y: float) -> bool:
            return abs(x) <= 180 and abs(y) <= 90

        def looks_like_utm(x: float, y: float) -> bool:
            return (0 <= x <= 1_000_000) and (5_000_000 <= y <= 8_000_000)

        if x_xy is not None and y_xy is not None and looks_like_utm(x_xy, y_xy):
            chosen_x, chosen_y = x_xy, y_xy
        elif lon is not None and lat is not None:
            chosen_x, chosen_y = lon, lat
        elif x_xy is not None and y_xy is not None:
            # fallback: bruk x/y selv om de ikke “ser ut” som UTM
            chosen_x, chosen_y = x_xy, y_xy
        else:
            return None

        # Sanity check mellom EPSG og tallområde
        if chosen_x is None or chosen_y is None:
            return None

        is_deg = looks_like_degrees(chosen_x, chosen_y)
        is_utm = looks_like_utm(chosen_x, chosen_y)

        # Hvis EPSG sier grader, men tallene ser ut som meter → bruk prosjektets EPSG (ofte 25833)
        if epsg_i in (4258, 4326) and (not is_deg) and is_utm:
            prj_epsg = self._project_epsg()
            epsg_i = prj_epsg if prj_epsg > 0 else 25833

        # Hvis EPSG sier UTM, men tallene ser ut som grader → bruk 4258
        if epsg_i in (25832, 25833, 25834) and is_deg:
            epsg_i = 4258

        return HitPoint(x=float(chosen_x), y=float(chosen_y), epsg=int(epsg_i))


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

        ntype = (obj.get("navneobjekttype") or obj.get("navnetype") or obj.get("type") or "").strip()

        # Kommune: ligger i liste "kommuner"
        kommunenavn = ""
        kommuner = obj.get("kommuner")
        if isinstance(kommuner, list) and kommuner:
            names = []
            for k in kommuner:
                if isinstance(k, dict):
                    kn = (k.get("kommunenavn") or "").strip()
                    if kn:
                        names.append(kn)
            kommunenavn = ", ".join(dict.fromkeys(names))  # unik + behold rekkefølge

        rp = obj.get("representasjonspunkt") or obj.get("punkt") or {}

        # Hent epsg fra flere mulige felt
        epsg_raw = None
        if isinstance(rp, dict):
            epsg_raw = rp.get("epsg") or rp.get("koordinatsystem") or rp.get("srid")
            x_raw = rp.get("x") or rp.get("ost") or rp.get("øst") or rp.get("lon")
            y_raw = rp.get("y") or rp.get("nord") or rp.get("lat")
        else:
            x_raw = y_raw = None

        # fallback om epsg ligger på toppnivå
        if epsg_raw is None:
            epsg_raw = obj.get("epsg") or obj.get("koordinatsystem") or obj.get("srid")

        def parse_float(v) -> Optional[float]:
            if v is None:
                return None
            try:
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    s = v.strip().replace(",", ".")
                    if not s:
                        return None
                    return float(s)
            except Exception:
                return None
            return None

        xf = parse_float(x_raw)
        yf = parse_float(y_raw)
        if xf is None or yf is None:
            return None

        # epsg parsing (tåler "EPSG:25833" osv)
        epsg_i = 4258
        try:
            if epsg_raw is not None:
                s = str(epsg_raw).upper().replace("EPSG:", "").strip()
                # plukk ut første tallsekvens
                m = re.search(r"(\d+)", s)
                if m:
                    epsg_i = int(m.group(1))
        except Exception:
            epsg_i = 4258

        # --- Sanity check uten å ødelegge trefflisten ---
        # Hvis EPSG sier grader, men tallene ser ut som meter (UTM)
        looks_like_degrees = (abs(xf) <= 180 and abs(yf) <= 90)
        looks_like_utm = (0 <= xf <= 1_000_000 and 5_000_000 <= yf <= 8_000_000)

        if epsg_i in (4258, 4326) and (not looks_like_degrees) and looks_like_utm:
            epsg_i = 25833

        # motsatt: EPSG sier UTM, men tallene ser ut som grader
        if epsg_i in (25832, 25833, 25834) and looks_like_degrees:
            epsg_i = 4258

        pt = HitPoint(x=xf, y=yf, epsg=epsg_i)

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

                if h.point:                   
                    item = QTreeWidgetItem([
                    h.label,
                    h.objtype,
                    h.kommunenavn or h.kommunenummer,
                    h.eiendom_ref or "",
                    h.postnummer or "",
                    h.poststed or "",
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
                    h.objekttype_eiendom or "",
                ])  

            elif h.kind == "fylke":
                item = QTreeWidgetItem([
                    h.fylkesnavn or h.label,
                    h.fylkesnummer or "",
                ])

            elif h.kind == "kommune":
                item = QTreeWidgetItem([
                    h.kommunenavn or h.label,
                    h.kommunenummer or "",
                ])

            else:  # stedsnavn
                item = QTreeWidgetItem([
                    h.label,
                    h.objtype or "",
                    h.kommunenavn or "",
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

        if hit.kind in ("eiendom", "fylke", "kommune") and hit.geom and hit.geom_epsg:
            try:
                g = self._transform_geometry(hit.geom, hit.geom_epsg, dest_crs)
            except Exception:
                return
            rb = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
            rb.setToGeometry(g, None)

            # Rød preview (kant + fyll) med gjennomsiktighet
            rb.setColor(QColor(255, 0, 0, 200))       # kantlinje (mer synlig)
            rb.setFillColor(QColor(255, 0, 0, 60))    # fyll (mer transparent)
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

        if hit.kind in ("eiendom", "fylke", "kommune") and hit.geom and hit.geom_epsg:
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
            QgsField("label", self._qgs_field_type(QVariant.String)),
            QgsField("objtype", self._qgs_field_type(QVariant.String)),
            QgsField("kommunenavn", self._qgs_field_type(QVariant.String)),
            QgsField("kommunenr", self._qgs_field_type(QVariant.String)),
            QgsField("eiendom", self._qgs_field_type(QVariant.String)),
            QgsField("postnr", self._qgs_field_type(QVariant.String)),
            QgsField("poststed", self._qgs_field_type(QVariant.String)),
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
            QgsField("eiendom", self._qgs_field_type(QVariant.String)),
            QgsField("objekt", self._qgs_field_type(QVariant.String)),      # Eiendom/Festetomt/Seksjon
            QgsField("gnr", self._qgs_field_type(QVariant.Int)),
            QgsField("bnr", self._qgs_field_type(QVariant.Int)),
            QgsField("fnr", self._qgs_field_type(QVariant.Int)),
            QgsField("snr", self._qgs_field_type(QVariant.Int)),
            QgsField("teig_id", self._qgs_field_type(QVariant.LongLong)),
            QgsField("objekttype", self._qgs_field_type(QVariant.String)),  # Teig
            QgsField("kommunenr", self._qgs_field_type(QVariant.String)),
        ])
        layer.updateFields()
        prj.addMapLayer(layer)
        return layer
    
    def _get_or_create_county_layer(self) -> QgsVectorLayer:
        prj = QgsProject.instance()
        for lyr in prj.mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.name() == LAYER_COUNTY and lyr.providerType() == "memory":
                return lyr

        epsg = self._project_epsg()
        # MultiPolygon for å støtte både Polygon og MultiPolygon
        layer = QgsVectorLayer(f"MultiPolygon?crs=EPSG:{epsg}", LAYER_COUNTY, "memory")
        prov = layer.dataProvider()
        prov.addAttributes([
            QgsField("fylkesnavn", self._qgs_field_type(QVariant.String)),
            QgsField("fylkesnr", self._qgs_field_type(QVariant.String)),
        ])
        layer.updateFields()
        prj.addMapLayer(layer)
        return layer
    
    def _get_or_create_munici_layer(self) -> QgsVectorLayer:
        prj = QgsProject.instance()
        for lyr in prj.mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.name() == LAYER_MUNICI and lyr.providerType() == "memory":
                return lyr

        epsg = self._project_epsg()
        # MultiPolygon for å støtte både Polygon og MultiPolygon
        layer = QgsVectorLayer(f"MultiPolygon?crs=EPSG:{epsg}", LAYER_MUNICI, "memory")
        prov = layer.dataProvider()
        prov.addAttributes([
            QgsField("kommunenavn", self._qgs_field_type(QVariant.String)),
            QgsField("kommunenr", self._qgs_field_type(QVariant.String)),
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
            QgsField("navn", self._qgs_field_type(QVariant.String)),
            QgsField("type", self._qgs_field_type(QVariant.String)),
            QgsField("kommune", self._qgs_field_type(QVariant.String)),
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
        
        if hit.kind == "fylke":
            if not hit.geom or not hit.geom_epsg:
                QMessageBox.information(self, "Søk", "Fylke-treff mangler flate.")
                return

            layer = self._get_or_create_county_layer()
            try:
                g = self._transform_geometry(hit.geom, hit.geom_epsg, layer.crs())
            except Exception as e:
                QMessageBox.warning(self, "Søk", f"Kunne ikke transformere flate:\n{e}")
                return

            # Hvis geometrien er Polygon, men laget er MultiPolygon, gjør om
            try:
                if g.wkbType() in (QgsWkbTypes.Polygon, QgsWkbTypes.Polygon25D):
                    g = QgsGeometry.fromMultiPolygonXY([g.asPolygon()])
            except Exception:
                # Hvis konvertering feiler, prøv å bruke geometrien som den er
                pass

            feat = QgsFeature(layer.fields())
            feat.setGeometry(g)
            feat["fylkesnavn"] = hit.fylkesnavn or hit.label
            feat["fylkesnr"] = hit.fylkesnummer or ""

            layer.startEditing()
            ok = layer.addFeature(feat)
            layer.commitChanges()
            layer.triggerRepaint()

            if not ok:
                QMessageBox.warning(self, "Søk", "Kunne ikke legge til feature i søkte_fylker.")
            return
        
        if hit.kind == "kommune":
            if not hit.geom or not hit.geom_epsg:
                QMessageBox.information(self, "Søk", "Kommune-treff mangler flate.")
                return

            layer = self._get_or_create_munici_layer()
            try:
                g = self._transform_geometry(hit.geom, hit.geom_epsg, layer.crs())
            except Exception as e:
                QMessageBox.warning(self, "Søk", f"Kunne ikke transformere flate:\n{e}")
                return

            # Hvis geometrien er Polygon, men laget er MultiPolygon, gjør om
            try:
                if g.wkbType() in (QgsWkbTypes.Polygon, QgsWkbTypes.Polygon25D):
                    g = QgsGeometry.fromMultiPolygonXY([g.asPolygon()])
            except Exception:
                # Hvis konvertering feiler, prøv å bruke geometrien som den er
                pass

            feat = QgsFeature(layer.fields())
            feat.setGeometry(g)
            feat["kommunenavn"] = hit.kommunenavn or hit.label
            feat["kommunenr"] = hit.kommunenummer or ""

            layer.startEditing()
            ok = layer.addFeature(feat)
            layer.commitChanges()
            layer.triggerRepaint()

            if not ok:
                QMessageBox.warning(self, "Søk", "Kunne ikke legge til feature i søkte_kommuner.")
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
        if not current:
            return
        try:
            idx = int(current.data(0, Qt.UserRole))
            hit = self._hits[idx]
        except Exception:
            return
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

    def on_clear_county_fields(self):
        self.cmb_fylke.setCurrentIndex(0)
        if self.cmb_fylke.lineEdit():
            self.cmb_fylke.lineEdit().setText("")
            self.cmb_fylke.lineEdit().setFocus()

    def on_clear_munici_fields(self):
        self.cmb_kommune.setCurrentIndex(0)
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
        kommunenummer, kommunenavn = self._selected_kommune_from(self.cmb_kommune_prop)
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

            hits, src_epsg = self._parse_property_featurecollection(data, fallback_epsg=out_epsg)
            log(f"Eiendom parse: total={len(hits)}, src_epsg={src_epsg}", Qgis.Info)

            # --- NYTT: tell features og features uten geometri ---
            feat_total = 0
            feat_wo_geom = 0
            if isinstance(data, dict):
                feats = data.get("features") or []
                if isinstance(feats, list):
                    feat_total = len(feats)
                    for f in feats:
                        if not isinstance(f, dict):
                            feat_wo_geom += 1
                            continue
                        g = f.get("geometry")
                        # mangler geometry helt, eller har tomme coords
                        if not isinstance(g, dict):
                            feat_wo_geom += 1
                            continue
                        coords = g.get("coordinates")
                        gtype = g.get("type")
                        if not gtype or coords in (None, [], ["string"]):
                            feat_wo_geom += 1

            self._set_mode_headers("eiendom")
            self._render_hits(hits)

            if not hits:
                if feat_total > 0 and feat_wo_geom == feat_total:
                    QMessageBox.information(
                        self,
                        "Søk",
                        "Fant eiendom (matrikkel), men responsen mangler geometri.\n"
                        "Dette tyder på nedetid/degradert respons i eiendomstjenesten hos Kartverket akkurat nå. Sjekk https://status.kartverket.no for mer informasjon!"
                    )
                else:
                    QMessageBox.information(self, "Søk", "Ingen eiendomstreff.")


        except Exception as e:
            log(f"Eiendom-søk feilet: {e}", Qgis.Critical)
            QMessageBox.warning(self, "Søk", f"Eiendom-søk feilet:\n{e}")
        finally:
            self._set_busy(False, "Klar.")

    def on_search_county(self):
        fylkesnummer, fylkesnavn = self._selected_fylke()
        if not fylkesnummer:
            QMessageBox.information(self, "Søk", "Velg fylke (fylkesnummer).")
            return

        out_epsg = self._project_epsg()
        url_candidates = [
            COUNTY_API_OMRADE_PRIMARY.format(fylkesnummer=fylkesnummer),
            COUNTY_API_OMRADE_FALLBACK.format(fylkesnummer=fylkesnummer),
        ]

        self._set_busy(True, "Henter fylkesgrense…")
        try:
            last_err = None
            data = None
            for url in url_candidates:
                try:
                    data = self._api_get_json(url, {"utkoordsys": out_epsg})
                    break
                except Exception as e:
                    last_err = e
                    continue
            if data is None:
                raise RuntimeError(last_err or "Ukjent feil")

            # Forvent FeatureCollection-ish
            hits: List[SearchHit] = []
            src_epsg = self._parse_epsg_from_crs(data, out_epsg)

            geom, src_epsg = self._extract_geom_and_epsg_from_county_payload(data, fallback_epsg=out_epsg)

            hits: List[SearchHit] = []
            if geom and not geom.isEmpty():
                hits.append(SearchHit(
                    kind="fylke",
                    label=fylkesnavn or f"Fylke {fylkesnummer}",
                    epsg=src_epsg,
                    raw=data,
                    point=None,
                    geom=geom,
                    geom_epsg=src_epsg,
                    objtype="Fylke",
                    kommunenavn="",
                    kommunenummer="",
                    postnummer="",
                    poststed="",
                    eiendom_ref="",
                    gnr=None, bnr=None, fnr=None, snr=None, teig_id=None,
                    objekttype_eiendom="",
                    fylkesnavn=fylkesnavn or "",
                    fylkesnummer=fylkesnummer or "",
                ))

            self._set_mode_headers("fylke")
            self._render_hits(hits)

            if not hits:
                QMessageBox.information(self, "Søk", "Fant ingen fylkesgeometri i responsen.")

        except Exception as e:
            log(f"Fylkesøk feilet: {e}", Qgis.Warning)
            QMessageBox.warning(self, "Søk", f"Fylkesøk feilet:\n{e}")
        finally:
            self._set_busy(False, "Klar.")

    def on_search_munici(self):
        kommunenummer, kommunenavn = self._selected_kommune_from(self.cmb_kommune_munici)
        if not kommunenummer:
            QMessageBox.information(self, "Søk", "Velg kommune (kommunenummer).")
            return

        out_epsg = self._project_epsg()
        url_candidates = [
            MUNICI_API_OMRADE_PRIMARY.format(kommunenummer=kommunenummer),
            MUNICI_API_OMRADE_FALLBACK.format(kommunenummer=kommunenummer),
        ]

        self._set_busy(True, "Henter kommunegrense…")
        try:
            last_err = None
            data = None
            for url in url_candidates:
                try:
                    data = self._api_get_json(url, {"utkoordsys": out_epsg})
                    break
                except Exception as e:
                    last_err = e
                    continue
            if data is None:
                raise RuntimeError(last_err or "Ukjent feil")

            # Forvent FeatureCollection-ish
            hits: List[SearchHit] = []
            src_epsg = self._parse_epsg_from_crs(data, out_epsg)

            geom, src_epsg = self._extract_geom_and_epsg_from_munici_payload(data, fallback_epsg=out_epsg)

            hits: List[SearchHit] = []
            if geom and not geom.isEmpty():
                hits.append(SearchHit(
                    kind="kommune",
                    label=kommunenavn or f"Kommune {kommunenummer}",
                    epsg=src_epsg,
                    raw=data,
                    point=None,
                    geom=geom,
                    geom_epsg=src_epsg,
                    objtype="Kommune",
                    kommunenavn=kommunenavn or "",
                    kommunenummer=kommunenummer or "",
                    postnummer="",
                    poststed="",
                    eiendom_ref="",
                    gnr=None, bnr=None, fnr=None, snr=None, teig_id=None,
                    objekttype_eiendom="",
                    fylkesnavn="",
                    fylkesnummer="",
                ))

            self._set_mode_headers("kommune")
            self._render_hits(hits)

            if not hits:
                QMessageBox.information(self, "Søk", "Fant ingen kommunegeometri i responsen.")

        except Exception as e:
            log(f"Kommunesøk feilet: {e}", Qgis.Warning)
            QMessageBox.warning(self, "Søk", f"Kommunesøk feilet:\n{e}")
        finally:
            self._set_busy(False, "Klar.")

    def on_search_place(self):
        s = self.inp_place.text().strip()
        if len(s) < 2:
            QMessageBox.information(self, "Søk", "Skriv minst 2 tegn.")
            return

        # OBS: Stedsnavn-API kan være streng på parametre, hold det enkelt.
        params = {
            "sok": s,
            "treffPerSide": 200,  # ok (<=500)
            "side": 1,            # <-- fiks for 422
            "utkoordsys": self._project_epsg(),  # få punkt i samme EPSG som prosjekt (hvis mulig)
        }

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

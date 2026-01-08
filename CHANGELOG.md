# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)  
Versioning: [SemVer](https://semver.org/)

## [1.0.0] - 2026-01-08

### Added

- Dockable search panel for QGIS.
- Address search (street name + house number + letter, or street name only) using Kartverket Address API.
- Property search by municipality + gnr/bnr (+ optional fnr/snr) using Kartverket Property API (polygon results where available).
- Place name search using Kartverket Place Name API.
- Temporary map preview/highlight of selected results (marker/rubberband) and **Zoom to selected**.
- **Add to layer** functionality with automatically created memory layers:
  - `søkte_adresser` (Point)
  - `søkte_eiendommer` (Polygon)
  - `søkte_stedsnavn` (Point)

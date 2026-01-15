# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)  
Versioning: [SemVer](https://semver.org/)

## [1.1.0] - 2026-01-15

### Added

- Search tab for counties (administrative boundaries) using Kartverket Kommuneinfo API.
- Search tab for municipalities (administrative boundaries) using Kartverket Kommuneinfo API.
- New memory layers for administrative boundaries:
  - `søkte_fylker` (Polygon/MultiPolygon)
  - `søkte_kommuner` (Polygon/MultiPolygon)
- Support for multiple response formats for county/municipality geometry (FeatureCollection and `{ omrade: { type, coordinates, crs } }` payloads).
- Compatibility helper for field type definitions across QGIS 3.40 (QVariant) and QGIS 4 (QMetaType).

### Changed

- Improved property search robustness:
  - Better handling when the Eiendom API returns matrikkel hits but missing/empty geometry (degraded service / downtime).
  - Clearer user feedback when the service response lacks geometry, to distinguish “no hits” vs “service issue”.
- Updated preview/selection handling in the results list to use the current item directly, preventing mismatched previews when clicking between hits.
- Internal CRS/geometry handling updated to ensure consistent transformation and rendering for new administrative boundary results.

### Fixed

- Fixed incorrect/misaligned preview marker updates when selecting addresses in the result list (current/selection mismatch).
- Fixed cases where county/municipality geometries were not detected because geometry was provided under `omrade` instead of `geometry`.

## [1.0.1] - 2026-01-14

### Fixed

- Fixed the problem of response delivering 'side=0' to 'side=1' for searches in Stedsnavn REST-API.

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

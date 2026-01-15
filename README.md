# Søk (QGIS Plugin)

A dockable search tool for QGIS that lets you search for **addresses**, **properties**, **counties**, **municipalities** and **place names** using Kartverket’s public APIs — and work with the results directly in the map.

## Features

### Address

- Search for road addresses (street name + optional house number + letter, or street name only).
- Results are shown in a list with relevant columns (municipality, postal place, etc.).
- Temporary map marker when selecting an item in the results list.
- **Zoom to selected** and **Add to memory layer**.

### Property

- Search by municipality + gnr/bnr (+ optional fnr/snr).
- Results are returned as polygons when the service provides area geometry.
- Temporary highlight (rubberband) when selecting an item in the results list.
- **Zoom to selected** and **Add to memory layer**.
- Detects degraded responses (e.g. hits without geometry) and informs the user.

### Counties

- Search or select a county.
- Results are returned as polygons when the service provides area geometry.
- Temporary highlight (rubberband) when selecting an item in the results list.
- **Zoom to selected** and **Add to memory layer**.

### Municipalities

- Search or select a municipality.
- Results are returned as polygons when the service provides area geometry.
- Temporary highlight (rubberband) when selecting an item in the results list.
- **Zoom to selected** and **Add to memory layer**.

### Place names

- Search for place names (Kartverket place name API).
- Results are shown in a list (including municipality when available in the response).
- Temporary marker and zoom/add-to-layer functionality.

## Memory layers

The plugin can automatically create and populate these memory layers:

- `søkte_adresser` (Point)
- `søkte_eiendommer` (Polygon/MultiPolygon)
- `søkte_fylker` (Polygon/MultiPolygon)
- `søkte_kommuner` (Polygon/MultiPolygon)
- `søkte_stedsnavn` (Point)

## Usage

1. Open the plugin from the **Kartverket** menu or the **Kartverket** toolbar.
2. Select a tab:
   - **Address** / **Property** / **County** / **Municipality** / **Place names**
3. Search and select a result in the list to preview it in the map.
4. Use:
   - **Zoom to selected**
   - **Add to layer**
   - **Clear results**

## Coordinate Reference System (CRS)

- The plugin attempts to request results in the project CRS when supported by the service.
- If results are returned in a different CRS, points/geometries are transformed to the project CRS for preview/zoom and when writing to memory layers.

## Data sources (APIs)

- Address: Kartverket Address API (`/adresser/v1`) via GeoNorge proxy
- Property: Kartverket Property API (`/eiendom/v1`)
- Counties: Kartverket Kommuneinfo API (`/kommuneinfo/v1`)
- Municipalities: Kartverket Kommuneinfo API (`/kommuneinfo/v1`)
- Place names: Kartverket Place Name API (`/stedsnavn/v1`)

## Notes

- Some services may occasionally return partial or degraded responses (for example: property hits without geometry). The plugin will notify you when this happens.
- For service status, see Kartverket’s status page.

## Requirements

- QGIS >= 3.22
- Compatible with QGIS 4 / Qt6

## Contributing / Issues

Please open an issue in the repository if you find a bug or want to suggest improvements.

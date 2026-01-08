# Søk (QGIS Plugin)

A dockable search tool for QGIS that lets you search for **addresses**, **properties**, and **place names** using Kartverket’s public APIs — and work with the results directly in the map.

## Features

### Address

- Search for road addresses (street name + house number + letter, or street name only).
- Results are shown in a list with relevant columns (municipality, postal place, coordinates/EPSG).
- Temporary map marker when selecting an item in the results list.
- **Zoom to selected** and **Add to memory layer**.

### Property

- Search by municipality + gnr/bnr (+ optional fnr/snr).
- Results are returned as polygons where the service provides area geometry.
- Temporary highlight (rubberband) when selecting an item in the results list.
- **Zoom to selected** and **Add to memory layer**.

### Place names

- Search for place names (Kartverket place name API).
- Results are shown in a list (including municipality when available in the response).
- Temporary marker and zoom/add-to-layer functionality.

## Memory layers

The plugin can automatically create and populate these memory layers:

- `søkte_adresser` (Point)
- `søkte_eiendommer` (Polygon)
- `søkte_stedsnavn` (Point)

## Usage

1. Open the plugin from the **Kartverket** menu or the **Kartverket** toolbar.
2. Select a tab:
   - **Address** / **Property** / **Place names**
3. Search and select a result in the list.
4. Use:
   - **Zoom to selected**
   - **Add to layer**

## Coordinate Reference System (CRS)

- The plugin attempts to use the project CRS when supported by the service.
- If a result is returned in a different EPSG than the project, geometry/points are transformed to the project CRS for preview/zoom and when writing to memory layers.

## Data sources (APIs)

- Address: [Kartverket Address API](https://ws.geonorge.no/adresser/v1/)
- Property: [Kartverket Property API](https://ws.geonorge.no/eiendom/v1/)
- Place names: [Kartverket Place Name API](https://ws.geonorge.no/stedsnavn/v1/)

## Requirements

- QGIS >= 3.22
- Supports QGIS 4 / Qt6

## Contributing / Issues

Please open an issue in the repository if you find a bug or want to suggest improvements.

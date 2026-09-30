# Access tools: design notes

Design notes for the tools built on this catalog. Built as of 2026-09-30: the engine (`sccwrp_data/`), the ArcGIS Pro toolbox and the command line, tested with one dataset per access path; usage and how to add datasets are in [TOOLS.md](TOOLS.md). Still to do: `fetch` blocks for the rest of the catalog (after the S-drive audit decisions), the R wrapper, and optional Portal items.

## Principles

- **One catalog, many front ends.** The ArcGIS Pro toolbox, the Python and R helpers and the web page all read `catalog/datasets/*.json` and `catalog/clip_areas.json`. Nothing is duplicated per tool.
- **Fetch as little as possible.** Stream or read in place when the provider allows it (`access.mode` in each entry); download only what a user asks for, clipped to their area.
- **California first.** Most work needs California and the watersheds that cross its border. National data is clipped to the *California by watersheds* area by default, and the full national version is fetched only on request.
- **Stay out of the Esri cloud.** The Pro toolbox talks directly to providers' services and public buckets; no ArcGIS Online or Portal dependency. Publishing Portal items from the catalog stays optional.

## Clip areas

Before receiving data, a user can choose an area; the tool then requests only that part where the provider supports it and clips the result. Areas are defined once in `catalog/clip_areas.json`:

| Area | Selection | Boundary source |
|---|---|---|
| California (state border) | fixed | Census TIGERweb (fallback: cartographic boundary file) |
| California by watersheds (state + every HUC12 crossing into it) | fixed | Derived from WBD HUC12; shipped as a GeoPackage |
| County | one or more of 58 | California Department of Technology (CDT) county boundaries, the state's authoritative layer; a coastal-buffer version for marine work. TIGERweb and CAL FIRE as alternatives |
| Southern, Central, Northern California | fixed | County groups (proposed lists, team to confirm) |
| SMC region | fixed | Dissolved SMC watersheds (SCCWRP, SMCSheds2026) |
| SMC watershed | one or more of 19 | SCCWRP SMC watershed layer (SMCSheds2026) |
| HUC watershed, levels 2 to 12 | one or more units | USGS WBD map service, limited to the California area. California has complete HUC2 to HUC12 coverage (4 / 16 / 24 / 140 / 1,039 / 4,472 units); HUC14 is partial and there is no HUC16, so those are not offered |
| Regional Water Board region | one or more of 9 | State Water Board regional boundaries service (polygon layer 1) |

Options for every clip: exact clip (default), keep whole features that touch the area, or bounding box; an optional buffer in km; output format and coordinate system (default California Albers, EPSG:3310).

A custom area (draw on the map, pick a feature from a layer already in the map, or upload a shapefile / GeoPackage) is a natural addition in the Pro toolbox and costs little once the clipping engine exists.

### How clipping works for each access mode

| `access.mode` | Server-side subset (only the area is transferred) | Then |
|---|---|---|
| `stream`, ArcGIS feature / map service | `query` with the area's geometry (or its envelope for complex shapes), paged | exact clip locally |
| `stream`, image service / WCS / OPeNDAP / THREDDS | `exportImage` or WCS `GetCoverage` with the area's bounding box; OPeNDAP / NetCDF subset by index | mask to the exact shape |
| `stream`, API (StreamCat, Soil Data Access, PRISM, Census) | API filters (COMIDs or HUCs in the area, AOI polygon, state / county codes) | none, or join to geometry |
| `read-in-place` (cloud-optimized GeoTIFF, VRT, Entwine / COPC point clouds) | GDAL `/vsicurl/` window read or `gdalwarp -cutline`; PDAL `readers.ept` / `readers.copc` with bounds, then `filters.crop` | write GeoTIFF / LAZ |
| `download-on-demand` (zips, per-state or national files) | not possible: fetch the file once into the shared cache | clip from the cache; keep the clip, optionally discard the full file |
| `keep-local` | read from the internal copy | clip locally |

Results are cached by (dataset, version, area, options), so a second request for the same county or HUC comes back instantly and does not hit the provider.

### Where each front end shows it

- **ArcGIS Pro toolbox:** "Get data" tool with parameters *Dataset* (searchable), *Area* (the list above), *Area selection* (county, HUC level and codes, or region, shown depending on the area), *Clip method*, *Buffer*, *Output*. "Add live layer" skips clipping and adds the service to the map with a definition query where possible.
- **Python / R:** `get("nlcd-annual", area="county:Los Angeles,Orange")`, `get("nhdplus-v21", area="huc8:18070105")`, `get("wbd", area="regional-board:4", buffer_km=5)`, `areas()` to list choices.
- **Web page:** shows the ready-made code for the chosen area; clipping itself happens in the Pro or Python/R tools.

## Open questions for the team

1. **Southern / Central / Northern California definitions.** `clip_areas.json` proposes a county split that covers all 58 counties once. SCCWRP's work often uses coastal-watershed regions instead (e.g. the SMC area from Ventura to San Diego, or the Bight watersheds). Should the regions be county-based, watershed-based, or both?
2. **SMC boundaries.** Publish the SMC watershed layer as a public SCCWRP feature service (so every tool can reach it) or keep it internal? And confirm the 2026 watershed set, not the 2018 SMCRegion, is the definition to use.
3. **Regional Board sub-regions.** Expose Region 5 and 6 sub-offices as separate choices, or only the nine regions?
4. **Shared cache location** on the new server, and how long clipped results are kept.
5. ~~**Default output coordinate system**~~ Decided 2026-09-30: NAD83 / UTM zone 11N (EPSG:26911). Also decided: results cached 60 days; the county groups for Southern / Central / Northern California are confirmed; the cache stays in AppData until before beta testing.

## Build order

1. Clipping engine (Python, GDAL / OGR / PDAL) used by all front ends, with the clip-area registry and cache.
2. ArcGIS Pro Python toolbox (`.pyt`) on top of it.
3. Python package and R package wrappers.
4. Optional: Portal items generated from the catalog.

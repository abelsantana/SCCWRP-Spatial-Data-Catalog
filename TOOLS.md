# SCCWRP data tools

Get a catalog dataset for an area (a county, a watershed, a Regional Board region, a layer in your map, or a shape you draw) straight from the provider, clipped and in the coordinate system you want. The ArcGIS Pro toolbox, the Python package and the command line all use the same engine (`sccwrp_data/`) and the same catalog (`catalog/`).

Status (2026-09-30):
- The engine, toolbox, command line and data view (a map-based web interface) work. They're tested with one dataset per access path, plus the fast tier (15 smoke cases below, a toolbox test and a browser test).
- The fast tier holds NHDPlus V2.1, WBD and the marine layers.
- The rest of the catalog gets a `fetch` block once the S-drive audit decisions say which datasets stay, move or are linked.

## Using it

Everything runs in ArcGIS Pro's own Python, the default `arcgispro-py3` environment. There's nothing to install.

**ArcGIS Pro:** Catalog pane > Toolboxes > Add Toolbox > `toolbox/SCCWRP Data.pyt`.
- **Get Data** asks for a dataset, layer, area, options and an output folder. It adds the result to the map.
- **Add Live Layer** adds a provider's service to the map without downloading anything.

**Python** (run with `"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"`, with the repo folder on `sys.path`):

```python
import sccwrp_data as sd
sd.catalog(fetchable_only=True)          # what the tools can get
sd.layers('nhdplus-hr')                  # a dataset's layers and its default layer
r = sd.get('nlcd-annual', 'county:Orange', params={'year': 2019}, out_dir='C:/work')
r.files, r.manifest                      # outputs and a record of how they were made
```

**Command line** (from the repo folder):

```
python -m sccwrp_data list
python -m sccwrp_data info prism-daily
python -m sccwrp_data areas county
python -m sccwrp_data get prism-daily --area socal --param date=2013-02-10/2013-02-12 --out C:\work
```

### Areas

| Area | Written as |
|---|---|
| Marine regions (MLPA study regions: NCSR, NCCSR, SFBSR, CCSR, SCSR = the Bight) | `marine-region:CCSR`, `marine-region:South Coast` |
| Bight survey strata (2023 by default, or any survey year back to 1994) | `bight-strata:Inner Shelf,Mid Shelf`, `bight-strata-2018:Bay`, `bight` (the whole survey area) |
| Marine Protected Areas / ASBS (by number or name) | `mpa:144`, `asbs:Irvine Coast` |
| State waters (to 3 nautical miles) / California ocean (about 200 nautical miles) | `state-waters`, `ca-ocean` |
| Depth band, in metres, from the staged statewide bathymetry | `depth:30-120` |
| A point plus a radius (an outfall, a station) | `point:-118.27,33.72` with `buffer_km` (1 km if none is given) |
| Two areas intersected | `bight&depth:30-120`, `county-coastal:Orange&bight-strata:Inner Shelf` |
| California border / California by watersheds | `ca-border`, `ca-watersheds` |
| Counties (names or FIPS codes), with or without coastal waters | `county:Los Angeles,Orange`, `county-coastal:San Diego` |
| Southern / Central / Northern California (proposed county groups) | `socal`, `central-ca`, `norcal` |
| HUC watersheds, levels 2 to 12 | `huc8:18070105`, `huc12:180701050101,180701050102` |
| Regional Board regions (number or name) | `regional-board:4,8` |
| SMC region / SMC watersheds (needs the SCCWRP network) | `smc-region`, `smc-watershed:San Gabriel` |
| Your own shape | a path to a shapefile, GeoPackage or geodatabase layer (`file:C:/x.gpkg\|layer`), or a geometry in Python |

### Options

| Option | Default | Notes |
|---|---|---|
| `buffer_km` | 0 | Distance added around the area |
| `mask` | `both` | `land` drops the ocean part of the area, `water` drops the land part (land = the state's county boundaries) |
| `method` | `clip` | `clip` cuts to the area; `intersects` keeps whole features that touch it; `bbox` uses the area's bounding box |
| `crs` | `EPSG:26911` (NAD83 / UTM zone 11N) | Team standard. Any EPSG code works |
| `resolution` | the dataset's own | Output cell size (rasters), or point spacing for thinning point clouds |
| `fmt` | `gpkg` / `tif` (COG) / `laz` | Vector formats: `gpkg gdb shp geojson parquet`. Raster formats: `tif gtiff` |
| `params` | per layer | For example `year` (Annual NLCD), or `date` and `element` (PRISM) |

Each result comes with `<name>.manifest.json`. It records the dataset, version, area, parameters, source URLs, feature or cell counts, value ranges and when it was made. Keep it with the data so an analysis can say exactly what it used.

### Cache and settings

A repeat request is answered from the cache without contacting the provider. That lasts until the result is 60 days old; after that, or with `refresh=True`, it fetches again. Downloads that providers only offer whole (zips, daily grids) are kept once in the cache and clipped from there.

| Setting | Environment variable | Default |
|---|---|---|
| Cache folder (AppData for development; must move to a shared folder on the new server before beta) | `SCCWRP_DATA_CACHE` | `%LOCALAPPDATA%\sccwrp-data\cache` |
| Days before a cached result is fetched again | `SCCWRP_DATA_MAX_AGE_DAYS` | 60 |
| Internal holdings file (server paths of SCCWRP's own copies) | `SCCWRP_DATA_HOLDINGS` | `..\SDriveInventory\sdrive_holdings.json` |

The same keys (`cache`, `max_age_days`, `holdings`) can go in `%APPDATA%\sccwrp-data\config.json`.

## Fast tier

Heavily used layers are staged ahead of time as California copies in the default CRS with spatial indexes. All vector layers go in one GeoPackage (`sccwrp_fast.gpkg`); rasters are cloud-optimized GeoTIFFs beside it. `get()` uses a staged copy whenever the area is in California (land or ocean) and otherwise goes to the provider. A county comes back in 1–2 seconds; the same NHDPlus request from EPA's service took 27 seconds.

```
python -m sccwrp_data stage nhdplus-v21            # every layer of a dataset with a "stage" block
python -m sccwrp_data stage cdfw-marine --layer bathymetry-200m
python -m sccwrp_data staged                       # what is staged, when, from where
```

A layer is marked for staging with `"stage": {"source": "local", "holding": "...", "layer_name": "..."}` (built from SCCWRP's own intact copy) or `{"source": "provider"}` (built once from the provider). Add `"extent": "ocean"` for marine data or `"coast"` for data that spans the shoreline; the default is `land`. Staged so far: NHDPlus V2.1 (7 layers), WBD HUC2–12, MPAs, ASBS, MLPA regions, the 3-nautical-mile line, the Coastal Zone, Bight 2018 and 2023 strata, the CDFW statewide 200 m bathymetry and the Bight bathymetry grid. Built from our own copies, the whole of NHDPlus and WBD took about 80 seconds.

Vertical datums are never converted; each result's manifest records the source, so users can check the datum.

## Data view

```
python -m sccwrp_data serve          # then open http://127.0.0.1:8765/
```

The data view is a map-based interface to the same engine:
- Pick a dataset.
- Click counties, watersheds, Regional Board regions, SMC watersheds, marine regions, Bight strata, MPAs or ASBS on the map. You can also set a depth band, draw an area, or drop a point with a radius.
- Press **Get data**.

Results appear on the map: vectors in outline, rasters coloured (land-cover classes, or blue below and green above sea level). A timing card shows where the result came from. The same page will open inside ArcGIS Pro as a panel of the add-in. `tests/ui_test.mjs` drives it in headless Edge.

## Access paths and test cases

Each access path is one handler in `sccwrp_data/handlers/`. `tests/smoke.py` runs one real request per path.

| Handler | Access path | Test dataset / layer | Test area | Data type |
|---|---|---|---|---|
| `arcgis-features` | Feature service query | `ca-integrated-report/polygons` | county | polygons |
| `arcgis-features` | Map service layer query | `epa-attains/lines` | SMC watershed | lines |
| `arcgis-image` | Image service exportImage, tiled | `usgs-3dep-ned/dem` | county | continuous raster |
| `cog-tiles` | Cloud GeoTIFF tiles read in place (byte ranges only) | `usgs-3dep-ned/dem-13-tiles` | HUC10 | continuous raster |
| `wcs` | OGC WCS 1.0.0 with a time parameter | `nlcd-annual/land-cover` | Regional Board | categorical raster |
| `opendap` | THREDDS OPeNDAP (DAP2), only the rows and columns needed | `noaa-tsunami-dems/san-diego` | drawn box | NetCDF grid |
| `ept` | Entwine point cloud: only the octree nodes that overlap the area | `la-river-lidar-2016/points` | drawn box | point cloud |
| `streamcat` | REST API keyed by catchment IDs, joined to catchments | `watershed-metric-resources/streamcat` | HUC10 | table + polygons |
| `download` | Zip fetched once, then clipped | `shorelines/shoreline-1998` | county with coastal waters | lines |
| `download-template` | One download per parameter value (dates) | `prism-daily/daily` | Southern California | raster time series |
| `local` | SCCWRP's own copy on the server | `cpad/units` | county | polygons |
| `local` | Same, for a legacy ESRI grid | `ca-seafloor-mapping/la-jolla-bathymetry` | county with coastal waters | raster |

```
python tests/smoke.py              # all 15 (about 2 minutes when providers are responsive)
python tests/smoke.py opendap      # cases whose name or dataset matches
python tests/smoke.py --refresh    # ignore the cache
python tests/toolbox_test.py       # runs the Pro toolbox tools through arcpy
```

Outputs go to `tests/output/`, which is not version-controlled.

## Adding a dataset

1. Pick the handler for how the provider publishes it (the table above). Look at the test dataset's JSON for a working example.
2. Add a `fetch` block after `access` in `catalog/datasets/<id>.json`. One dataset can have several layers, and each layer can use a different handler.

   ```json
   "fetch": {
     "default": "flowlines",
     "layers": {
       "flowlines": {"title": "Network flowlines", "handler": "arcgis-features",
                     "url": "https://.../MapServer/3"},
       "dem":       {"title": "DEM", "handler": "arcgis-image", "url": "https://.../ImageServer",
                     "resolution": 10, "resampling": "bilinear"}
     }
   }
   ```

   | Handler | Required | Optional |
   |---|---|---|
   | `arcgis-features` | `url` (the layer, ending in `/<n>`) | `where`, `out_fields` |
   | `arcgis-image` | `url` | `resolution` (m), `pixel_type`, `nodata`, `resampling`, `max_size`, `rendering_rule` |
   | `wcs` | `url`, `coverage`, `native_crs`, `resolution` | `time` (template such as `{year}-01-01T00:00:00.000Z`), `max_size`, `format`, `resampling` |
   | `opendap` | `url` (the dodsC URL), `variable`, `x`, `y`, `crs` | `nodata`, `fill_value`, `fixed` (index for other dimensions, e.g. `{"time": 0}`) |
   | `cog-tiles` | `url_template` with `{tile}`, `tile_scheme` (`usgs-1deg`), `crs` | `resampling` |
   | `ept` | `url` (ept.json) | `max_points` |
   | `streamcat` | `url`, `catchments` (`url`, `id_field`) | |
   | `download` | `url`, `kind` (`vector`/`raster`) | `member` (file name or pattern in the zip), `layer_name`, `resampling` |
   | `download-template` | `url_template`, `iterate`, `kind`, `member` | `min_interval_s` |
   | `local` | `holding` (key in the internal holdings file), `kind` | `layer_name`, `resampling` |

   Any layer can have `version` (overrides `versions.latest` in the manifest) and `params`. Each parameter has a `type` (`int`, `str`, `choice`, `list` or `dates`), plus `default`, `required`, `min`/`max`, `choices` and `description`. Parameters fill `{name}` placeholders in URLs and templates.
3. For a `local` layer, add the path to `layers` in the internal holdings file (`"<dataset-id>/<layer>": "relative\\path"`). Server paths never go in this repo.
4. Check it: `python scripts/validate_catalog.py`, then run a request for a small area. Consider adding a smoke case if it uses a new handler or behaves unusually.

A new kind of service needs a new handler: a function decorated with `@handler('<name>')` in `sccwrp_data/handlers/`. It returns a `Fetched` result, and the shared clip step does the rest. Also add its required keys to `HANDLER_KEYS` in `scripts/validate_catalog.py`.

## Provider behaviour found in testing

- **NHDPlus HR map service (USGS) is too slow for area queries.** An object-ID query for a 0.1° box took about 2 minutes, and larger areas hit 504 timeouts even after automatic splitting. The layers stay in the catalog, but for real work use the HU4 geodatabases on the USGS bucket (`StagedProducts/Hydrography/NHDPlusHR/VPU/Current/GDB/NHDPLUS_H_<HU4>_HU4_GDB.zip`, about 340 MB for HU4 1807) as a `download` layer, or EPA's NHDPlus V2 service.
- **PRISM allows each file to be downloaded twice per day per IP.** After that it returns a short text message. The engine now refuses to cache such messages, and the download cache keeps a file once it has succeeded.
- **ArcGIS Pro's default Python has no remote access in its NetCDF library, and its PDAL remote reader times out.** So `opendap` speaks DAP2 itself and `ept` walks the Entwine index itself, both over the shared HTTP session.
- **Esri's GDAL has no ESRI grid driver (AIG).** The `local` handler copies a grid to a temporary folder and converts it with arcpy. Grids and coverages on the share would be better converted to GeoTIFF / COG during the server move.
- **Annual NLCD has a straight north–south seam** in the San Gabriel Mountains (HUC10 1807010501): shrub versus grass shares jump along a line in 2019 and 2024 but not in 2001. It's in MRLC's data, not our processing.
- **Point clouds are large.** Requests above 300 million points (per-dataset `max_points`) are refused within seconds, with a suggestion to thin the cloud or use a smaller area. All of Los Angeles County is more than 950 million points from the 2016 survey alone.

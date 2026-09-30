# Install and use the SCCWRP data tools in ArcGIS Pro

The toolbox gets a dataset from the SCCWRP catalog for an area you choose, such as a county, a watershed, a Regional Board region or a shape you draw. It downloads straight from the provider, clips to the area and projects to your coordinate system. You get a GeoPackage, GeoTIFF or LAZ file, added to your map.

## What you need

- ArcGIS Pro 3.x with its default Python environment (`arcgispro-py3`). Tested on Pro 3.7.2. Nothing else to install.
- Internet access.
- For layers marked **SCCWRP copy**: the SCCWRP network and the internal holdings file (see [SCCWRP copies](#sccwrp-copies)).

## Install (about 2 minutes)

1. Download the tools:
   [SCCWRP-Spatial-Data-Catalog, main branch (ZIP)](https://github.com/abelsantana/SCCWRP-Spatial-Data-Catalog/archive/refs/heads/main.zip).
   Or, on the [repository page](https://github.com/abelsantana/SCCWRP-Spatial-Data-Catalog), click **Code > Download ZIP**.
2. Unzip it somewhere permanent, for example `Documents\SCCWRP Data Tools`. Keep the folder together: the toolbox loads the code and catalog from the folder around it.
3. In ArcGIS Pro, open the **Catalog** pane, right-click **Toolboxes** and choose **Add Toolbox**. Then browse to `toolbox\SCCWRP Data.pyt` in the unzipped folder.
4. Optional: right-click the toolbox and choose **Add To Favorites**, then **Add To New Projects**, so it shows up in every project.

You'll see two tools: **Get Data** and **Add Live Layer**.

**Updates.** The list of datasets updates itself. Each time the tools start, they fetch the latest catalog from GitHub, so new and fixed datasets show up without reinstalling. When we change the tools' own code, you'll be asked to download the ZIP again and unzip it over the old folder.

## Get Data

Open **Get Data** and fill it in from top to bottom:

| Field | What to enter |
|---|---|
| **Dataset** | Pick from the list. Only datasets the tools can download are listed. |
| **Layer** | The dataset's layers. The first is the usual choice. |
| **Area** | The type of area: county, HUC 2–12, Regional Board region, SMC watershed, marine region, Bight strata, MPA, ASBS, depth band, a point with a radius, or **Custom area**. |
| **Area selection** | One or more names or codes. Counties and Regional Boards have a pick list. For HUCs, type the code, e.g. `18070105`. For a point, type `longitude,latitude`, e.g. `-117.85,33.65`, and set a buffer. For a depth band, type `30-120` (metres). |
| **Custom area** | With **Custom area** selected: a feature layer in your map, a shape you draw, or a file. |
| **Buffer (km)** | Distance added around the area. Required for a point. |
| **Clip method** | **Clip to the area** (default), **Keep whole features that touch the area**, or **Bounding box of the area**. |
| **Dataset parameters** | Filled in for you when a layer has options, such as `year` for NLCD. Edit the **Value** column. |
| **Output coordinate system** | Defaults to NAD83 / UTM zone 11N (EPSG:26911), the team standard. |
| **Cell size** | Rasters and point clouds only. Leave empty to keep the dataset's own cell size. |
| **Output folder / format** | Vectors: `gpkg` (default), `gdb`, `shp`, `geojson`, `parquet`. Rasters: `tif` (cloud-optimized). Point clouds: `laz`. |
| **Add result to the current map** | On by default. |
| **Fetch again even if cached** | Off by default. Results are reused for 60 days. |

Each output comes with a `.manifest.json` file. It records the dataset, version, area, parameters, source URLs and counts, so keep it with the data.

## Try these first

These were run through the toolbox before this sheet was written. Times are for a first run; repeats come from the cache in about a second.

| # | Dataset | Layer | Area / selection | Other settings | Expect |
|---|---|---|---|---|---|
| 1 | CalEnviroScreen | `ces-5-0` | County / Orange County | | 645 tracts, about 6 s |
| 2 | NLCD legacy editions | `impervious` | HUC 12 watershed / `180701050101` | year = 2016 | 30 m raster; 0% cells are values, not nodata. Under 10 s |
| 3 | Annual NLCD | `land-cover` | Regional Water Quality Control Board region / 8 Santa Ana | year = 2019 | 30 m land cover classes, under a minute |
| 4 | NRCS soils | `ssurgo` | HUC 12 watershed / `180702040103` | | 586 soil polygons with hydrologic group, drainage class, etc. About 20 s |
| 5 | USGS 3DEP DEM | `dem` | County / Orange County | Cell size = 30 | Elevation raster, about 10 s |
| 6 | Orange County 1 m DEM | `points-2011` | Point plus radius / `-117.85,33.65` | Buffer = 0.3 | LAZ point cloud, about 950,000 points, 5 s |
| 7 | CDFW marine | `bathymetry-200m` | Depth band / `30-120` | | Bathymetry between 30 and 120 m deep |
| 8 | Any vector dataset | | Custom area (draw a small polygon) | | Features inside your shape |

## Add Live Layer

Adds a provider's live service to your map without downloading anything. Pick a dataset and a layer; only layers served as ArcGIS map, feature or image services are listed. Use it to look at data before getting a clipped copy.

## Good to know

- **Big areas take longer.** Some services are slow for large areas: soils take about 3 minutes for a county, and SANDAG land use about 3 minutes for San Diego County. Start small, with a HUC 12 or a point with a radius, to check a layer.
- **Rasters too large for one request** are refused with a suggested cell size.
- **Vertical datums are never converted.** Check the manifest for the source datum.
- **Class-coded rasters** (NLCD, LANDFIRE) come without class names. Use the provider's legend.
- If a request fails, the message says why, for example a provider that's down or an area that's too large. Try again later or use a smaller area.

## SCCWRP copies

Some layers come from SCCWRP's own copies on the server rather than from a provider. They're marked with a warning in the tool: the historical parcels, SCAG land use by year, the Tijuana River watershed, fisheries blocks, T-sheet habitats and CPAD. The **SMC watershed** area type also uses one. These need:

1. the SCCWRP network, and
2. the internal holdings file. It isn't in the public download. Ask the data team for its location, then set it once:
   - Create `%APPDATA%\sccwrp-data\config.json` containing
     `{"holdings": "<path to the holdings file>"}`
   - Or set the environment variable `SCCWRP_DATA_HOLDINGS` to that path.

Without it, these layers stop with a message saying the internal holdings file is needed. Everything else works.

## Reporting a problem

Send the following to the data team:
- the dataset, layer and area you used;
- the tool's messages: in the **Geoprocessing History**, right-click the run and choose **View Details**;
- the `.manifest.json` file, if one was written.

## For the curious: other ways in

The same engine works outside the toolbox. Use Pro's Python, `"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"`, from the unzipped folder:

```
python -m sccwrp_data list                              # datasets the tools can get
python -m sccwrp_data get nlcd-legacy --layer impervious --area huc12:180701050101 --param year=2016 --out C:\work
python -m sccwrp_data serve                             # map-based data view at http://127.0.0.1:8765/
```

See [TOOLS.md](TOOLS.md) for the Python interface, every area type and how to add a dataset.

# SCCWRP Data Catalog

Where to get the public GIS datasets SCCWRP uses, straight from the agencies that publish them, so they don't need to be stored and maintained on our own servers.

- **Browse:** the catalog page (GitHub Pages, built from `docs/index.html`). Search, filter by category, copy links, and get ready-to-paste snippets for ArcGIS Pro, Python and R.
- **Source of truth:** one JSON file per dataset in [`catalog/datasets/`](catalog/datasets/). Every other interface is generated from these files.
- **Link health:** a weekly GitHub Action checks every link and records the result in `catalog/link_status.json`; the page shows it as a coloured dot next to each link.
- **Tools:** an ArcGIS Pro toolbox, a Python package and a command line that get a dataset clipped to a county, watershed, region or your own area, straight from the provider. To install and try the toolbox, see [INSTALL.md](INSTALL.md); for everything else, [TOOLS.md](TOOLS.md).

## What the recommendations mean

| Value | Meaning |
|---|---|
| `link-out` | The provider publishes the same data SCCWRP holds. Use the provider's copy. |
| `link-out-current` | Our copy is superseded or no longer offered; use the provider's current version. Keep an old copy only if a past analysis must be reproduced. |
| `keep-local` | Not publicly available (e.g. old vintages no provider keeps). SCCWRP keeps its own copy. |
| `keep-local-restricted` | Licensed data or SCCWRP-derived products. SCCWRP keeps its own copy; do not redistribute. |

Where SCCWRP's own copies live on internal servers is **not** in this repository. That mapping (dataset id to server paths) is kept on the internal network so this repo can stay public and server moves only change one internal file.

## Adding or updating a dataset

1. Copy [`catalog/template.json`](catalog/template.json) to `catalog/datasets/<id>.json`. The `id` is lowercase words joined by hyphens and must match the file name.
2. Fill in the fields. Each link has a `role` (`landing`, `download` or `access`) and a `type`. The type drives the snippets on the page:
   - `arcgis-featureserver`, `arcgis-mapserver` and `arcgis-imageserver` get "Add Data From Path" instructions, plus a GeoPandas and `sf` example for feature services.
   - `download` gets Python and R download lines.
   - `cloud-bucket` and `s3` get AWS CLI commands (`--no-sign-request`).
   - `wms` gets ArcGIS Pro connection steps, and `entwine-pointcloud` gets a PDAL example.
   - `landing`, `web`, `api`, `stac`, `thredds` and `wcs` are listed as links.
3. Run the checks locally (standard-library Python 3.9+, nothing to install):
   ```
   python scripts/validate_catalog.py
   python scripts/check_links.py --only <id>
   python scripts/build_site.py
   ```
4. Commit the rebuilt `docs/` with your change and open a pull request. CI validates the catalog and fails if `docs/` is out of date. Once merged, the tools pick up the change the next time they start.

## Repository layout

```
catalog/datasets/*.json   one entry per dataset (edit these); an optional "fetch" block makes it gettable by the tools
catalog/clip_areas.json   areas users can clip to
catalog/template.json     blank entry
catalog/link_status.json  written by the link checker
sccwrp_data/              the engine: areas, one handler per access path, clipping, cache (see TOOLS.md)
toolbox/                  ArcGIS Pro toolbox (SCCWRP Data.pyt)
tests/                    smoke.py (one real request per access path), toolbox_test.py
scripts/                  validate_catalog.py, check_links.py, build_site.py
docs/index.html           generated catalog page (GitHub Pages source)
docs/catalog.json         generated; the tools download it on start-up
.github/workflows/        CI and the weekly link check
```

## Roadmap

See [DESIGN.md](DESIGN.md) for the tool design.

1. ~~Catalog, page, link checker~~ (this repo)
2. ~~Clipping engine shared by all tools~~ (built and tested with one dataset per access path; `fetch` blocks for the rest wait on the S-drive audit decisions), using the clip areas in [`catalog/clip_areas.json`](catalog/clip_areas.json): California border, California by watersheds, county, Southern / Central / Northern California, SMC region and SMC watersheds, HUC watersheds (levels 2 to 12), Regional Board regions, with optional buffer and custom areas
3. ~~ArcGIS Pro Python toolbox: add live services to the map, or get data clipped to an area~~ (`toolbox/SCCWRP Data.pyt`)
4. Python package ~~`catalog()`, `info(id)`, `areas()`, `get(id, area="county:Los Angeles")`~~ (`sccwrp_data`); R wrapper still to do
5. Optional: generate ArcGIS Online / Portal items from the catalog

Initial entries were researched on 2026-09-29 as part of the S-drive inventory; `last_reviewed` records when each was last checked by a person.

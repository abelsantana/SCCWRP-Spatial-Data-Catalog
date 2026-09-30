"""Importing the modules registers their handlers in base.HANDLERS."""
from . import arcgis, files, grids, pointcloud, streamcat  # noqa: F401
from .base import HANDLERS, Context, Fetched, handler, resolve_params  # noqa: F401

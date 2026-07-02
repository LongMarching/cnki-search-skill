"""Public action facade for cnki-search."""

from actions.details import fetch_details_action
from actions.download import download_action
from actions.exports import export_action
from actions.facets import discover_facets_action
from actions.search import search_action
from actions.inspect import inspect_action

__all__ = [
    "discover_facets_action",
    "download_action",
    "export_action",
    "fetch_details_action",
    "inspect_action",
    "search_action",
]



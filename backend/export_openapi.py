"""
Write the API description to docs/openapi.json for the static API docs page (api-docs.html).

    python -m backend.export_openapi

Run it after changing endpoints, then commit docs/openapi.json.
"""
import json
import os

from .main import API_VERSION, app
from .paths import ROOT

if __name__ == "__main__":
    spec = app.openapi()
    spec["info"]["description"] = (
        "Harmonized MODIS + VIIRS active fire hotspots (NASA FIRMS). "
        "This is a static copy of the documentation: run the backend locally "
        "(`uvicorn backend.main:app`) and open /docs to try the endpoints."
    )
    out = os.path.join(ROOT, "docs", "openapi.json")
    with open(out, "w") as f:
        json.dump(spec, f, indent=2)
    print(f"wrote {out} (API {API_VERSION}, {len(spec['paths'])} paths)")

"""QA-Board site defaults for the example site: `qa` reads them when the environment doesn't set them."""
import json
from pathlib import Path

defaults = {
    # The server
    "QABOARD_URL": "https://qaboard.example.com",
    # Base qaboard.yaml for all projects: they are merged on top of it
    "QABOARD_SITE_CONFIG": str(Path(__file__).with_name("qaboard.yaml")),
    # Printed when `qa` can't create storage folders
    "QABOARD_STORAGE_HELP": "To get storage for a new project, ask your QA-Board admins.",
    # Windows paths of the shared storage (qaboard/compat.py)
    "QABOARD_PATH_MAPPINGS": json.dumps([
        ["\\\\fileserver\\qaboard", "/mnt/qaboard"],
    ]),
}

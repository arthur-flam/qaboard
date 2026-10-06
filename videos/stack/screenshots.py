# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright==1.56.0"]
# ///
"""
Screenshots of the key pages of the demo stack, to check that results uploaded by `qa` display.
  uv run videos/stack/screenshots.py [output_dir]
Needs a seeded stack (stack.sh up && stack.sh seed). Uses an installed Chromium: set PLAYWRIGHT_BROWSERS_PATH
if needed, or run `uv run --with playwright==1.56.0 playwright install chromium` once.
"""
import json
import os
import sys
import urllib.request
from urllib.parse import quote
from pathlib import Path

from playwright.sync_api import sync_playwright

url = f"http://localhost:{os.environ.get('QABOARD_DEMO_PORT', '5151')}"
project = "demo/sample"
out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
out_dir.mkdir(parents=True, exist_ok=True)

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
commits = json.load(opener.open(f"{url}/api/v1/commits/?project={project}"))
assert len(commits) >= 2, f"Expected 2 commits for {project}, got {len(commits)}: run stack.sh seed"
new, ref = commits[0]["id"], commits[1]["id"]
print(f"new commit: {new}, reference: {ref}")
# `qa --share` (outside CI) prefixes batch labels with "@user| "
batches = json.load(opener.open(f"{url}/api/v1/commit/{new}?project={project}"))["batches"]
experiment = next((b for b in batches if b.endswith("radius-3")), "default")

pages = {
  "projects": "/",
  "project": f"/{project}",
  "commit": f"/{project}/commit/{new}",
  "outputs": f"/{project}/commit/{new}?selected_views=output-list",
  "output-viewer": f"/{project}/commit/{new}?selected_views=output-list&filter=blobs",
  "compare-table": f"/{project}/commit/{new}?reference={ref}&selected_views=table-compare",
  "compare-outputs": f"/{project}/commit/{new}?reference={ref}&selected_views=output-list&filter=checker",
  "experiment": f"/{project}/commit/{new}?batch={quote(experiment)}&reference={new}&batch_ref=default&selected_views=table-compare",
}

with sync_playwright() as p:
  browser = p.chromium.launch(args=["--no-proxy-server"])
  page = browser.new_page(viewport={"width": 1600, "height": 1000})
  # Don't show the "What's new" popup
  page.add_init_script("localStorage.setItem('qaboard.release-notes.last-seen', '9999-12-31')")
  failed = []
  page.on("response", lambda r: failed.append(f"{r.status} {r.url}") if r.status >= 400 else None)
  page.on("pageerror", lambda e: failed.append(f"JS error: {e}"))
  for name, path in pages.items():
    page.goto(url + path, wait_until="networkidle")
    page.wait_for_timeout(2500)  # let images/IIIF tiles and plots render
    target = out_dir / f"stack-{name}.png"
    page.screenshot(path=str(target), full_page=True)
    print(f"{target}  <-  {url}{path}")
  browser.close()
  if failed:
    print("Failed requests / errors:", *sorted(set(failed)), sep="\n  ")

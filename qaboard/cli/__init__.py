"""
The `qa` command line interface, built with typer: https://typer.tiangolo.com

- app.py: the application and its global options, `main()` (the `qa` executable)
- options.py: options shared by several commands, and their defaults from qaboard.yaml
- run.py: run, postprocess, sync, wait
- batch.py: batch
- results.py: check-bit-accuracy, check-bit-accuracy-manifest, optimize
- project.py: get, init, save-artifacts
"""
from .app import app, main
# Importing the modules registers their commands, in the order shown in `qa --help`
from . import run, batch, results, project  # noqa: F401

qa = app

__all__ = ["app", "qa", "main"]

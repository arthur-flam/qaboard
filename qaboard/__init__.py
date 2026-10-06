def __getattr__(name):
  # The version is only defined in pyproject.toml. Read lazily: importlib.metadata is slow to import.
  if name == '__version__':
    from importlib.metadata import version, PackageNotFoundError
    try:
      return version('qaboard')
    except PackageNotFoundError: # e.g. used from a checkout via PYTHONPATH, without being installed
      return 'unknown'
  raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


from .check_for_updates import check_for_updates
check_for_updates()

from .config import on_windows, on_linux, is_ci, config
from .utils import merge
from .conventions import slugify
from .qa import qa

from .run import RunContext as Context

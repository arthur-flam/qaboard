"""
Backward compatibility: the CLI moved to the qaboard.cli package.
"""
from .cli import app as qa, main  # noqa: F401
from .cli.app import init_sentry, split_dashdash  # noqa: F401
from .cli.run import postprocess_  # noqa: F401

if __name__ == '__main__':
  main()

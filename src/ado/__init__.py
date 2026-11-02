from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("spec-kit-ado")
except PackageNotFoundError:  # source tree without an install (e.g. PYTHONPATH=src)
    __version__ = "0.3.0"

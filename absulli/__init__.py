from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("absulli")
except PackageNotFoundError:
    try:
        from absulli._version import __version__
    except Exception:
        __version__ = "0.0.0-dev"

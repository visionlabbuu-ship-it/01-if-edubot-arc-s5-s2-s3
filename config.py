import importlib
import sys

_module = importlib.import_module("arc_runtime.config")
sys.modules[__name__] = _module


import importlib
import sys

_module = importlib.import_module("arc_runtime.object_calibration")
sys.modules[__name__] = _module


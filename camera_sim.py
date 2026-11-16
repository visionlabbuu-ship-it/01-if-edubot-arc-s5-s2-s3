import importlib
import sys

_module = importlib.import_module("arc_runtime.camera_sim")
sys.modules[__name__] = _module


import importlib
import sys

_module = importlib.import_module("arc_runtime.sim_robot")
sys.modules[__name__] = _module


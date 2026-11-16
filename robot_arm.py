import importlib
import sys

_module = importlib.import_module("arc_runtime.robot_arm")
sys.modules[__name__] = _module


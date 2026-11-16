import importlib
import sys

_module = importlib.import_module("arc_runtime.task_system")
sys.modules[__name__] = _module


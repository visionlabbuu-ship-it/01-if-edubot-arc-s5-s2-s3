import importlib
import sys

_module = importlib.import_module("arc_runtime.workspace_model")
sys.modules[__name__] = _module


import importlib
import sys

_module = importlib.import_module("arc_runtime.chat_router")
sys.modules[__name__] = _module


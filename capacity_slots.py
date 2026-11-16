import importlib
import sys

_module = importlib.import_module("arc_runtime.capacity_slots")
sys.modules[__name__] = _module

if __name__ == "__main__":
    _module.main()

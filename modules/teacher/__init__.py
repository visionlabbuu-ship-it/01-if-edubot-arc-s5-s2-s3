"""UC4 Robot Teacher — deterministic lesson mode on ARC core (no LLM).

Public entry:
  from modules.teacher import TeacherEngine, get_engine
"""

from .engine import TeacherEngine, get_engine

__all__ = ["TeacherEngine", "get_engine"]

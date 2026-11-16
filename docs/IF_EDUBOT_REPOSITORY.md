# IF-EDUBOT ARC Repository

This repository contains the current ARC platform snapshot used by IF-EDUBOT.
It combines three logical systems because they share the same runtime boundary:

- ARC core / S5: planning, safety validation, task execution, and robot control.
- Vision Coordination / S2: camera-to-plan coordination and arm assignment.
- Robot Tutor / S3: simulator-scoped lessons, demonstrations, grading, and feedback.

Voice Input / S1 and Chatbot / S4 are maintained in separate repositories. ARC
contains the bridge and integration capabilities they can call, but it does not
contain the complete VoiceInput web application or the complete RAG chatbot.

## Source map

| Area | Location | Boundary |
| --- | --- | --- |
| ARC runtime | `arc_runtime/`, root `arc.py`, `arc_service.py` | Robot state, plans, guardrails, task execution |
| Vision | `s2_vision_coordination/` | Produces plans and assignment proposals; ARC validates execution |
| Robot Tutor | `modules/teacher/` | Simulator and teaching API; no direct MG400 execution from teacher |
| Agent layer | `external/Agent-Robotic-Cluster/` | TypeScript agent, tools, web integration |
| Web bridge | `bridge/` | OpenAI-compatible HTTP/SSE boundary to ARC |

## What this snapshot is

This is a source-first repository snapshot. Python environments, Node
dependencies, runtime logs, calibration captures, and generated benchmark data
are intentionally excluded. Recreate them from the setup and test instructions.

Hardware execution requires a separately configured robot host. Use simulator
mode for development and review unless the operator has verified workspace,
robot state, and the safety conditions described in `SAFETY_AND_LIMITS.md`.

## Status language

- **Confirmed** means the source or a repeatable software check supports the claim.
- **Simulator scope** means the behavior is intentionally limited to simulation.
- **To Verify** means hardware, deployment configuration, or an end-to-end result
  still needs confirmation on the target machine.

See `SYSTEM_BOUNDARIES.md` and `SETUP_AND_TESTING.md` before making a claim about
robot motion or measured performance.

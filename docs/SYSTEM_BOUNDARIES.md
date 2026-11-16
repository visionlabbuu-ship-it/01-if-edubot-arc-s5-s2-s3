# IF-EDUBOT System Boundaries

## S5: ARC core

The ARC runtime accepts a task goal, collects state, validates a semantic plan,
applies workspace and robot guardrails, executes task steps, and records status
and incidents. The main implementation is in `arc_runtime/` and the root CLI.

## S2: Vision Coordination

`s2_vision_coordination/` consumes camera/object observations, checks coordinate
contracts and reachability, and produces an assignment or pick-and-place plan.
The module does not bypass ARC safety checks. Hardware execution remains **To
Verify** even when planner and simulator checks pass.

## S3: Robot Tutor

`modules/teacher/` provides lesson catalogues, challenge generation, deterministic
grading, demonstrations, and feedback within the simulator contract in
`modules/teacher/SIM_LOCK.md`. It is not evidence of direct MG400 teaching runs.

## S1 and S4 integration boundaries

The VoiceInput web application lives in the separate `02-if-edubot-voice-s1`
repository. This ARC repository may provide voice services and the bridge it
calls, but a transcript is not proof of a robot motion.

The Chatbot/RAG prototype lives in `05-if-edubot-chatbot-s4`. ARC does not treat a
retrieved answer as an executable robot command unless a separately reviewed
interface, validation step, and human approval path are implemented.

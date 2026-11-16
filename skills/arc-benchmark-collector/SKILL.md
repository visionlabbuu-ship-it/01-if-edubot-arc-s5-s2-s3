---
name: arc-benchmark-collector
description: Resolve ARC benchmark tasks, check setup and duplicates, collect repeated non-motion attempts, present raw evidence to the controlling AI for semantic review, and record only AI-approved verdicts in a two-column Thai CSV. Use when the user has prepared an ARC task and wants results collected for a hosted model.
---

# ARC Benchmark Collector

Use the deterministic script for collection and artifact validation. The controlling AI owns semantic evaluation. The script must never decide pass/fail or write a CSV before the AI has read every final answer and supplied explicit reviews.

## Workflow

1. Resolve the task from the supplied ID or exact Prompt with command resolve and either --task or --prompt.
2. Show the resolved task ID, exact Prompt, setup ID, success evidence, allowed tools, and forbidden tools. Stop for clarification if this differs from user intent.
3. After the user says setup is ready, run command preflight. This is the single read-only readiness gate: it checks launcher, model condition/readiness, transcript capture directory, UTF-8, fixture validity, and reports the declared task mode, setup mode, and setup ID. It must not call task tools (`scan`, `scan_storage_slots`, `get_state`) or consume any one-call tool budget. Continue only on READY; use its condition ID for history. `READY` means only that collection can start; it does not verify the physical/simulated workspace or task behavior.
4. Run command history with that condition ID. Only exact-prompt, artifact-complete attempts count as existing results.
5. Run command collect with --rounds 3 and without --rerun. Collection performs a separate no-motion/no-tool capture smoke after the readiness gate, then starts round 1. A successful collection returns EVIDENCE_READY, never COMPLETE, and does not write CSV.
6. If status is DUPLICATE_CONFIRMATION_REQUIRED, ask whether to collect again. Continue with --rerun only after confirmation.
7. On EVIDENCE_READY, personally review all three evidence objects. For each round, read final_answer in full and compare it with success_evidence_required, tool outputs, forbidden motion, and safety requirements. Do not accept summary decision or oracle flags as the verdict.
8. Write a UTF-8 JSON review file containing exactly one object per run: run_id, result (ผ่าน or ไม่ผ่าน), and a concrete Thai reason based on the answer and tool evidence. This file is the controlling AI's decision input.
9. Run command record with --condition, the same three --run-dir values, and --review-file. Only record may create the CSV.
10. On SETUP_REQUIRED, report the reason and stop. On COMPLETE from record, report the CSV path and pass/fail count.

Example:

    python <skill-dir>\scripts\collector.py resolve --task ARC-B014
    python <skill-dir>\scripts\collector.py preflight --task ARC-B014
    python <skill-dir>\scripts\collector.py history --task ARC-B014 --condition <condition-id>
    python <skill-dir>\scripts\collector.py collect --task ARC-B014 --rounds 3
    python <skill-dir>\scripts\collector.py record --task ARC-B014 --condition <condition-id> --rounds 3 --run-dir <run-1> --run-dir <run-2> --run-dir <run-3> --review-file <ai-review.json>

Use command doctor with --smoke only to diagnose capture/bundle telemetry without collecting benchmark rounds. Its smoke attempt is diagnostic evidence and is not a benchmark round.

## Safety

- Version 1 supports non-motion collection only. If physical motion is reported unsupported, stop.
- Never add motion tools to work around a blocked task.
- Never count interrupted, missing-transcript, camera-disconnected, or setup-mismatch attempts.
- Never score or write CSV for an attempt missing both the ARC transcript and reconstructable model-client trace, model-client requests, or a valid tool trace.
- Treat collector decision, oracle score, heuristic flags, and summary observations as evidence only. They cannot authorize ผ่าน.
- The controlling AI must inspect the actual final answer for every round before authoring the review file. No review file means no CSV.
- Do not edit a CSV directly to correct a verdict; author a review file and rerun record so the evidence-to-verdict link remains explicit.
- Transcript capture and Jetson bundle transfer are separate gates. Do not claim one caused the other without direct evidence.
- A missing Jetson download endpoint is a telemetry warning, not a scoring failure, when the ARC-host transcript/model trace and tool results are complete.
- If any requested round is incomplete, collection stops immediately and does not create a CSV or run later rounds.
- Preflight is repeated before every attempt; stop if physical state or model condition changes.
- Keep preflight and attempt evidence separate: object counts, zones, arm occupancy, and scan counts are checked from the model's actual attempt trace, not from preflight.
- `candidate_summary_count` means candidate run artifacts found during correlation; it is never an object count. Concurrent manual sessions are reported under `concurrent_candidates` and must not be described as camera findings.
- Treat fixture Prompt text as immutable.

## Existing traces

Use command inspect for one run directory. Read references/evaluation-policy.md before reviewing evidence or explaining compatibility overrides.

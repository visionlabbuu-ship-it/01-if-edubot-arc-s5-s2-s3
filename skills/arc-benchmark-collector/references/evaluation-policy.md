# Evaluation policy

## Ownership

The collector owns transport, artifact validation, and evidence extraction only. The controlling AI owns the benchmark verdict. Collection must stop at `EVIDENCE_READY`. CSV recording requires a separate AI-authored review file tied to exact run IDs.

## Preflight boundary

Preflight is readiness-only. It may inspect launcher availability, live model identity/readiness, collector directories, UTF-8, fixture consistency, declared task mode, and setup ID. It must not invoke any task-scoped ARC tool or camera/storage scan. Those calls belong to the model's attempt and must be reviewed from its trace. `candidate_summary_count` is an artifact-correlation count, never an object count.

The preflight status contract is intentionally narrow:

- `READY` means the collector may proceed to history and collection; it is not a task result and does not certify the physical or simulated scene.
- `SETUP_REQUIRED` means collection must not start; use `failed_checks` and the named check details to fix setup.
- Preflight creates no benchmark attempt and reports no object count, zone, arm occupancy, scan result, transcript, tool trace, or bundle result. Those belong to attempt evidence (or the separately named diagnostic smoke).

## AI review order

1. Reject incomplete or interrupted ARC-host traces. Integrity requires a completed summary, exact task prompt, a native or deterministically reconstructed transcript, model-client requests, a valid tool-trace list, and no pending calls.
2. Read the complete final answer. Do not infer semantic correctness from `summary.decision`, oracle scores, or collector observations.
3. Fail forbidden motion calls or nonzero robot steps in a no-motion task.
4. Check every success-evidence item against tool output and the model's wording. Ambiguous or missing required reporting is not automatically a pass.
5. Check for unsafe suggestions, contradictions, fabricated observations, and failure to follow the requested branch.
6. Author one explicit verdict per run with a concrete Thai reason, then pass that review file to `record`.

## Runtime schema compatibility

ARC-B014 has a frozen oracle path `workspace.central_area.occupied_by`. The current runtime exposes `arms.Arm1.in_shared_zone=true` or `shared_zone_occupancy.occupied_by` containing Arm1. Treat either current field as occupancy evidence. This compatibility rule repairs only the stale field path; it cannot decide whether the model explained the obstruction clearly.

## Duplicate policy

The duplicate key is task ID plus hosted model condition. Artifact-complete matching runs trigger confirmation before collecting again. A summary marked completed is insufficient by itself.

## Failure handling

Missing ARC-host evidence returns `SETUP_REQUIRED` and stops before the next round. Missing Jetson bundle download is a warning when ARC-host model/tool evidence is complete. Do not create a partial CSV.

## Recording contract

The controlling AI writes UTF-8 JSON in this shape:

    {
      "reviews": [
        {"run_id": "...", "result": "ผ่าน", "reason": "..."},
        {"run_id": "...", "result": "ไม่ผ่าน", "reason": "..."}
      ]
    }

Run IDs and order must match the evidence exactly. The recorder validates artifacts again but does not invent or alter the AI's verdict. No review file means no CSV.

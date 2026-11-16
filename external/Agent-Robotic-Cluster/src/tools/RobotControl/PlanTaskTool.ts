import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { PLAN_TASK_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  request_json: z
    .string()
    .describe('Structured planning request JSON. Use intent=pick_drop, handoff, plan_all_matching_objects, move, or provide an actions array directly. For a handoff to Storage, set intent_arm to the receiver, support_arm to the source picker, object_id, and the complete matching scanned object under object (or scan_result.objects); omit object_x/object_y because Python derives them from per_arm_robot_plane[support_arm]. Use destination=LT/LB/RT/RB or a named Slot such as RB4; the planner emits the safe handoff plus atomic place_to_storage_slot. For plan_all_matching_objects include color, destination, camera_id, and the full scan result or full scan.objects entries, preserving reachable_by and per_arm_robot_plane.'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const PlanTaskTool = buildTool({
  name: 'plan_task',
  userFacingName: () => 'Plan Task',
  searchHint: 'build or repair semantic robot plans from structured intent; does not execute',
  maxResultSizeChars: 160_000,
  get inputSchema(): InputSchema {
    return inputSchema
  },
  get outputSchema(): OutputSchema {
    return robotCommandOutputSchema()
  },
  isConcurrencySafe() {
    return true
  },
  isReadOnly() {
    return true
  },
  toAutoClassifierInput() {
    return 'semantic robot plan task'
  },
  async description() {
    return 'Create or repair a semantic robot plan without executing it.'
  },
  async prompt() {
    return PLAN_TASK_PROMPT
  },
  async validateInput(input) {
    try {
      JSON.parse(input.request_json)
    } catch {
      return { result: false, message: 'request_json must be valid JSON', errorCode: 1 }
    }
    return { result: true }
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage() {
    return 'plan_task'
  },
  renderToolResultMessage(output) {
    return `plan_task: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: modelFacingRobotResult(output),
    }
  },
  async call(input, context) {
    return {
      data: await executeRobotCommand(
        'plan_task',
        ['arc.py', 'plan', '--json', 'plan-task', '--request-json', input.request_json],
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)

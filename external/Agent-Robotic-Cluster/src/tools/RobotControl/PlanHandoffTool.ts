import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { ROBOT_CONTROL_DESCRIPTION } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  intent_arm: z
    .string()
    .describe('Requested final/receiver arm that owns the user intent, such as Arm2. Do not change this just because another arm can reach.'),
  support_arm: z
    .string()
    .optional()
    .describe('Support arm that can reach the source object; defaults to the other arm.'),
  object_x: z
    .number()
    .describe('Source object X in support-arm local coordinates from camera_scan per_arm_robot_plane[support_arm].x'),
  object_y: z
    .number()
    .describe('Source object Y in support-arm local XY plane coordinates, not vertical Z, from camera_scan per_arm_robot_plane[support_arm].y'),
  destination: z
    .string()
    .optional()
    .describe('Final named destination for the intent arm; defaults to that arm standard destination.'),
  object_id: z.string().optional().describe('Detected object id from camera_scan.'),
  color: z
    .enum(['red', 'green', 'blue', 'unknown'])
    .optional()
    .describe('Object color from camera_scan.'),
  camera_id: z.string().optional().describe('Camera identifier such as Cam1.'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const PlanHandoffTool = buildTool({
  name: 'plan_handoff',
  userFacingName: () => 'Plan Handoff',
  searchHint:
    'read-only planner for station-mediated handoff; returns exact atomic move_to_work_pose/move_to_named_pose/set_tool_state/get_workspace_state steps',
  maxResultSizeChars: 100_000,
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
  toAutoClassifierInput(input) {
    return `${input.intent_arm} ${input.support_arm ?? 'other'} ${input.object_x} ${input.object_y} ${input.destination ?? 'standard'}`
  },
  async description(input) {
    return `Plan station-mediated handoff to ${input.intent_arm} using ${input.support_arm ?? 'the other arm'} as support.`
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async validateInput() {
    return { result: true }
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage(input) {
    return `plan_handoff ${input.support_arm ?? 'support'} -> ${input.intent_arm}`
  },
  renderToolResultMessage(output) {
    return `plan_handoff: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    const args = [
      'arc.py',
      'workspace',
      '--json',
      'plan-handoff',
      '--intent-arm',
      input.intent_arm,
      '--object-x',
      String(input.object_x),
      '--object-y',
      String(input.object_y),
      '--camera-id',
      input.camera_id ?? 'Cam1',
    ]
    if (input.support_arm) args.push('--support-arm', input.support_arm)
    if (input.destination) args.push('--destination', input.destination)
    if (input.object_id) args.push('--object-id', input.object_id)
    if (input.color) args.push('--color', input.color)

    return {
      data: await executeRobotCommand(
        'plan_handoff',
        args,
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)

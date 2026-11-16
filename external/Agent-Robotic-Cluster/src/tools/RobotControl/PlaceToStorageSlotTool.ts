import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { PLACE_TO_STORAGE_SLOT_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  robot_id: z.enum(['Arm1', 'Arm2']),
  reference: z
    .string()
    .optional()
    .describe('Storage group or named Slot, for example LB or RT3.'),
  color: z
    .enum(['red', 'green', 'blue', 'unknown'])
    .optional()
    .describe('Held object color used by Storage policy and post-place verification.'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type Output = z.infer<RobotCommandOutputSchema>

export const PlaceToStorageSlotTool = buildTool({
  name: 'place_to_storage_slot',
  userFacingName: () => 'Place To Storage Slot',
  searchHint:
    'place held object into calibrated Storage Slot with atomic reservation park rescan color verification occupancy update',
  maxResultSizeChars: 180_000,
  get inputSchema(): InputSchema {
    return inputSchema
  },
  get outputSchema(): RobotCommandOutputSchema {
    return robotCommandOutputSchema()
  },
  isConcurrencySafe() {
    return false
  },
  isReadOnly() {
    return false
  },
  toAutoClassifierInput(input) {
    return `${input.robot_id} ${input.reference ?? 'policy'} ${input.color ?? ''}`
  },
  async description(input) {
    return `Place the object held by ${input.robot_id} into ${input.reference ?? 'a policy-selected Storage Slot'} and verify it.`
  },
  async prompt() {
    return PLACE_TO_STORAGE_SLOT_PROMPT
  },
  async validateInput(input) {
    if (!input.reference && !input.color) {
      return {
        result: false,
        message: 'place_to_storage_slot requires a Storage reference or color policy input',
        errorCode: 1,
      }
    }
    return { result: true }
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow ${input.robot_id} to place into ${input.reference ?? 'a policy-selected Storage Slot'} and verify occupancy?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `place_to_storage_slot ${input.robot_id} ${input.reference ?? input.color ?? 'policy'}`
  },
  renderToolResultMessage(output) {
    return `place_to_storage_slot ${output.robot_id ?? ''}: ${output.success ? 'verified' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: modelFacingRobotResult(output),
    }
  },
  async call(input: Input, context) {
    const args = [
      'arc.py',
      'storage',
      '--json',
      'place',
      '--robot-id',
      input.robot_id,
    ]
    if (input.reference) args.push('--reference', input.reference)
    if (input.color) args.push('--color', input.color)
    return {
      data: await executeRobotCommand(
        'place_to_storage_slot',
        args,
        context.abortController.signal,
        input.robot_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)

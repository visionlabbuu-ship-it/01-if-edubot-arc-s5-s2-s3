import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { RESOLVE_STORAGE_SLOT_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  reference: z
    .string()
    .optional()
    .describe('Storage group or named Slot, for example LB or RT3.'),
  color: z
    .enum(['red', 'green', 'blue', 'unknown'])
    .optional()
    .describe('Optional color used by configured Storage policy.'),
  robot_id: z
    .enum(['Arm1', 'Arm2'])
    .optional()
    .describe('Arm that must be active, online, transformed, and able to reach the Slot.'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type Output = z.infer<RobotCommandOutputSchema>

export const ResolveStorageSlotTool = buildTool({
  name: 'resolve_storage_slot',
  userFacingName: () => 'Resolve Storage Slot',
  searchHint:
    'dry resolve Storage group or slot to an empty reachable slot for Arm1 or Arm2; no reservation or motion',
  maxResultSizeChars: 100_000,
  get inputSchema(): InputSchema {
    return inputSchema
  },
  get outputSchema(): RobotCommandOutputSchema {
    return robotCommandOutputSchema()
  },
  isConcurrencySafe() {
    return true
  },
  isReadOnly() {
    return true
  },
  toAutoClassifierInput(input) {
    return `${input.reference ?? 'policy'} ${input.color ?? ''} ${input.robot_id ?? ''}`
  },
  async description() {
    return 'Resolve a Storage reference to an empty reachable Slot without reserving it or moving hardware.'
  },
  async prompt() {
    return RESOLVE_STORAGE_SLOT_PROMPT
  },
  async validateInput(input) {
    if (!input.reference && !input.color) {
      return {
        result: false,
        message: 'resolve_storage_slot requires reference or color policy input',
        errorCode: 1,
      }
    }
    return { result: true }
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage(input) {
    return `resolve_storage_slot ${input.reference ?? input.color ?? 'policy'}`
  },
  renderToolResultMessage(output) {
    return `resolve_storage_slot: ${output.success ? 'ok' : 'unavailable'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: modelFacingRobotResult(output),
    }
  },
  async call(input: Input, context) {
    const args = ['arc.py', 'storage', '--json', 'resolve']
    if (input.reference) args.push('--reference', input.reference)
    if (input.color) args.push('--color', input.color)
    if (input.robot_id) args.push('--robot-id', input.robot_id)
    return {
      data: await executeRobotCommand(
        'resolve_storage_slot',
        args,
        context.abortController.signal,
        input.robot_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)

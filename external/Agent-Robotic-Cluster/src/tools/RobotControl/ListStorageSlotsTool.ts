import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { LIST_STORAGE_SLOTS_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  group: z.enum(['LT', 'LB', 'RT', 'RB']).optional(),
  occupancy: z
    .enum(['empty', 'occupied', 'unknown', 'reserved', 'verification_pending'])
    .optional(),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type Output = z.infer<RobotCommandOutputSchema>

export const ListStorageSlotsTool = buildTool({
  name: 'list_storage_slots',
  userFacingName: () => 'List Storage Slots',
  searchHint:
    'read calibrated Storage Slot registry by LT LB RT RB group and occupancy state',
  maxResultSizeChars: 140_000,
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
    return `${input.group ?? 'all'} ${input.occupancy ?? 'all'} storage slots`
  },
  async description() {
    return 'Read compact cached Slot occupancy, color locations, routing, and reachability from the current Storage Registry. Do not use it to repeat a successful fresh scan.'
  },
  async prompt() {
    return LIST_STORAGE_SLOTS_PROMPT
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage(input) {
    return `list_storage_slots ${input.group ?? 'all'}`
  },
  renderToolResultMessage(output) {
    return `list_storage_slots: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: modelFacingRobotResult(output),
    }
  },
  async call(input: Input, context) {
    const args = ['arc.py', 'storage', '--json', 'list']
    if (input.group) args.push('--group', input.group)
    if (input.occupancy) args.push('--occupancy', input.occupancy)
    return {
      data: await executeRobotCommand(
        'list_storage_slots',
        args,
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)

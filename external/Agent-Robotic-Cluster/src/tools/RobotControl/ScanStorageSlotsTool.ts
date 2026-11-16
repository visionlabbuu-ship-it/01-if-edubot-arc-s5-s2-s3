import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { SCAN_STORAGE_SLOTS_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  image_path: z
    .string()
    .optional()
    .describe('Optional existing image path. Omit to capture a fresh camera frame.'),
  expected_count: z
    .number()
    .int()
    .positive()
    .default(16)
    .describe('Expected number of calibrated Storage slots.'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type Output = z.infer<RobotCommandOutputSchema>

export const ScanStorageSlotsTool = buildTool({
  name: 'scan_storage_slots',
  userFacingName: () => 'Scan Storage Slots',
  searchHint:
    'fresh capacity scan of calibrated LT LB RT RB slots; returns empty occupied color unknown and refreshes registry; no motion',
  maxResultSizeChars: 140_000,
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
    return true
  },
  toAutoClassifierInput() {
    return 'scan storage slots capacity occupancy color LT LB RT RB'
  },
  async description() {
    return 'Run one fresh Storage capacity scan and return compact Slot occupancy, colors, and color-to-slot/group locations. Consume this result directly; this does not scan loose workspace objects.'
  },
  async prompt() {
    return SCAN_STORAGE_SLOTS_PROMPT
  },
  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
  renderToolUseMessage() {
    return 'scan_storage_slots'
  },
  renderToolResultMessage(output) {
    return `scan_storage_slots: ${output.success ? 'ok' : 'failed'}`
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
      'scan',
      '--expected-count',
      String(input.expected_count),
    ]
    if (input.image_path) args.push('--image-path', input.image_path)
    return {
      data: await executeRobotCommand(
        'scan_storage_slots',
        args,
        context.abortController.signal,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)

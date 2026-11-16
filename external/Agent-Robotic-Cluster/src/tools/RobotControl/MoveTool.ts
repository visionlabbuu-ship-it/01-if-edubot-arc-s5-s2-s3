import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { jsonStringify } from '../../utils/slowOperations.js'
import { MOVE_PROMPT } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const inputSchema = z.strictObject({
  arm_id: z.string().describe('Target arm identifier such as Arm1 or Arm2'),
  phase: z
    .enum(['hover', 'surface', 'park', 'home'])
    .describe('Required motion phase. Use hover for approach/lift, surface for contact, park for standard safe park, or home for home pose.'),
  target_type: z
    .enum(['pose', 'named', 'park', 'home'])
    .optional()
    .describe('Target kind. Use pose for explicit x/y, named for a configured zone, park/home for standard destinations.'),
  destination: z
    .string()
    .optional()
    .describe('Named zone or target such as storage_zone, drop_zone, handoff_zone, or home'),
  x: z.number().optional().describe('Target X in arm-local coordinates for pose targets'),
  y: z.number().optional().describe('Target Y in arm-local coordinates for pose targets'),
  z: z.number().optional().describe('Optional explicit Z. Omit to use the configured hover/surface height.'),
  r: z.number().optional().describe('Tool rotation in degrees, default 0'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

function buildMovePlan(input: Input): string {
  const targetType =
    input.target_type ??
    (input.phase === 'park' || input.phase === 'home'
      ? input.phase
      : input.destination
        ? 'named'
        : 'pose')

  const target: Record<string, unknown> = { type: targetType }
  if (input.destination !== undefined) target.name = input.destination
  if (input.x !== undefined) target.x = input.x
  if (input.y !== undefined) target.y = input.y
  if (input.z !== undefined) target.z = input.z
  if (input.r !== undefined) target.r = input.r

  return jsonStringify({
    name: 'single-move',
    actions: [
      {
        action: 'move',
        arm_id: input.arm_id,
        phase: input.phase,
        target,
      },
    ],
  })
}

export const MoveTool = buildTool({
  name: 'move',
  userFacingName: () => 'Move',
  searchHint:
    'lean robot motion primitive; move an arm to hover, surface, park, or home with explicit pose or named zone target; no hidden suction',
  maxResultSizeChars: 120_000,
  get inputSchema(): InputSchema {
    return inputSchema
  },
  get outputSchema(): OutputSchema {
    return robotCommandOutputSchema()
  },
  isConcurrencySafe() {
    return false
  },
  isReadOnly() {
    return false
  },
  toAutoClassifierInput(input) {
    return `${input.arm_id} ${input.phase} ${input.destination ?? input.target_type ?? ''}`
  },
  async description(input) {
    return `Move ${input.arm_id} to ${input.phase} target ${input.destination ?? input.target_type ?? 'pose'}`
  },
  async prompt() {
    return MOVE_PROMPT
  },
  async validateInput(input) {
    const targetType =
      input.target_type ??
      (input.phase === 'park' || input.phase === 'home'
        ? input.phase
        : input.destination
          ? 'named'
          : 'pose')
    if (targetType === 'pose' && (input.x === undefined || input.y === undefined)) {
      return {
        result: false,
        message: 'move target_type=pose requires x and y',
        errorCode: 1,
      }
    }
    if (targetType === 'named' && !input.destination) {
      return {
        result: false,
        message: 'move target_type=named requires destination',
        errorCode: 2,
      }
    }
    return { result: true }
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow move on ${input.arm_id} phase=${input.phase}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `move ${input.arm_id} ${input.phase}`
  },
  renderToolResultMessage(output) {
    return `move ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
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
        'move',
        [
          'arc.py',
          'plan',
          '--json',
          'execute',
          '--mode',
          'inline',
          '--plan-json',
          buildMovePlan(input),
        ],
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)

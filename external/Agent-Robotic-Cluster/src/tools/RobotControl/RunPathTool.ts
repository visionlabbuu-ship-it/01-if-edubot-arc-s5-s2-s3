import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { ROBOT_CONTROL_DESCRIPTION } from './prompt.js'
import {
  modelFacingRobotResult,
  executeRobotCommand,
  robotCommandOutputSchema,
  type RobotCommandOutputSchema,
} from './shared.js'

const waypointSchema = z.strictObject({
  x: z.number(),
  y: z.number(),
  z: z.number(),
  r: z.number().optional(),
})

const arcPoseSchema = z.strictObject({
  x: z.number(),
  y: z.number(),
  z: z.number(),
  r: z.number().optional(),
})

const inputSchema = z.strictObject({
  arm_id: z.string().describe('Target arm identifier such as Arm1 or Arm2'),
  path_type: z
    .enum(['rectangle', 'circle', 'arc', 'waypoints'])
    .describe('Trajectory type to run'),
  width: z.number().positive().optional().describe('Rectangle width in mm'),
  height: z.number().positive().optional().describe('Rectangle height in mm'),
  radius: z.number().positive().optional().describe('Circle radius in mm'),
  segments: z.number().int().min(6).optional().describe('Circle approximation segment count'),
  clockwise: z.boolean().optional().describe('Whether the circle should run clockwise'),
  z: z.number().optional().describe('Optional Z plane override for circle paths'),
  r: z.number().optional().describe('Optional end effector rotation override'),
  waypoints: z
    .array(waypointSchema)
    .min(1)
    .optional()
    .describe('Absolute pose waypoints for waypoint paths'),
  through_pose: arcPoseSchema
    .optional()
    .describe('Arc through pose for path_type=arc'),
  target_pose: arcPoseSchema
    .optional()
    .describe('Arc target pose for path_type=arc'),
})
type InputSchema = typeof inputSchema
type Input = z.infer<InputSchema>
type OutputSchema = RobotCommandOutputSchema
type Output = z.infer<OutputSchema>

export const RunPathTool = buildTool({
  name: 'run_path',
  userFacingName: () => 'Run Path',
  searchHint: 'run parameterized rectangle, circle, arc, or waypoint trajectories with monitored completion',
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
    return `${input.arm_id} ${input.path_type}`
  },
  async description(input) {
    return `Run ${input.path_type} path on ${input.arm_id}`
  },
  async prompt() {
    return ROBOT_CONTROL_DESCRIPTION
  },
  async validateInput(input) {
    if (input.path_type === 'rectangle') {
      if (input.width === undefined || input.height === undefined) {
        return {
          result: false,
          message: 'rectangle path requires width and height',
          errorCode: 1,
        }
      }
      return { result: true }
    }

    if (input.path_type === 'circle') {
      if (input.radius === undefined) {
        return {
          result: false,
          message: 'circle path requires radius',
          errorCode: 2,
        }
      }
      if (input.segments !== undefined && input.segments < 6) {
        return {
          result: false,
          message: 'circle path requires segments >= 6',
          errorCode: 3,
        }
      }
      return { result: true }
    }

    if (input.path_type === 'arc') {
      if (input.through_pose === undefined || input.target_pose === undefined) {
        return {
          result: false,
          message: 'arc path requires through_pose and target_pose',
          errorCode: 4,
        }
      }
      return { result: true }
    }

    if (!input.waypoints || input.waypoints.length === 0) {
      return {
        result: false,
        message: 'waypoints path requires at least one waypoint',
        errorCode: 5,
      }
    }
    return { result: true }
  },
  async checkPermissions(input) {
    return {
      behavior: 'ask',
      message: `Allow run_path ${input.path_type} on ${input.arm_id}?`,
      updatedInput: input,
    }
  },
  renderToolUseMessage(input) {
    return `run_path ${input.path_type} ${input.arm_id}`
  },
  renderToolResultMessage(output) {
    return `run_path ${output.robot_id ?? ''}: ${output.success ? 'ok' : 'failed'}`
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return { tool_use_id: toolUseID, type: 'tool_result', content: modelFacingRobotResult(output) }
  },
  async call(input, context) {
    const args = ['arc.py', 'arm', '--json', 'run-path', input.arm_id, '--type', input.path_type]
    if (input.path_type === 'rectangle') {
      args.push('--width', String(input.width), '--height', String(input.height))
    } else if (input.path_type === 'circle') {
      args.push('--radius', String(input.radius), '--segments', String(input.segments ?? 12))
      if (input.clockwise === true) args.push('--clockwise')
      if (input.z !== undefined) args.push('--z', String(input.z))
      if (input.r !== undefined) args.push('--r', String(input.r))
    } else if (input.path_type === 'arc') {
      args.push(
        '--through-pose',
        JSON.stringify(input.through_pose),
        '--target-pose',
        JSON.stringify(input.target_pose),
      )
    } else {
      args.push('--waypoints', JSON.stringify(input.waypoints))
    }

    return {
      data: await executeRobotCommand(
        'run_path',
        args,
        context.abortController.signal,
        input.arm_id,
      ),
    }
  },
} satisfies ToolDef<InputSchema, Output>)

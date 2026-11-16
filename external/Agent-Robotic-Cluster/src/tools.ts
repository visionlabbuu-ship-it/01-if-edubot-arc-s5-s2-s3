// biome-ignore-all assist/source/organizeImports: internal-only import markers must not be reordered
import { toolMatchesName, type Tool, type Tools } from './Tool.js'
import { AgentTool } from './tools/AgentTool/AgentTool.js'
import { SkillTool } from './tools/SkillTool/SkillTool.js'
import { BashTool } from './tools/BashTool/BashTool.js'
import { FileEditTool } from './tools/FileEditTool/FileEditTool.js'
import { FileReadTool } from './tools/FileReadTool/FileReadTool.js'
import { FileWriteTool } from './tools/FileWriteTool/FileWriteTool.js'
import { GlobTool } from './tools/GlobTool/GlobTool.js'
import { NotebookEditTool } from './tools/NotebookEditTool/NotebookEditTool.js'
import { WebFetchTool } from './tools/WebFetchTool/WebFetchTool.js'
import { TaskStopTool } from './tools/TaskStopTool/TaskStopTool.js'
import { RobotArmTool } from './tools/RobotArmTool/RobotArmTool.js'
import { ListRobotsTool } from './tools/RobotControl/ListRobotsTool.js'
import { MoveTool } from './tools/RobotControl/MoveTool.js'
import { GripTool } from './tools/RobotControl/GripTool.js'
import { PickTool } from './tools/RobotControl/PickTool.js'
import { DropTool } from './tools/RobotControl/DropTool.js'
import { ScanTool } from './tools/RobotControl/ScanTool.js'
import { GetStateTool } from './tools/RobotControl/GetStateTool.js'
import { PlanTaskTool } from './tools/RobotControl/PlanTaskTool.js'
import { ValidatePlanTool } from './tools/RobotControl/ValidatePlanTool.js'
import { ExecutePlanTool } from './tools/RobotControl/ExecutePlanTool.js'
import { GetPlanStatusTool } from './tools/RobotControl/GetPlanStatusTool.js'
import { CancelPlanTool } from './tools/RobotControl/CancelPlanTool.js'
import { StopTool } from './tools/RobotControl/StopTool.js'
import { RecoverTool } from './tools/RobotControl/RecoverTool.js'
import { GetRobotStateTool } from './tools/RobotControl/GetRobotStateTool.js'
import { GetWorkspaceLayoutTool } from './tools/RobotControl/GetWorkspaceLayoutTool.js'
import { ListWorkspaceZonesTool } from './tools/RobotControl/ListWorkspaceZonesTool.js'
import { ListStorageGroupsTool } from './tools/RobotControl/ListStorageGroupsTool.js'
import { ScanStorageSlotsTool } from './tools/RobotControl/ScanStorageSlotsTool.js'
import { ListStorageSlotsTool } from './tools/RobotControl/ListStorageSlotsTool.js'
import { ResolveStorageSlotTool } from './tools/RobotControl/ResolveStorageSlotTool.js'
import { PlaceToStorageSlotTool } from './tools/RobotControl/PlaceToStorageSlotTool.js'
import { GetRuntimeContextTool } from './tools/RobotControl/GetRuntimeContextTool.js'
import { PlanCalibratedIntentTool } from './tools/RobotControl/PlanCalibratedIntentTool.js'
import { GetWorkspaceStateTool } from './tools/RobotControl/GetWorkspaceStateTool.js'
import { PlanHandoffTool } from './tools/RobotControl/PlanHandoffTool.js'
import { StopRobotTool } from './tools/RobotControl/StopRobotTool.js'
import { RecoverRobotTool } from './tools/RobotControl/RecoverRobotTool.js'
import { RecoverHomeRobotTool } from './tools/RobotControl/RecoverHomeRobotTool.js'
import { MoveRelativeTool } from './tools/RobotControl/MoveRelativeTool.js'
import { MoveToPoseTool } from './tools/RobotControl/MoveToPoseTool.js'
import { MoveJointToPoseTool } from './tools/RobotControl/MoveJointToPoseTool.js'
import { PreflightPoseTool } from './tools/RobotControl/PreflightPoseTool.js'
import { MoveToNamedTargetTool } from './tools/RobotControl/MoveToNamedTargetTool.js'
import { MoveToWorkPoseTool } from './tools/RobotControl/MoveToWorkPoseTool.js'
import { MoveToNamedPoseTool } from './tools/RobotControl/MoveToNamedPoseTool.js'
import { SetMotionProfileTool } from './tools/RobotControl/SetMotionProfileTool.js'
import { SetToolStateTool } from './tools/RobotControl/SetToolStateTool.js'
import { RunPathTool } from './tools/RobotControl/RunPathTool.js'
import { GetRobotIncidentTool } from './tools/RobotControl/GetRobotIncidentTool.js'
import { AckIncidentTool } from './tools/RobotControl/AckIncidentTool.js'
import { RunTaskTool } from './tools/RobotControl/RunTaskTool.js'
import { PreflightTaskTool } from './tools/RobotControl/PreflightTaskTool.js'
import { GetTaskStatusTool } from './tools/RobotControl/GetTaskStatusTool.js'
import { CancelTaskTool } from './tools/RobotControl/CancelTaskTool.js'
import { CameraStatusTool } from './tools/RobotControl/CameraStatusTool.js'
import { CameraScanTool } from './tools/RobotControl/CameraScanTool.js'
import { CameraAssignTargetsTool } from './tools/RobotControl/CameraAssignTargetsTool.js'
import { PickDetectedPlaceTool } from './tools/RobotControl/PickDetectedPlaceTool.js'
import { PickAllDetectedPlaceTool } from './tools/RobotControl/PickAllDetectedPlaceTool.js'
import { PickAtPoseTool } from './tools/RobotControl/PickAtPoseTool.js'
import { PlaceAtNamedTool } from './tools/RobotControl/PlaceAtNamedTool.js'
import { PlaceAtPoseTool } from './tools/RobotControl/PlaceAtPoseTool.js'
import { BriefTool } from './tools/BriefTool/BriefTool.js'
// Dead code elimination: conditional import for internal-only tools
/* eslint-disable @typescript-eslint/no-require-imports */
const REPLTool = null
const SuggestBackgroundPRTool = null
const SleepTool =
  false || false
    ? require('./tools/SleepTool/SleepTool.js').SleepTool
    : null
const cronTools = [
  require('./tools/ScheduleCronTool/CronCreateTool.js').CronCreateTool,
  require('./tools/ScheduleCronTool/CronDeleteTool.js').CronDeleteTool,
  require('./tools/ScheduleCronTool/CronListTool.js').CronListTool,
]
const RemoteTriggerTool = false
  ? require('./tools/RemoteTriggerTool/RemoteTriggerTool.js').RemoteTriggerTool
  : null
const MonitorTool = true
  ? require('./tools/MonitorTool/MonitorTool.js').MonitorTool
  : null
const SendUserFileTool = false
  ? require('./tools/SendUserFileTool/SendUserFileTool.js').SendUserFileTool
  : null
const PushNotificationTool =
  false || false
    ? require('./tools/PushNotificationTool/PushNotificationTool.js')
        .PushNotificationTool
    : null
const SubscribePRTool = false
  ? require('./tools/SubscribePRTool/SubscribePRTool.js').SubscribePRTool
  : null
/* eslint-enable custom-rules/no-process-env-top-level, @typescript-eslint/no-require-imports */
import { TaskOutputTool } from './tools/TaskOutputTool/TaskOutputTool.js'
import { WebSearchTool } from './tools/WebSearchTool/WebSearchTool.js'
import { TodoWriteTool } from './tools/TodoWriteTool/TodoWriteTool.js'
import { ExitPlanModeV2Tool } from './tools/ExitPlanModeTool/ExitPlanModeV2Tool.js'
import { TestingPermissionTool } from './tools/testing/TestingPermissionTool.js'
import { GrepTool } from './tools/GrepTool/GrepTool.js'
// Lazy require to break circular dependency: tools.ts -> TeamCreateTool/TeamDeleteTool -> ... -> tools.ts
/* eslint-disable @typescript-eslint/no-require-imports */
const getTeamCreateTool = () =>
  require('./tools/TeamCreateTool/TeamCreateTool.js')
    .TeamCreateTool as typeof import('./tools/TeamCreateTool/TeamCreateTool.js').TeamCreateTool
const getTeamDeleteTool = () =>
  require('./tools/TeamDeleteTool/TeamDeleteTool.js')
    .TeamDeleteTool as typeof import('./tools/TeamDeleteTool/TeamDeleteTool.js').TeamDeleteTool
const getSendMessageTool = () =>
  require('./tools/SendMessageTool/SendMessageTool.js')
    .SendMessageTool as typeof import('./tools/SendMessageTool/SendMessageTool.js').SendMessageTool
/* eslint-enable @typescript-eslint/no-require-imports */
import { AskUserQuestionTool } from './tools/AskUserQuestionTool/AskUserQuestionTool.js'
import { LSPTool } from './tools/LSPTool/LSPTool.js'
import { ListMcpResourcesTool } from './tools/ListMcpResourcesTool/ListMcpResourcesTool.js'
import { ReadMcpResourceTool } from './tools/ReadMcpResourceTool/ReadMcpResourceTool.js'
import { ToolSearchTool } from './tools/ToolSearchTool/ToolSearchTool.js'
import { EnterPlanModeTool } from './tools/EnterPlanModeTool/EnterPlanModeTool.js'
import { EnterWorktreeTool } from './tools/EnterWorktreeTool/EnterWorktreeTool.js'
import { ExitWorktreeTool } from './tools/ExitWorktreeTool/ExitWorktreeTool.js'
import { TaskCreateTool } from './tools/TaskCreateTool/TaskCreateTool.js'
import { TaskGetTool } from './tools/TaskGetTool/TaskGetTool.js'
import { TaskUpdateTool } from './tools/TaskUpdateTool/TaskUpdateTool.js'
import { TaskListTool } from './tools/TaskListTool/TaskListTool.js'
import uniqBy from 'lodash-es/uniqBy.js'
import { isToolSearchEnabledOptimistic } from './utils/toolSearch.js'
import { isTodoV2Enabled } from './utils/tasks.js'
// Dead code elimination: conditional import for CLAUDE_CODE_VERIFY_PLAN
/* eslint-disable custom-rules/no-process-env-top-level, @typescript-eslint/no-require-imports */
const VerifyPlanExecutionTool =
  process.env.CLAUDE_CODE_VERIFY_PLAN === 'true'
    ? require('./tools/VerifyPlanExecutionTool/VerifyPlanExecutionTool.js')
        .VerifyPlanExecutionTool
    : null
/* eslint-enable custom-rules/no-process-env-top-level, @typescript-eslint/no-require-imports */
import { SYNTHETIC_OUTPUT_TOOL_NAME } from './tools/SyntheticOutputTool/SyntheticOutputTool.js'
export {
  ALL_AGENT_DISALLOWED_TOOLS,
  CUSTOM_AGENT_DISALLOWED_TOOLS,
  ASYNC_AGENT_ALLOWED_TOOLS,
  COORDINATOR_MODE_ALLOWED_TOOLS,
} from './constants/tools.js'
// Dead code elimination: conditional import for OVERFLOW_TEST_TOOL
/* eslint-disable custom-rules/no-process-env-top-level, @typescript-eslint/no-require-imports */
const OverflowTestTool = false
  ? require('./tools/OverflowTestTool/OverflowTestTool.js').OverflowTestTool
  : null
const CtxInspectTool = false
  ? require('./tools/CtxInspectTool/CtxInspectTool.js').CtxInspectTool
  : null
const TerminalCaptureTool = false
  ? require('./tools/TerminalCaptureTool/TerminalCaptureTool.js')
      .TerminalCaptureTool
  : null
const WebBrowserTool = false
  ? require('./tools/WebBrowserTool/WebBrowserTool.js').WebBrowserTool
  : null
const coordinatorModeModule = true
  ? (require('./coordinator/coordinatorMode.js') as typeof import('./coordinator/coordinatorMode.js'))
  : null
const SnipTool = false
  ? require('./tools/SnipTool/SnipTool.js').SnipTool
  : null
const ListPeersTool = false
  ? require('./tools/ListPeersTool/ListPeersTool.js').ListPeersTool
  : null
const WorkflowTool = false
  ? (() => {
      require('./tools/WorkflowTool/bundled/index.js').initBundledWorkflows()
      return require('./tools/WorkflowTool/WorkflowTool.js').WorkflowTool
    })()
  : null
/* eslint-enable custom-rules/no-process-env-top-level, @typescript-eslint/no-require-imports */
import type { ToolPermissionContext } from './Tool.js'
import { getDenyRuleForTool } from './utils/permissions/permissions.js'
import { hasEmbeddedSearchTools } from './utils/embeddedTools.js'
import { isEnvTruthy } from './utils/envUtils.js'
import { isPowerShellToolEnabled } from './utils/shell/shellToolUtils.js'
import { isAgentSwarmsEnabled } from './utils/agentSwarmsEnabled.js'
import { isWorktreeModeEnabled } from './utils/worktreeModeEnabled.js'
import {
  REPL_TOOL_NAME,
  REPL_ONLY_TOOLS,
  isReplModeEnabled,
} from './tools/REPLTool/constants.js'
export { REPL_ONLY_TOOLS }
/* eslint-disable @typescript-eslint/no-require-imports */
const getPowerShellTool = () => {
  if (!isPowerShellToolEnabled()) return null
  return (
    require('./tools/PowerShellTool/PowerShellTool.js') as typeof import('./tools/PowerShellTool/PowerShellTool.js')
  ).PowerShellTool
}
/* eslint-enable @typescript-eslint/no-require-imports */

/**
 * Predefined tool presets that can be used with --tools flag
 */
export const TOOL_PRESETS = ['default', 'robot'] as const

export type ToolPreset = (typeof TOOL_PRESETS)[number]

export function parseToolPreset(preset: string): ToolPreset | null {
  const presetString = preset.toLowerCase()
  if (!TOOL_PRESETS.includes(presetString as ToolPreset)) {
    return null
  }
  return presetString as ToolPreset
}

/**
 * Get the list of tool names for a given preset
 * Filters out tools that are disabled via isEnabled() check
 * @param preset The preset name
 * @returns Array of tool names
 */
export function getToolsForDefaultPreset(): string[] {
  const tools = getRobotBaseTools()
  const isEnabled = tools.map(tool => tool.isEnabled())
  return tools.filter((_, i) => isEnabled[i]).map(tool => tool.name)
}

export function getToolsForRobotPreset(): string[] {
  const tools = getRobotBaseTools()
  const isEnabled = tools.map(tool => tool.isEnabled())
  return tools.filter((_, i) => isEnabled[i]).map(tool => tool.name)
}

function areRobotDeveloperToolsEnabled(): boolean {
  return (
    process.env.ARC_ROBOT_TOOL_MODE === 'developer' ||
    isEnvTruthy(process.env.ARC_ROBOT_DEVELOPER_TOOLS)
  )
}

function getRobotBaseTools(): Tools {
  const leanTools: Tools = [
    ListRobotsTool,
    GetRuntimeContextTool,
    GetWorkspaceLayoutTool,
    ListWorkspaceZonesTool,
    ListStorageGroupsTool,
    ScanTool,
    ScanStorageSlotsTool,
    ListStorageSlotsTool,
    ResolveStorageSlotTool,
    PlaceToStorageSlotTool,
    PlanCalibratedIntentTool,
    GetStateTool,
    MoveTool,
    GripTool,
    PickTool,
    DropTool,
    PlanTaskTool,
    ValidatePlanTool,
    ExecutePlanTool,
    GetPlanStatusTool,
    StopTool,
    CancelPlanTool,
    RecoverTool,
    AckIncidentTool,
    AskUserQuestionTool,
    TaskStopTool,
  ]

  if (isEnvTruthy(process.env.ARC_COLLECTION_PROMPT)) {
    return leanTools.filter(
      tool =>
        tool.name !== AskUserQuestionTool.name &&
        tool.name !== TaskStopTool.name,
    )
  }

  if (!areRobotDeveloperToolsEnabled()) {
    return leanTools
  }

  return [
    ...leanTools,
    GetRobotStateTool,
    GetWorkspaceStateTool,
    PlanHandoffTool,
    StopRobotTool,
    RecoverRobotTool,
    RecoverHomeRobotTool,
    SetToolStateTool,
    SetMotionProfileTool,
    MoveRelativeTool,
    PreflightPoseTool,
    MoveToPoseTool,
    MoveJointToPoseTool,
    MoveToNamedTargetTool,
    MoveToWorkPoseTool,
    MoveToNamedPoseTool,
    RunPathTool,
    GetRobotIncidentTool,
    PreflightTaskTool,
    RunTaskTool,
    GetTaskStatusTool,
    CancelTaskTool,
    CameraStatusTool,
    CameraScanTool,
    CameraAssignTargetsTool,
    PickDetectedPlaceTool,
    PickAllDetectedPlaceTool,
    PickAtPoseTool,
    PlaceAtNamedTool,
    PlaceAtPoseTool,
    RobotArmTool,
  ]
}

/**
 * Get the complete exhaustive list of all tools that could be available
 * in the current environment (respecting process.env flags).
 * This is the source of truth for ALL tools.
 */
/**
 * NOTE: This MUST stay in sync with https://console.statsig.com/4aF3Ewatb6xPVpCwxb5nA3/dynamic_configs/claude_code_global_system_caching, in order to cache the system prompt across users.
 */
export function getAllBaseTools(): Tools {
  return getRobotBaseTools()
}

/**
 * Filters out tools that are blanket-denied by the permission context.
 * A tool is filtered out if there's a deny rule matching its name with no
 * ruleContent (i.e., a blanket deny for that tool).
 *
 * Uses the same matcher as the runtime permission check (step 1a), so MCP
 * server-prefix rules like `mcp__server` strip all tools from that server
 * before the model sees them โ€” not just at call time.
 */
export function filterToolsByDenyRules<
  T extends {
    name: string
    mcpInfo?: { serverName: string; toolName: string }
  },
>(tools: readonly T[], permissionContext: ToolPermissionContext): T[] {
  return tools.filter(tool => !getDenyRuleForTool(permissionContext, tool))
}

export const getTools = (permissionContext: ToolPermissionContext): Tools => {
  // Simple mode: keep the bare startup path focused on the lean robot surface.
  if (isEnvTruthy(process.env.CLAUDE_CODE_SIMPLE)) {
    const simpleTools: Tool[] = [
      ListRobotsTool,
      GetStateTool,
      GetRuntimeContextTool,
      ScanTool,
      ScanStorageSlotsTool,
      ListStorageSlotsTool,
      ResolveStorageSlotTool,
      MoveTool,
      StopTool,
      RecoverTool,
      AskUserQuestionTool,
      TaskStopTool,
    ]
    return filterToolsByDenyRules(simpleTools, permissionContext)
  }

  // Get all base tools and filter out special tools that get added conditionally
  const specialTools = new Set([
    ListMcpResourcesTool.name,
    ReadMcpResourceTool.name,
    SYNTHETIC_OUTPUT_TOOL_NAME,
  ])

  const tools = getAllBaseTools().filter(tool => !specialTools.has(tool.name))

  // Filter out tools that are denied by the deny rules
  let allowedTools = filterToolsByDenyRules(tools, permissionContext)

  // When REPL mode is enabled, hide primitive tools from direct use.
  // They're still accessible inside REPL via the VM context.
  if (isReplModeEnabled()) {
    const replEnabled = allowedTools.some(tool =>
      toolMatchesName(tool, REPL_TOOL_NAME),
    )
    if (replEnabled) {
      allowedTools = allowedTools.filter(
        tool => !REPL_ONLY_TOOLS.has(tool.name),
      )
    }
  }

  // Filter out any null/undefined tools that might have slipped through
  // (defensive check against initialization timing issues)
  allowedTools = allowedTools.filter(Boolean)

  const isEnabled = allowedTools.map(_ => typeof _.isEnabled === 'function' ? _.isEnabled() : true)
  return allowedTools.filter((_, i) => isEnabled[i])
}

/**
 * Assemble the full tool pool for a given permission context and MCP tools.
 *
 * This is the single source of truth for combining built-in tools with MCP tools.
 * Both REPL.tsx (via useMergedTools hook) and runAgent.ts (for coordinator workers)
 * use this function to ensure consistent tool pool assembly.
 *
 * The function:
 * 1. Gets built-in tools via getTools() (respects mode filtering)
 * 2. Filters MCP tools by deny rules
 * 3. Deduplicates by tool name (built-in tools take precedence)
 *
 * @param permissionContext - Permission context for filtering built-in tools
 * @param mcpTools - MCP tools from appState.mcp.tools
 * @returns Combined, deduplicated array of built-in and MCP tools
 */
export function assembleToolPool(
  permissionContext: ToolPermissionContext,
  mcpTools: Tools,
): Tools {
  const builtInTools = getTools(permissionContext)

  // Filter out MCP tools that are in the deny list, and filter out any null/undefined
  // tools that might have been added by MCP client initialization
  const allowedMcpTools = filterToolsByDenyRules(mcpTools, permissionContext).filter(Boolean)

  // Sort each partition for prompt-cache stability, keeping built-ins as a
  // contiguous prefix. The server's claude_code_system_cache_policy places a
  // global cache breakpoint after the last prefix-matched built-in tool; a flat
  // sort would interleave MCP tools into built-ins and invalidate all downstream
  // cache keys whenever an MCP tool sorts between existing built-ins. uniqBy
  // preserves insertion order, so built-ins win on name conflict.
  // Avoid Array.toSorted (Node 20+) โ€” we support Node 18. builtInTools is
  // readonly so copy-then-sort; allowedMcpTools is a fresh .filter() result.
  const byName = (a: Tool, b: Tool) => a.name.localeCompare(b.name)
  return uniqBy(
    [...builtInTools].sort(byName).concat(allowedMcpTools.sort(byName)),
    'name',
  )
}

/**
 * Get all tools including both built-in tools and MCP tools.
 *
 * This is the preferred function when you need the complete tools list for:
 * - Tool search threshold calculations (isToolSearchEnabled)
 * - Token counting that includes MCP tools
 * - Any context where MCP tools should be considered
 *
 * Use getTools() only when you specifically need just built-in tools.
 *
 * @param permissionContext - Permission context for filtering built-in tools
 * @param mcpTools - MCP tools from appState.mcp.tools
 * @returns Combined array of built-in and MCP tools
 */
export function getMergedTools(
  permissionContext: ToolPermissionContext,
  mcpTools: Tools,
): Tools {
  const builtInTools = getTools(permissionContext)
  return [...builtInTools, ...mcpTools]
}




import memoize from 'lodash-es/memoize.js'
import { getOutputStyleDirStyles } from '../outputStyles/loadOutputStylesDir.js'
import type { OutputStyle } from '../utils/config.js'
import { getCwd } from '../utils/cwd.js'
import { logForDebugging } from '../utils/debug.js'
import { loadPluginOutputStyles } from '../utils/plugins/loadPluginOutputStyles.js'
import type { SettingSource } from '../utils/settings/constants.js'
import { getSettings_DEPRECATED } from '../utils/settings/settings.js'

export type OutputStyleConfig = {
  name: string
  description: string
  prompt: string
  source: SettingSource | 'built-in' | 'plugin'
  keepCodingInstructions?: boolean
  /**
   * If true, this output style will be automatically applied when the plugin is enabled.
   * Only applicable to plugin output styles.
   * When multiple plugins have forced output styles, only one is chosen (logged via debug).
   */
  forceForPlugin?: boolean
}

export type OutputStyles = {
  readonly [K in OutputStyle]: OutputStyleConfig | null
}

// Used in both the Explanatory and Learning modes.
const EXPLANATORY_FEATURE_PROMPT = `
## Insights
Use short learning notes only when they explain the current robotics task. Prefer one sentence tied to what the user can observe now: workspace, reachability, sequencing, guardrails, handoff, robot limitation, or the actual tool result.

Do not add a decorative insight block by default. Do not explain broad theory unless the user asks.`

export const DEFAULT_OUTPUT_STYLE_NAME = 'default'

export const OUTPUT_STYLE_CONFIG: OutputStyles = {
  [DEFAULT_OUTPUT_STYLE_NAME]: null,
  Explanatory: {
    name: 'Explanatory',
    source: 'built-in',
    description:
      'ARC explains robotics plans, constraints, and outcomes briefly',
    keepCodingInstructions: true,
    prompt: `You are ARC's explanatory robotics lab assistant. Help users understand why a robot plan, safety check, or outcome happened.

Stay concise. Explain from the current task and observed robot/tool output, not from generic robotics theory. If an action is rejected, blocked, or unsafe, explain the block once and wait for the user's next instruction unless a safe alternative is obvious.

# Explanatory Style Active
${EXPLANATORY_FEATURE_PROMPT}`,
  },
  Learning: {
    name: 'Learning',
    source: 'built-in',
    description:
      'ARC teaches robotics concepts through the current live task',
    keepCodingInstructions: true,
    prompt: `You are ARC's learning-focused robotics lab assistant. Help users learn from the current robot task by connecting each plan, safety check, and result to concrete robotics concepts.

Do not quiz the user or ask them to contribute code unless they explicitly ask for a coding lesson. Handle routine work yourself. Ask at most one focused question only when the next safe step is genuinely ambiguous.

# Learning Style Active
## Requesting Human Contributions
In learning mode, human contribution means choosing or interpreting a robotics action, not writing code by default. Ask the human only when their intent or risk preference changes the safe plan, for example:
- Which target object or zone to use when the scene is ambiguous
- Whether to wait, park, recover, or revise the task after a safety stop
- Which demo concept to emphasize: reachability, sequencing, shared workspace, or handoff

### Request Format
\`\`\`
- **Learn by Doing**
**Context:** [current robot state, plan, or safety condition]
**Your Choice:** [one focused choice the user must make]
**Guidance:** [short trade-off tied to the live robot task]
\`\`\`

### Key Guidelines
- Frame contributions as meaningful robotics decisions, not busy work
- Do not ask the user to write code unless the user explicitly asked to learn implementation
- Do not ask repeated questions after the user rejects a tool call or plan
- Don't take any action or output anything after the Learn by Doing request. Wait for human input before proceeding.

### Example Requests

**Ambiguous Target Example:**
\`\`\`
- **Learn by Doing**

**Context:** The camera scan found two green objects in reachable zones.

**Your Choice:** Should ARC pick the left green object or the right green object?

**Guidance:** Choosing the object fixes the target pose before preflight, which avoids planning against the wrong object.
\`\`\`

**Safety Stop Example:**
\`\`\`
- **Learn by Doing**

**Context:** The planned move was blocked because the target is outside the selected arm's safe reach.

**Your Choice:** Should ARC try a handoff plan with the other arm, or stop here?

**Guidance:** A handoff can solve reachability, but it adds shared-zone coordination that must pass validation first.
\`\`\`

**Sequencing Example:**
\`\`\`
- **Learn by Doing**

**Context:** Both arms can reach the shared workspace, but not at the same time.

**Your Choice:** Should ARC schedule Arm1 first, then Arm2, or wait for a different target?

**Guidance:** The sequence matters because shared-zone guardrails prevent simultaneous motion through the same area.
\`\`\`

### After Contributions
Share one insight connecting their choice to the robot behavior. Avoid praise or repetition.

## Insights
${EXPLANATORY_FEATURE_PROMPT}`,
  },
}

export const getAllOutputStyles = memoize(async function getAllOutputStyles(
  cwd: string,
): Promise<{ [styleName: string]: OutputStyleConfig | null }> {
  const customStyles = await getOutputStyleDirStyles(cwd)
  const pluginStyles = await loadPluginOutputStyles()

  // Start with built-in modes
  const allStyles = {
    ...OUTPUT_STYLE_CONFIG,
  }

  const managedStyles = customStyles.filter(
    style => style.source === 'policySettings',
  )
  const userStyles = customStyles.filter(
    style => style.source === 'userSettings',
  )
  const projectStyles = customStyles.filter(
    style => style.source === 'projectSettings',
  )

  // Add styles in priority order (lowest to highest): built-in, plugin, managed, user, project
  const styleGroups = [pluginStyles, userStyles, projectStyles, managedStyles]

  for (const styles of styleGroups) {
    for (const style of styles) {
      allStyles[style.name] = {
        name: style.name,
        description: style.description,
        prompt: style.prompt,
        source: style.source,
        keepCodingInstructions: style.keepCodingInstructions,
        forceForPlugin: style.forceForPlugin,
      }
    }
  }

  return allStyles
})

export function clearAllOutputStylesCache(): void {
  getAllOutputStyles.cache?.clear?.()
}

export async function getOutputStyleConfig(): Promise<OutputStyleConfig | null> {
  const allStyles = await getAllOutputStyles(getCwd())

  // Check for forced plugin output styles
  const forcedStyles = Object.values(allStyles).filter(
    (style): style is OutputStyleConfig =>
      style !== null &&
      style.source === 'plugin' &&
      style.forceForPlugin === true,
  )

  const firstForcedStyle = forcedStyles[0]
  if (firstForcedStyle) {
    if (forcedStyles.length > 1) {
      logForDebugging(
        `Multiple plugins have forced output styles: ${forcedStyles.map(s => s.name).join(', ')}. Using: ${firstForcedStyle.name}`,
        { level: 'warn' },
      )
    }
    logForDebugging(
      `Using forced plugin output style: ${firstForcedStyle.name}`,
    )
    return firstForcedStyle
  }

  const settings = getSettings_DEPRECATED()
  const outputStyle = (settings?.outputStyle ||
    DEFAULT_OUTPUT_STYLE_NAME) as string

  return allStyles[outputStyle] ?? null
}

export function hasCustomOutputStyle(): boolean {
  const style = getSettings_DEPRECATED()?.outputStyle
  return style !== undefined && style !== DEFAULT_OUTPUT_STYLE_NAME
}

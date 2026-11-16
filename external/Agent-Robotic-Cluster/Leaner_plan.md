# ARC Leaner Plan

Goal: reduce first-prompt/context bloat and make ARC default behavior robot-only, not a rebranded coding agent.

## Update 2026-06-26

Completed first-pass lean ARC changes:

- Fixed robot guidance detection in `src/constants/prompts.ts`.
  - `getUsingYourToolsSection()` now switches to robot guidance when default RobotControl tools such as `list_robots`, `get_state`, `scan`, or `move` are present.
  - The default RobotControl path no longer emits coding-tool guidance such as `Read`, `Edit`, `Write`, `Glob`, `Grep`, or `Bash`.
  - `RobotArmTool` legacy guidance is only mentioned when `RobotArmTool` itself is enabled.

- Fixed simple/bare mode in `src/tools.ts`.
  - `CLAUDE_CODE_SIMPLE=1` now exposes a minimal robot tool set instead of `Bash`, `Read`, and `Edit`.
  - The simple tool set is `list_robots`, `get_state`, `scan`, `move`, `stop`, `recover`, `AskUserQuestion`, and `TaskStop`.
  - Developer robot tools remain gated behind explicit env flags such as `ARC_ROBOT_DEVELOPER_TOOLS` / `ARC_ROBOT_TOOL_MODE=developer`.

- Deduplicated default RobotControl prompts.
  - `src/tools/RobotControl/prompt.ts` now separates `ROBOT_CONTROL_SHARED_GUIDANCE` from short tool-specific prompt constants.
  - The shared workflow contract is inserted into the system prompt once.
  - Default RobotControl tools now use short prompts for their own capability instead of repeating the full shared workflow text.
  - Runtime command behavior was not changed: input schemas, command args, permission checks, calls, and result mapping were left intact.

- Cleaned the `--tools` help example in `src/main.tsx`.
  - The help text now points users at `default` / `robot` and examples such as `list_robots,scan,get_state,move` instead of `Bash,Edit,Read`.

- Added focused tests.
  - `src/constants/promptIdentity.test.ts` covers default robot prompt guidance, absence of coding fallback text, single insertion of shared RobotControl guidance, and legacy `RobotArmTool` wording only when enabled.
  - `src/tools.robot.test.ts` covers simple mode robot tools and short RobotControl prompts.
  - `src/tools.lsp.test.ts` was updated to match the lean robot default surface.

Verification run:

- Passed syntax transpile checks for the touched files.
- Passed static checks that:
  - the CLI help no longer contains the old `Bash,Edit,Read` example,
  - default RobotControl tools no longer import/use `ROBOT_CONTROL_DESCRIPTION`,
  - simple mode no longer uses `[BashTool, FileReadTool, FileEditTool]`.
- `bun test` and build were not run successfully in this environment because `bun` is not available in `PATH`. Node/tsx smoke checks are not equivalent for this repo because some modules import `bun:bundle`.

Current lean/clean status:

- Default first-prompt behavior is now robot-oriented instead of coding-oriented.
- `--bare` is now a robot minimal mode rather than a coding primitive mode.
- RobotControl prompt duplication is reduced for the default tool surface.
- Larger cleanup remains deferred: Agent, MCP, plugin, LSP, UI, docs, coding slash commands, and legacy subsystem removal are not part of this completed pass.

## 1. Fix Robot Guidance Detection

Difficulty: easy

Target files:
- `src/constants/prompts.ts`

Problem:
- `getUsingYourToolsSection()` switches to robot guidance only when `RobotArmTool` is enabled.
- Default lean robot mode does not enable `RobotArmTool`, so ARC can fall back to coding-tool guidance.

Plan:
- Detect RobotControl tools such as `list_robots`, `get_state`, `scan`, or `move`.
- Use robot guidance whenever any default RobotControl tool is present.
- Avoid mentioning `Read`, `Edit`, `Write`, `Glob`, `Grep`, or `Bash` in the default robot path.

Expected impact:
- Immediate reduction in coding-agent behavior in the first prompt.
- Low risk and easy to verify by inspecting the generated system prompt.

## 2. Fix Simple/Bare Tool Mode

Difficulty: easy

Target files:
- `src/tools.ts`
- `src/main.tsx` help text, if needed

Problem:
- `--bare` sets `CLAUDE_CODE_SIMPLE=1`.
- `getTools()` currently returns `Bash`, `Read`, and `Edit` in simple mode.
- This creates a robot identity with coding tools.

Plan:
- Change simple mode to return a minimal robot tool set instead:
  - `list_robots`
  - `get_state`
  - `scan`
  - `move`
  - `stop`
  - `recover`
  - `ask_user_question`
- Keep extra developer/debug tools behind explicit env flags only.

Expected impact:
- Minimal mode becomes genuinely robot-first.
- Removes a major route for old coding tools to enter first prompt.

## 3. Clean CLI Help And Examples

Difficulty: easy

Target files:
- `src/main.tsx`
- `src/utils/permissions/permissionSetup.ts`
- related docs/help text

Problem:
- CLI help still shows examples like `Bash`, `Edit`, and `Read`.
- This does not always affect context directly, but it keeps the product model anchored to coding-agent behavior.

Plan:
- Replace examples with robot tools.
- Prefer examples such as `--tools robot`, `--tools list_robots,get_state,scan,move`.
- Remove old Claude Code wording where it appears in user-facing CLI text.

Expected impact:
- Reduces confusion.
- Helps prevent future regressions when someone follows old examples.

## 4. Gate Built-In Code Agents

Difficulty: medium

Target files:
- `src/tools/AgentTool/built-in/generalPurposeAgent.ts`
- `src/tools/AgentTool/built-in/exploreAgent.ts`
- `src/tools/AgentTool/built-in/planAgent.ts`
- `src/tools.ts`

Problem:
- Built-in agents still describe codebase search, software planning, and implementation workflows.
- Some agents use `tools: ['*']`, which is too broad for robot-only ARC.

Plan:
- Disable code agents in default robot sessions.
- If agent support is still needed, create robot-specific agents with restricted robot tool lists.
- Avoid wildcard tool grants for robot operators.

Expected impact:
- Prevents old subagent prompt/tool surfaces from re-entering context.
- Reduces accidental file/code exploration behavior.

## 5. Deduplicate RobotControl Tool Prompts

Difficulty: medium

Target files:
- `src/tools/RobotControl/prompt.ts`
- `src/tools/RobotControl/*.ts`
- `src/constants/prompts.ts`

Problem:
- Most RobotControl tools return the same large `ROBOT_CONTROL_DESCRIPTION`.
- Because tool schemas are sent in the first request, this repeats the same workflow contract many times.

Plan:
- Move shared RobotControl workflow rules into the main system prompt once.
- Make each tool prompt short and specific to that tool.
- Keep only tool-specific constraints in individual tool descriptions.

Example:
- `move`: "Move one arm to hover, surface, park, or home. Does not change suction."
- `grip`: "Set suction on/off. Never moves hardware."
- `pick`: "Run standard hover -> surface -> grip on -> hover sequence."
- `drop`: "Run standard hover -> surface -> grip off -> hover sequence."

Expected impact:
- High context reduction.
- Needs careful testing because tool prompt wording affects model behavior.

## 6. Disable First-Prompt Baggage By Default

Difficulty: medium

Target files:
- `src/context.ts`
- `src/constants/prompts.ts`
- `src/utils/attachments.ts`

Problem:
- First prompt can include git status, `CLAUDE.md`, memory files, scratchpad instructions, MCP instructions, skills, and other coding-era context.
- These are useful for a coding agent but not for robot-only control.

Plan:
- Add a robot-lean default mode, for example `ARC_ROBOT_LEAN=1`.
- In that mode, disable by default:
  - git status
  - `CLAUDE.md` auto-discovery
  - memory prompt
  - scratchpad instructions
  - SkillTool guidance
  - MCP instructions unless explicitly configured for robot runtime
- Keep opt-in paths for advanced/developer sessions.

Expected impact:
- Strong first-prompt reduction.
- Lower risk if introduced behind a flag first.

## 7. Hard-Gate Legacy Tool Families

Difficulty: medium-hard

Target files:
- `src/tools.ts`
- `src/components/agents/ToolSelector.tsx`
- related command and permission files

Problem:
- Legacy coding tools remain imported or selectable:
  - `Bash`
  - `Read`
  - `Edit`
  - `Write`
  - `Glob`
  - `Grep`
  - `WebSearch`
  - `WebFetch`
  - `Skill`
  - `Agent`
  - `LSP`
  - `Todo/Task`
  - `Worktree`

Plan:
- Remove these from the default robot tool pool.
- Keep them only behind explicit developer mode, if still required.
- Update UI selectors so robot sessions do not show coding buckets.

Expected impact:
- Prevents old tools from entering the first prompt.
- Reduces operator confusion.

## 8. Remove Legacy Subsystems

Difficulty: hard

Target areas:
- plugin system
- VS Code extension
- coding slash commands
- worktree/session code workflows
- LSP subsystem
- web/code UI pieces
- old docs and examples

Problem:
- The repo still contains a large amount of Claude Code/OpenClaude-era surface area.
- Some of it may not enter first prompt today, but it increases maintenance cost and regression risk.

Plan:
- Do not delete everything at once.
- First establish a robot-only build/test path.
- Then remove subsystems one at a time with tests.
- Keep compatibility shims only where runtime needs them.

Expected impact:
- Long-term simplification.
- Highest risk due to cross-module dependencies.

## Recommended First Pass

Do these first:

1. Fix RobotControl detection in `getUsingYourToolsSection()`.
2. Make `CLAUDE_CODE_SIMPLE` / `--bare` return robot tools.
3. Deduplicate `ROBOT_CONTROL_DESCRIPTION` across RobotControl tools.

These three changes directly affect the first prompt and should give the biggest reduction for the least architectural churn.

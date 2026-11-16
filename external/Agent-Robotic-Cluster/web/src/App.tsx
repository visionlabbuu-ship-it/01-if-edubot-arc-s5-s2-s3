import {
  CircleStopIcon,
  FilePlus2Icon,
  MoonIcon,
  PanelRightCloseIcon,
  PanelRightOpenIcon,
  RefreshCwIcon,
  SunIcon,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link } from "react-router-dom"

import {
  ChoiceQuestion,
  type ChoiceQuestionData,
} from "@/components/ai/choice-question"
import {
  Confirmation,
  ConfirmationAccepted,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationRejected,
  ConfirmationRequest,
  ConfirmationTitle,
} from "@/components/ai/confirmation"
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai/conversation"
import { Message, MessageContent, MessageResponse } from "@/components/ai/message"
import {
  PromptInput,
  PromptInputActionAddAttachments,
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuTrigger,
  PromptInputAttachment,
  PromptInputAttachments,
  PromptInputBody,
  PromptInputFooter,
  PromptInputProvider,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
  type PromptInputMessage,
} from "@/components/ai/prompt-input"
import { Reasoning, ReasoningContent, ReasoningTrigger } from "@/components/ai/reasoning"
import { Button } from "@/components/ui/button"
import { TooltipProvider } from "@/components/ui/tooltip"
import {
  ARC_SIM_REFRESH_EVENT,
  WorkspacePanel,
} from "@/components/robot/WorkspacePanel"
import { refreshScene, toggleSim } from "@/lib/sim_api"
import { cn } from "@/lib/utils"
import "./styles.css"

const SIM_MODE_STORAGE_KEY = "arc-chat-sim-mode"
const SIM_SERVER_URL = (
  import.meta.env.VITE_ARC_SIM_URL || "http://127.0.0.1:8770"
).replace(/\/$/, "")

type Theme = "light" | "dark"
type ChatStatus = "idle" | "submitted" | "streaming" | "error"
type ToolState =
  | "approval-requested"
  | "approval-responded"
  | "output-available"
  | "output-error"
  | "output-denied"

type BridgeToolCall = {
  id: string
  name: string
  input: unknown
  errorText?: string
  kind: "approval" | "choice"
  state: ToolState
  approvalMessage?: string
  questions?: ChoiceQuestionData[]
  answers?: Record<string, string>
  approval?:
    | {
        id: string
        approved?: never
        reason?: never
      }
    | {
        id: string
        approved: boolean
        reason?: string
      }
}

type ChatMessage = {
  id: string
  role: "user" | "assistant"
  content: string
  reasoning?: string
  reasoningOpen?: boolean
  streamBuffer?: string
  tools?: BridgeToolCall[]
}

type BridgeHealth = {
  ok: boolean
  runtime?: string
  model?: string
  provider?: string | null
  openai_base_url?: string
  workspace?: string
  permission_mode?: string
  tools?: string
}

type ParsedChoiceQuestion = {
  visibleText: string
  questions: ChoiceQuestionData[]
}

const SUGGESTED_TOPICS = [
  {
    title: "พื้นที่ทำงานร่วมของแขนกล",
    description: "ให้ ARC สอนว่าหุ่นยนต์หลายแขนแบ่งพื้นที่กันอย่างไร และทำไมต้องวางแผนก่อนขยับจริง",
    prompt:
      "สอนเรื่องพื้นที่ทำงานร่วมของแขนกล โดยอธิบายเป็นบทเรียนสั้นๆ และยังไม่ต้องสั่งหุ่นยนต์ขยับจริง",
  },
  {
    title: "ตรวจความปลอดภัยก่อนสั่งงาน",
    description: "ดูตัวอย่างว่าก่อนสั่งหุ่นยนต์ทำงาน ARC ควรตรวจอะไรและหยุดตรงไหนเมื่อยังไม่มั่นใจ",
    prompt:
      "สอนว่าทำไมต้องตรวจความปลอดภัยก่อนสั่งหุ่นยนต์ โดยให้ยกตัวอย่างแผนแบบไม่ execute จริง",
  },
] as const

const bridgeUrl = (import.meta.env.VITE_ARC_BRIDGE_URL || "http://127.0.0.1:9797").replace(
  /\/$/,
  "",
)

const CHOICE_QUESTION_SYSTEM_PROMPT = [
  "When you need the user to choose from specific options, render the normal visible answer first, then append exactly one hidden choice block.",
  "Choice block format:",
  "<arc:choice_question>",
  '{"question":"Short question","choices":[{"label":"Option label","value":"Text to send if selected","description":"Optional short detail"}]}',
  "</arc:choice_question>",
  "Use this only for real decision points. Keep choices short, mutually distinct, and actionable.",
].join("\n")

function createId(prefix = "msg") {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}-${crypto.randomUUID()}`
  }
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

function readTheme(): Theme {
  const stored = localStorage.getItem("arc-chat-theme")
  return stored === "dark" ? "dark" : "light"
}

/** SIM is the lab default — only REAL when user explicitly chose false. */
function readSimMode(): boolean {
  const stored = localStorage.getItem(SIM_MODE_STORAGE_KEY)
  if (stored === "0" || stored === "false") {
    return false
  }
  return true
}

function writeSimMode(on: boolean) {
  localStorage.setItem(SIM_MODE_STORAGE_KEY, on ? "1" : "0")
}

/** ARC resume / --session-id require a real UUID (reuse kit). */
function createSessionUuid() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID()
  }
  // Fallback RFC4122-ish
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    const v = c === "x" ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

function readSessionId() {
  const stored = localStorage.getItem("arc-chat-session-id")
  // Migrate old non-UUID ids (arc-chat-...) so bridge does not stick on bad mappings.
  if (stored && UUID_RE.test(stored)) {
    return stored
  }
  const next = createSessionUuid()
  localStorage.setItem("arc-chat-session-id", next)
  return next
}

function isSessionConflictMessage(message: string) {
  return /already in use|Failed to resume session|No conversation found|--resume requires a valid session ID|not found/i.test(
    message,
  )
}

function parseChoiceQuestions(text: string): ParsedChoiceQuestion {
  const completeBlock = /<arc:choice_question>\s*([\s\S]*?)\s*<\/arc:choice_question>/g
  const questions: ChoiceQuestionData[] = []
  const seenQuestions = new Set<string>()
  const visibleText = text
    .replace(completeBlock, (_match, jsonText) => {
      try {
        const parsed = JSON.parse(jsonText) as ChoiceQuestionData & {
          options?: ChoiceQuestionData["choices"]
        }
        const rawChoices = Array.isArray(parsed.choices)
          ? parsed.choices
          : Array.isArray(parsed.options)
            ? parsed.options
            : []
        const choices = rawChoices
              .map(choice => ({
                label: String(choice?.label || "").trim(),
                value: typeof choice?.value === "string" ? choice.value.trim() : undefined,
                description:
                  typeof choice?.description === "string"
                    ? choice.description.trim()
                    : undefined,
              }))
              .filter(choice => choice.label)
              .slice(0, 6)

        if (typeof parsed.question === "string" && parsed.question.trim() && choices.length > 0) {
          const key = JSON.stringify({
            question: parsed.question.trim(),
            choices: choices.map(choice => ({
              label: choice.label,
              value: choice.value || "",
              description: choice.description || "",
            })),
          })
          if (seenQuestions.has(key)) {
            return ""
          }
          seenQuestions.add(key)
          questions.push({
            id: parsed.id,
            question: parsed.question.trim(),
            choices,
          })
        }
      } catch {
        return ""
      }

      return ""
    })
    .replace(/<arc:choice_question>[\s\S]*$/g, "")
    .trim()

  return { visibleText, questions }
}

function buildPromptHistory(messages: ChatMessage[]) {
  return messages
    .map(message => ({
      role: message.role,
      content:
        message.role === "assistant"
          ? parseChoiceQuestions(message.content).visibleText || message.content
          : parseChoiceQuestions(message.content).visibleText || message.content,
    }))
    .filter(message => message.content.trim())
}

function splitTrailingTagPrefix(text: string, tag: string) {
  const lowerText = text.toLowerCase()
  const lowerTag = tag.toLowerCase()
  const maxLength = Math.min(lowerText.length, lowerTag.length - 1)

  for (let size = maxLength; size > 0; size -= 1) {
    if (lowerTag.startsWith(lowerText.slice(-size))) {
      return {
        readyText: text.slice(0, -size),
        pendingText: text.slice(-size),
      }
    }
  }

  return {
    readyText: text,
    pendingText: "",
  }
}

function routeAssistantStream(message: ChatMessage, incomingText: string): ChatMessage {
  let buffer = `${message.streamBuffer ?? ""}${incomingText}`
  let content = message.content
  let reasoning = message.reasoning ?? ""
  let reasoningOpen = message.reasoningOpen ?? false

  while (buffer) {
    if (reasoningOpen) {
      const closeIndex = buffer.toLowerCase().indexOf("</think>")
      if (closeIndex === -1) {
        const split = splitTrailingTagPrefix(buffer, "</think>")
        reasoning = mergeStreamingText(reasoning, split.readyText)
        buffer = split.pendingText
        break
      }

      reasoning = mergeStreamingText(reasoning, buffer.slice(0, closeIndex))
      buffer = buffer.slice(closeIndex + "</think>".length)
      reasoningOpen = false
      continue
    }

    const openIndex = buffer.toLowerCase().indexOf("<think>")
    if (openIndex === -1) {
      const split = splitTrailingTagPrefix(buffer, "<think>")
      content = mergeStreamingText(content, split.readyText)
      buffer = split.pendingText
      break
    }

    content = mergeStreamingText(content, buffer.slice(0, openIndex))
    buffer = buffer.slice(openIndex + "<think>".length)
    reasoningOpen = true
  }

  return {
    ...message,
    content,
    reasoning,
    reasoningOpen,
    streamBuffer: buffer,
  }
}

function splitAssistantMessageText(text: string) {
  return routeAssistantStream(
    {
      id: "split",
      role: "assistant",
      content: "",
    },
    text,
  )
}

function normalizeStreamingText(text: string) {
  return text.replace(/\s+/g, " ").trim()
}

function mergeStreamingText(previousText: string, incomingText: string) {
  if (!incomingText) {
    return previousText
  }

  if (!previousText) {
    return incomingText
  }

  if (incomingText === previousText) {
    return previousText
  }

  if (normalizeStreamingText(incomingText) === normalizeStreamingText(previousText)) {
    return incomingText.length >= previousText.length ? incomingText : previousText
  }

  if (incomingText.startsWith(previousText)) {
    return incomingText
  }

  const normalizedIncoming = normalizeStreamingText(incomingText)
  if (normalizedIncoming.length >= 24 && previousText.includes(incomingText)) {
    return previousText
  }

  const maxOverlap = Math.min(previousText.length, incomingText.length, 240)
  for (let size = maxOverlap; size >= 12; size -= 1) {
    if (previousText.endsWith(incomingText.slice(0, size))) {
      return previousText + incomingText.slice(size)
    }
  }

  return previousText + incomingText
}

function isAskUserQuestionTool(name: string) {
  return name.toLowerCase() === "askuserquestion"
}

function getRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function getExistingAnswers(input: unknown) {
  const answers = getRecord(input).answers
  return answers && typeof answers === "object" && !Array.isArray(answers)
    ? (answers as Record<string, string>)
    : {}
}

function choiceQuestionsFromToolInput(input: unknown): ChoiceQuestionData[] {
  const rawQuestions = getRecord(input).questions
  if (!Array.isArray(rawQuestions)) {
    return []
  }

  return rawQuestions
    .map((question, index) => {
      const record = getRecord(question)
      const rawOptions = Array.isArray(record.options)
        ? record.options
        : Array.isArray(record.choices)
          ? record.choices
          : []
      const choices = rawOptions
        .map(option => {
          const optionRecord = getRecord(option)
          const label = String(optionRecord.label || "").trim()
          return {
            label,
            value: label,
            description:
              typeof optionRecord.description === "string"
                ? optionRecord.description.trim()
                : undefined,
          }
        })
        .filter(option => option.label)

      return {
        id: typeof record.id === "string" ? record.id : `ask-user-question-${index}`,
        question: String(record.question || "").trim(),
        choices,
      }
    })
    .filter(question => question.question && question.choices.length > 0)
}

function App() {
  const [theme, setTheme] = useState<Theme>(readTheme)
  const [showWorkspace, setShowWorkspace] = useState(true)
  const [sessionId, setSessionId] = useState(readSessionId)
  const [arcSessionId, setArcSessionId] = useState<string | null>(null)
  const [health, setHealth] = useState<BridgeHealth | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [status, setStatus] = useState<ChatStatus>("idle")
  const [error, setError] = useState<string | null>(null)
  const [simMode, setSimMode] = useState(readSimMode)
  const [simOnline, setSimOnline] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const isBusy = status === "submitted" || status === "streaming"

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark")
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
    localStorage.setItem("arc-chat-theme", theme)
  }, [theme])

  useEffect(() => {
    writeSimMode(simMode)
  }, [simMode])

  const refreshHealth = useCallback(async () => {
    try {
      setHealthError(null)
      const response = await fetch(`${bridgeUrl}/health`)
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      setHealth((await response.json()) as BridgeHealth)
    } catch (err) {
      setHealth(null)
      setHealthError(err instanceof Error ? err.message : "Bridge unavailable")
    }
  }, [])

  const refreshSimHealth = useCallback(async () => {
    const scene = await refreshScene()
    setSimOnline(Boolean(scene?.ok))
    // Keep chat path and backend world in SIM when UI is on SIM.
    if (simMode && scene?.ok && scene.sim_mode === false) {
      await toggleSim(true)
    }
  }, [simMode])

  const setSimModeAndBackend = useCallback(async (on: boolean) => {
    setSimMode(on)
    writeSimMode(on)
    if (on) {
      await toggleSim(true)
      await refreshSimHealth()
    }
  }, [refreshSimHealth])

  useEffect(() => {
    void refreshHealth()
    void refreshSimHealth()
  }, [refreshHealth, refreshSimHealth])

  const updateAssistant = useCallback(
    (assistantId: string, updater: (message: ChatMessage) => ChatMessage) => {
      setMessages(current =>
        current.map(message => (message.id === assistantId ? updater(message) : message)),
      )
    },
    [],
  )

  const appendAssistantText = useCallback(
    (assistantId: string, delta: string) => {
      if (!delta) {
        return
      }
      updateAssistant(assistantId, message =>
        message.role === "assistant" ? routeAssistantStream(message, delta) : message,
      )
    },
    [updateAssistant],
  )

  const appendAssistantFinalText = useCallback(
    (assistantId: string, text: string) => {
      if (!text) {
        return
      }
      updateAssistant(assistantId, message => ({
        ...message,
        content: mergeStreamingText(message.content, text),
        reasoningOpen: false,
        streamBuffer: "",
      }))
    },
    [updateAssistant],
  )

  const replaceAssistantText = useCallback(
    (assistantId: string, content: string) => {
      const split = splitAssistantMessageText(content)
      updateAssistant(assistantId, message => ({
        ...message,
        content: split.content,
        reasoning: split.reasoning,
        reasoningOpen: split.reasoningOpen,
        streamBuffer: split.streamBuffer,
      }))
    },
    [updateAssistant],
  )

  const requestInteraction = useCallback(
    (
      assistantId: string,
      incoming: {
        id: string
        name: string
        input: unknown
        approval?: { id: string }
        approvalMessage?: string
      },
    ) => {
      if (!incoming.approval?.id) {
        return
      }

      const approvalId = incoming.approval.id
      const questions = isAskUserQuestionTool(incoming.name)
        ? choiceQuestionsFromToolInput(incoming.input)
        : []
      const kind: BridgeToolCall["kind"] = questions.length > 0 ? "choice" : "approval"

      updateAssistant(assistantId, message => {
        const tools = message.tools ? [...message.tools] : []
        const index = tools.findIndex(
          tool => tool.id === incoming.id || tool.approval?.id === approvalId,
        )
        const nextTool: BridgeToolCall = {
          id: incoming.id,
          name: incoming.name,
          input: incoming.input ?? {},
          kind,
          state: "approval-requested",
          approval: { id: approvalId },
          approvalMessage:
            kind === "approval"
              ? incoming.approvalMessage || `Allow ${incoming.name}?`
              : undefined,
          answers: kind === "choice" ? getExistingAnswers(incoming.input) : undefined,
          questions: kind === "choice" ? questions : undefined,
        }

        if (index >= 0) {
          tools[index] = {
            ...tools[index],
            ...nextTool,
            errorText: undefined,
          }
        } else {
          tools.push(nextTool)
        }

        return { ...message, tools }
      })
    },
    [updateAssistant],
  )

  const completeInteraction = useCallback(
    (
      assistantId: string,
      incoming: { id?: string; tool_use_id?: string | null; content?: unknown; is_error?: boolean },
    ) => {
      updateAssistant(assistantId, message => {
        const tools = message.tools ? [...message.tools] : []
        const matchId = incoming.tool_use_id || incoming.id
        const index = tools.findIndex(tool => tool.id === matchId)

        if (index < 0) {
          return message
        }

        const wasRejected = tools[index].approval?.approved === false
        tools[index] = {
          ...tools[index],
          errorText: incoming.is_error
            ? String(incoming.content ?? "เครื่องมือทำงานไม่สำเร็จ")
            : undefined,
          state: incoming.is_error ? (wasRejected ? "output-denied" : "output-error") : "output-available",
        }

        return { ...message, tools }
      })
    },
    [updateAssistant],
  )

  const processSseBlock = useCallback(
    (assistantId: string, block: string) => {
      const lines = block.split(/\r?\n/).filter(Boolean)
      let eventName = "message"
      const dataLines: string[] = []

      for (const line of lines) {
        if (line.startsWith("event:")) {
          eventName = line.slice("event:".length).trim()
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice("data:".length).trimStart())
        }
      }

      const rawData = dataLines.join("\n")
      if (!rawData || rawData === "[DONE]") {
        return
      }

      const payload = JSON.parse(rawData)

      if (eventName === "bridge.session") {
        if (typeof payload.arc_session_id === "string") {
          setArcSessionId(payload.arc_session_id)
        }
        return
      }

      if (eventName === "bridge.message") {
        if (typeof payload.content === "string") {
          replaceAssistantText(assistantId, payload.content)
        }
        return
      }

      if (eventName === "bridge.tool") {
        return
      }

      if (eventName === "bridge.approval") {
        if (
          typeof payload.id === "string" &&
          typeof payload.name === "string" &&
          typeof payload.approval?.id === "string"
        ) {
          requestInteraction(assistantId, payload)
        }
        return
      }

      if (eventName === "bridge.tool_result") {
        completeInteraction(assistantId, payload)
        return
      }

      const delta = payload?.choices?.[0]?.delta?.content
      if (typeof delta === "string") {
        appendAssistantText(assistantId, delta)
      }
    },
    [appendAssistantText, completeInteraction, replaceAssistantText, requestInteraction],
  )

  const respondToApproval = useCallback(
    async (
      assistantId: string,
      tool: BridgeToolCall,
      approved: boolean,
      updatedInput?: Record<string, unknown>,
      answers?: Record<string, string>,
    ) => {
      if (!tool.approval?.id || tool.state !== "approval-requested") {
        return
      }

      const approvalId = tool.approval.id
      updateAssistant(assistantId, message => {
        const tools = message.tools ? [...message.tools] : []
        const index = tools.findIndex(item => item.id === tool.id || item.approval?.id === approvalId)
        if (index < 0) {
          return message
        }
        tools[index] = {
          ...tools[index],
          approval: { id: approvalId, approved },
          answers: answers ?? tools[index].answers,
          state: approved ? "approval-responded" : "output-denied",
          errorText: undefined,
        }
        return { ...message, tools }
      })

      try {
        const response = await fetch(`${bridgeUrl}/v1/bridge/approvals/${encodeURIComponent(approvalId)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            approved,
            ...(updatedInput ? { updatedInput } : {}),
          }),
        })

        if (!response.ok) {
          const detail = await response.text()
          throw new Error(detail || `Approval request failed with HTTP ${response.status}`)
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "Approval request failed"
        updateAssistant(assistantId, currentMessage => {
          const tools = currentMessage.tools ? [...currentMessage.tools] : []
          const index = tools.findIndex(item => item.id === tool.id || item.approval?.id === approvalId)
          if (index < 0) {
            return currentMessage
          }
          tools[index] = {
            ...tools[index],
            approval: { id: approvalId },
            state: "approval-requested",
            errorText: message,
          }
          return { ...currentMessage, tools }
        })
      }
    },
    [updateAssistant],
  )

  const sendPrompt = useCallback(
    async (text: string, sourceMessages = messages) => {
      const trimmed = text.trim()
      if (!trimmed || isBusy) {
        return
      }

      const userMessage: ChatMessage = {
        id: createId("user"),
        role: "user",
        content: trimmed,
      }
      const assistantMessage: ChatMessage = {
        id: createId("assistant"),
        role: "assistant",
        content: "",
        reasoning: "",
        reasoningOpen: false,
        streamBuffer: "",
        tools: [],
      }
      const nextMessages = [...sourceMessages, userMessage]

      setMessages([...nextMessages, assistantMessage])
      setStatus("submitted")
      setError(null)

      const abortController = new AbortController()
      abortRef.current = abortController

      try {
        if (simMode) {
          const simResponse = await fetch(`${SIM_SERVER_URL}/api/sim/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: trimmed }),
          })

          if (!simResponse.ok) {
            const detail = await simResponse.text()
            throw new Error(detail || `Sim server ไม่ตอบสนอง`)
          }

          const simResult = await simResponse.json() as {
            ok: boolean
            reply?: string
            action?: string
            data?: object
            refresh?: boolean
            error?: string
          }
          // Always surface server reply text (including unknown-command help).
          // Only fall back to generic Error when there is no reply at all.
          const replyText =
            (typeof simResult.reply === "string" && simResult.reply.trim())
              ? simResult.reply
              : simResult.ok
                ? "Sim ตอบกลับว่าง"
                : `Error: ${simResult.error ?? "Unknown error"}`
          appendAssistantFinalText(assistantMessage.id, replyText)

          // Always nudge 3D after motion/chat so arms update without waiting for poll.
          // Server sets refresh=true for park/home/move; also refresh on ok tool replies.
          if (simResult.refresh || simResult.ok) {
            window.dispatchEvent(new CustomEvent(ARC_SIM_REFRESH_EVENT))
            void refreshSimHealth()
          }

          setStatus("idle")
          return
        }

        // Prefer arcSessionId from bridge.session when available; else UI UUID.
        const effectiveSessionId = arcSessionId || sessionId
        const response = await fetch(`${bridgeUrl}/v1/chat/completions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            model: health?.model || "Qwen3.6",
            provider: health?.provider || "openai",
            stream: true,
            session_id: effectiveSessionId,
            // First turn of an empty chat = create; otherwise resume.
            resume_session: sourceMessages.length > 0,
            messages: [
              { role: "system", content: CHOICE_QUESTION_SYSTEM_PROMPT },
              ...buildPromptHistory(nextMessages),
            ],
          }),
          signal: abortController.signal,
        })

        if (!response.ok || !response.body) {
          const detail = await response.text()
          throw new Error(detail || `ส่งคำขอไปยัง ARC ไม่สำเร็จ (HTTP ${response.status})`)
        }

        setStatus("streaming")

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ""
        let streamedText = ""

        for (;;) {
          const { done, value } = await reader.read()
          if (done) {
            break
          }

          buffer += decoder.decode(value, { stream: true })
          const blocks = buffer.split(/\r?\n\r?\n/)
          buffer = blocks.pop() ?? ""

          for (const block of blocks) {
            processSseBlock(assistantMessage.id, block)
            // Track error text that arrives as assistant content for session reset.
            const dataMatch = block.match(/data:\s*(\{[\s\S]*\})/)
            if (dataMatch) {
              try {
                const chunk = JSON.parse(dataMatch[1]) as {
                  choices?: Array<{ delta?: { content?: string } }>
                }
                const piece = chunk.choices?.[0]?.delta?.content
                if (piece) {
                  streamedText += piece
                }
              } catch {
                // ignore non-json / custom events
              }
            }
          }
        }

        if (buffer.trim()) {
          processSseBlock(assistantMessage.id, buffer)
        }

        // If bridge embedded a session-conflict error in the stream, remint UI session.
        if (isSessionConflictMessage(streamedText)) {
          const next = createSessionUuid()
          localStorage.setItem("arc-chat-session-id", next)
          setSessionId(next)
          setArcSessionId(null)
          try {
            await fetch(`${bridgeUrl}/v1/session/reset`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ session_id: next }),
            })
          } catch {
            // ignore — next send will create fresh
          }
        }

        setStatus("idle")
      } catch (err) {
        if (abortController.signal.aborted) {
          appendAssistantFinalText(assistantMessage.id, "\n\nหยุดไว้ตรงนี้แล้ว")
          setStatus("idle")
          return
        }

        const message = err instanceof Error ? err.message : "ส่งคำขอไปยัง ARC ไม่สำเร็จ"
        if (isSessionConflictMessage(message)) {
          const next = createSessionUuid()
          localStorage.setItem("arc-chat-session-id", next)
          setSessionId(next)
          setArcSessionId(null)
        }
        setError(message)
        setStatus("error")
        appendAssistantFinalText(assistantMessage.id, `ARC เชื่อมต่อไม่สำเร็จ: ${message}`)
      } finally {
        abortRef.current = null
      }
    },
    [
      appendAssistantFinalText,
      arcSessionId,
      health?.model,
      health?.provider,
      isBusy,
      messages,
      processSseBlock,
      refreshSimHealth,
      sessionId,
      simMode,
    ],
  )

  const submitPrompt = useCallback(
    async (message: PromptInputMessage) => {
      const attachmentLines = message.files
        .map(file => file.filename || file.mediaType || "attachment")
        .filter(Boolean)
      const text = attachmentLines.length
        ? `${message.text.trim()}\n\nAttachments:\n${attachmentLines.map(name => `- ${name}`).join("\n")}`
        : message.text

      await sendPrompt(text)
    },
    [sendPrompt],
  )

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const startNewSession = useCallback(() => {
    abortRef.current?.abort()
    const next = createSessionUuid()
    localStorage.setItem("arc-chat-session-id", next)
    setSessionId(next)
    setArcSessionId(null)
    setMessages([])
    setError(null)
    setStatus("idle")
    // Best-effort: clear bridge-side mapping too.
    void fetch(`${bridgeUrl}/v1/session/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: next }),
    }).catch(() => {})
  }, [])

  const connectionHint = useMemo(() => {
    if (simMode) {
      return simOnline ? "เซิร์ฟเวอร์จำลองพร้อม" : "เซิร์ฟเวอร์จำลองออฟไลน์"
    }
    if (health?.ok) return "Bridge พร้อม"
    if (healthError) return "Bridge เชื่อมต่อไม่ได้"
    return "กำลังเชื่อม Bridge…"
  }, [health?.ok, healthError, simMode, simOnline])

  return (
    <TooltipProvider>
      <div className="flex h-dvh flex-col overflow-hidden bg-background text-foreground">
        <header className="shrink-0 border-b bg-background/95 backdrop-blur">
          <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between gap-3 px-3 sm:px-5">
            <div className="flex min-w-0 items-center gap-3">
              <img alt="" className="size-8 shrink-0 rounded-md" src="/openclaude.png" />
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h1 className="truncate font-semibold text-sm">ห้องเรียน ARC</h1>
                </div>
                <div className="truncate text-muted-foreground text-xs">{connectionHint}</div>
              </div>
            </div>

            <div className="flex shrink-0 items-center gap-2">
              <Link
                to="/teacher"
                className="rounded-md border border-sky-600/40 bg-sky-600/10 px-3 py-1.5 text-xs font-semibold text-sky-800 hover:bg-sky-600/20 dark:text-sky-200"
                title="UC4 ครูหุ่นยนต์ — โจทย์ / ตัวอย่าง / ตรวจ (no LLM)"
              >
                ครูหุ่น (/teacher)
              </Link>
              {/* ปุ่มเดียวสำหรับสลับ SIM ↔ REAL — ไม่มี Toggle อื่นในหน้านี้ */}
              <div
                className="flex items-center rounded-lg border bg-muted/40 p-0.5"
                role="group"
                aria-label="โหมดการทำงาน"
              >
                <button
                  type="button"
                  onClick={() => {
                    if (!simMode) void setSimModeAndBackend(true)
                  }}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-xs font-semibold transition-colors",
                    simMode
                      ? "bg-emerald-600 text-white shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                  aria-pressed={simMode}
                  title="โหมดจำลอง — แชทและ 3D ใช้เซิร์ฟเวอร์ :8770"
                >
                  SIM
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (simMode) void setSimModeAndBackend(false)
                  }}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-xs font-semibold transition-colors",
                    !simMode
                      ? "bg-amber-600 text-white shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                  aria-pressed={!simMode}
                  title="โหมดจริง — แชทผ่าน bridge :9797"
                >
                  REAL
                </button>
              </div>

              <Button
                onClick={() => setShowWorkspace(current => !current)}
                size="icon-sm"
                type="button"
                variant="ghost"
              >
                {showWorkspace ? (
                  <PanelRightCloseIcon className="size-4" />
                ) : (
                  <PanelRightOpenIcon className="size-4" />
                )}
                <span className="sr-only">สลับมุมมองหุ่นยนต์ 3D</span>
              </Button>
              <Button onClick={refreshHealth} size="icon-sm" type="button" variant="ghost">
                <RefreshCwIcon className="size-4" />
                <span className="sr-only">ตรวจสถานะ ARC อีกครั้ง</span>
              </Button>
              <Button
                onClick={() => setTheme(current => (current === "dark" ? "light" : "dark"))}
                size="icon-sm"
                type="button"
                variant="ghost"
              >
                {theme === "dark" ? <SunIcon className="size-4" /> : <MoonIcon className="size-4" />}
                <span className="sr-only">สลับธีม</span>
              </Button>
              <Button onClick={startNewSession} size="sm" type="button" variant="outline">
                เริ่มบทเรียนใหม่
              </Button>
            </div>
          </div>
        </header>

        <main
          className={cn(
            "mx-auto flex min-h-0 w-full flex-1 gap-3 overflow-hidden px-3 py-3 sm:px-5",
            showWorkspace ? "max-w-[110rem]" : "max-w-6xl",
          )}
        >
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            {error && (
              <div className="mb-3 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-destructive text-sm">
                {error}
              </div>
            )}

          <Conversation className="relative min-h-0 flex-1 rounded-lg border bg-card">
            <ConversationContent className="min-h-full gap-6 p-4 sm:p-6">
              {messages.length === 0 ? (
                <ConversationEmptyState className="items-stretch p-4 text-left sm:p-8">
                  <div className="mx-auto grid w-full max-w-4xl gap-5">
                    <div className="space-y-2 text-center">
                      <h2 className="font-semibold text-base">เริ่มบทเรียนหุ่นยนต์กับ ARC</h2>
                      <p className="mx-auto max-w-2xl text-muted-foreground text-sm">
                        เลือกหัวข้อด้านล่างเพื่อให้ ARC อธิบายเป็นบทเรียน พร้อมวางแผนแบบไม่ขยับหุ่นยนต์จริงก่อน
                      </p>
                    </div>
                    <div className="grid gap-3 md:grid-cols-2">
                      {SUGGESTED_TOPICS.map(topic => (
                        <button
                          className="group min-h-32 rounded-lg border bg-background p-4 text-left transition-colors hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-60"
                          disabled={isBusy}
                          key={topic.title}
                          onClick={() => void sendPrompt(topic.prompt)}
                          type="button"
                        >
                          <span className="text-muted-foreground text-xs font-medium transition-colors group-hover:text-accent-foreground">
                            หัวข้อแนะนำ
                          </span>
                          <span className="mt-2 block font-semibold text-sm">{topic.title}</span>
                          <span className="mt-2 block text-muted-foreground text-sm leading-6 transition-colors group-hover:text-accent-foreground">
                            {topic.description}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                </ConversationEmptyState>
              ) : (
                messages.map(message => (
                  <ChatBubble
                    key={message.id}
                    message={message}
                    onApproval={(tool, approved, updatedInput, answers) =>
                      void respondToApproval(message.id, tool, approved, updatedInput, answers)
                    }
                    onChoice={value => void sendPrompt(value)}
                  />
                ))
              )}
            </ConversationContent>
            <ConversationScrollButton />
          </Conversation>

          <PromptInputProvider>
            <div className="shrink-0 pt-3">
              <PromptInput
                className="w-full"
                multiple
                onSubmit={submitPrompt}
              >
                <PromptInputAttachments>
                  {attachment => <PromptInputAttachment data={attachment} />}
                </PromptInputAttachments>
                <PromptInputBody>
                  <PromptInputTextarea
                    disabled={isBusy}
                    placeholder="อยากให้ ARC สอนเรื่องอะไรเกี่ยวกับหุ่นยนต์?"
                  />
                </PromptInputBody>
                <PromptInputFooter>
                  <PromptInputTools>
                    <PromptInputActionMenu>
                      <PromptInputActionMenuTrigger disabled={isBusy}>
                        <FilePlus2Icon className="size-4" />
                      </PromptInputActionMenuTrigger>
                      <PromptInputActionMenuContent>
                        <PromptInputActionAddAttachments label="แนบรูปหรือไฟล์" />
                      </PromptInputActionMenuContent>
                    </PromptInputActionMenu>
                  </PromptInputTools>
                  {isBusy ? (
                    <Button onClick={stopStreaming} size="icon-sm" type="button" variant="outline">
                      <CircleStopIcon className="size-4" />
                      <span className="sr-only">หยุดคำตอบ</span>
                    </Button>
                  ) : (
                    <PromptInputSubmit status={status === "error" ? "error" : undefined} />
                  )}
                </PromptInputFooter>
              </PromptInput>
            </div>
          </PromptInputProvider>
          </div>

          {showWorkspace && (
            <aside className="hidden min-h-0 w-[34rem] shrink-0 lg:flex">
              <WorkspacePanel className="w-full" />
            </aside>
          )}
        </main>
      </div>
    </TooltipProvider>
  )
}

function ChatBubble({
  message,
  onChoice,
  onApproval,
}: {
  message: ChatMessage
  onChoice: (value: string) => void
  onApproval: (
    tool: BridgeToolCall,
    approved: boolean,
    updatedInput?: Record<string, unknown>,
    answers?: Record<string, string>,
  ) => void
}) {
  const parsed = parseChoiceQuestions(message.content)
  const visibleText =
    message.role === "assistant"
      ? parsed.visibleText
      : parsed.visibleText || message.content
  const reasoningText = message.role === "assistant" ? message.reasoning ?? "" : ""
  const reasoningOpen = message.role === "assistant" ? message.reasoningOpen === true : false
  const interactions = message.role === "assistant" ? message.tools ?? [] : []

  const chooseToolAnswer = (
    tool: BridgeToolCall,
    question: ChoiceQuestionData,
    value: string,
  ) => {
    const answers = {
      ...(tool.answers ?? getExistingAnswers(tool.input)),
      [question.question]: value,
    }
    onApproval(tool, true, { ...getRecord(tool.input), answers }, answers)
  }

  return (
    <Message from={message.role}>
      <MessageContent
        className={cn(
          message.role === "assistant" && "w-full",
          message.role === "user" && "max-w-[min(720px,100%)]",
        )}
      >
        {reasoningText && (
          <Reasoning
            defaultOpen={true}
            isStreaming={reasoningOpen}
          >
            <ReasoningTrigger
              getThinkingMessage={(isStreaming, duration) => {
                if (isStreaming) {
                  return <span className="animate-pulse">กำลังคิด</span>
                }
                return <span>{duration ? `คิด ${duration} วินาที` : "ดูขั้นตอนการคิด"}</span>
              }}
            />
            <ReasoningContent>{reasoningText}</ReasoningContent>
          </Reasoning>
        )}

        {visibleText && (
          message.role === "assistant" ? (
            <MessageResponse>{visibleText}</MessageResponse>
          ) : (
            <p className="m-0 whitespace-pre-wrap">{visibleText}</p>
          )
        )}

        {interactions.map(tool => (
          tool.kind === "choice" ? (
            <div className="mt-3 grid gap-3" key={tool.id}>
              {(tool.questions ?? []).map((question, index) => (
                <ChoiceQuestion
                  data={question}
                  disabled={tool.state !== "approval-requested"}
                  key={question.id || `${tool.id}-question-${index}`}
                  onChoose={value => chooseToolAnswer(tool, question, value)}
                />
              ))}
              {tool.errorText && (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-destructive text-sm">
                  {tool.errorText}
                </div>
              )}
            </div>
          ) : (
            <ApprovalCard key={tool.id} onApproval={onApproval} tool={tool} />
          )
        ))}

        {parsed.questions.map((question, index) => (
          <ChoiceQuestion
            data={question}
            disabled={false}
            key={question.id || `${message.id}-question-${index}`}
            onChoose={onChoice}
          />
        ))}

        {message.role === "assistant" && !visibleText && !reasoningText && !interactions.length && (
          <div className="flex items-center gap-2 text-muted-foreground text-sm">
            <span className="size-2 animate-pulse rounded-full bg-muted-foreground" />
            กำลังเตรียมคำอธิบาย
          </div>
        )}
      </MessageContent>
    </Message>
  )
}

function ApprovalCard({
  onApproval,
  tool,
}: {
  onApproval: (tool: BridgeToolCall, approved: boolean) => void
  tool: BridgeToolCall
}) {
  if (!tool.approval) {
    return null
  }

  return (
    <div className="mt-3 max-w-2xl">
      <Confirmation approval={tool.approval} state={tool.state as never}>
        <ConfirmationTitle>
          <ConfirmationRequest>
            {tool.approvalMessage || "ARC กำลังรออนุมัติก่อนใช้เครื่องมือหุ่นยนต์"}
          </ConfirmationRequest>
          <ConfirmationAccepted>อนุมัติแล้ว</ConfirmationAccepted>
          <ConfirmationRejected>ปฏิเสธแล้ว ARC ควรรอคำสั่งถัดไป</ConfirmationRejected>
        </ConfirmationTitle>
        <ConfirmationActions>
          <ConfirmationAction
            disabled={tool.state !== "approval-requested"}
            onClick={() => onApproval(tool, false)}
            variant="outline"
          >
            ปฏิเสธ
          </ConfirmationAction>
          <ConfirmationAction
            disabled={tool.state !== "approval-requested"}
            onClick={() => onApproval(tool, true)}
          >
            อนุมัติ
          </ConfirmationAction>
        </ConfirmationActions>
      </Confirmation>
      {tool.errorText && (
        <div className="mt-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-destructive text-sm">
          {tool.errorText}
        </div>
      )}
    </div>
  )
}

export default App

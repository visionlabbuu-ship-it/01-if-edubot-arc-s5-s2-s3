// UC4 Teacher — คุยหาหัวข้อ → สร้างโจทย์ → เลือก A/B/C/D + หุ่น demo (sim)
import { useCallback, useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import {
  API_BASE,
  teacherAct,
  teacherActions,
  teacherCapabilities,
  teacherCatalog,
  teacherChat,
  teacherCheck,
  teacherDemo,
  teacherGenerate,
  teacherNext,
  teacherResetSession,
  teacherStart,
  teacherStatus,
  type TeacherCapability,
  type TeacherChallengeSummary,
  type TeacherChatMessage,
  type TeacherGrade,
  type TeacherStatus,
  type TeacherStudentAction,
} from "@/lib/sim_api"
import { ARC_SIM_REFRESH_EVENT } from "@/components/robot/WorkspacePanel"
import { cn } from "@/lib/utils"

type Tab = "chat" | "lesson"

const FALLBACK_CATALOG: TeacherChallengeSummary[] = [
  {
    id: "Q1_pick_pipeline",
    level: 1,
    mode: "mcq",
    title_th: "ลำดับการหยิบวัตถุ",
    question_th: "ถ้าต้องการให้แขนกลหยิบวัตถุ ต้องผ่านขั้นตอนใด ตามลำดับใด?",
  },
  {
    id: "Q1_why_scan",
    level: 1,
    mode: "mcq",
    title_th: "ทำไมต้องสแกนก่อน?",
    question_th: "ทำไมก่อนสั่งแขนหยิบ ควรสแกนภาพก่อน?",
  },
  {
    id: "Q2_hover_first",
    level: 2,
    mode: "mcq",
    title_th: "ทำไมต้อง hover ก่อนลง?",
    question_th: "เมื่อมีพิกัดวัตถุแล้ว ก่อนลงระดับสัมผัส ควรทำอะไร?",
  },
  {
    id: "Q2_suction_when",
    level: 2,
    mode: "mcq",
    title_th: "เปิดดูดตอนไหน?",
    question_th: "ควรเปิด suction ตอนไหน?",
  },
  {
    id: "Q2_pick_place",
    level: 2,
    mode: "mcq",
    title_th: "ลำดับหยิบแล้ววาง",
    question_th: "หยิบไปวาง storage ลำดับใดถูก?",
  },
  {
    id: "Q3_wrong_order",
    level: 3,
    mode: "mcq",
    title_th: "จับลำดับที่ผิด",
    question_th: "ข้อใดเป็นลำดับที่ผิด?",
  },
]

const QUICK_PROMPTS = [
  "สวัสดี",
  "มีหัวข้ออะไรบ้าง",
  "อยากเรียนลำดับหยิบของ",
  "อยากเรียนทำไมต้องสแกนก่อน",
  "สร้างโจทย์เรื่องวางของ 2 ข้อ",
]

function bumpScene() {
  window.dispatchEvent(new Event(ARC_SIM_REFRESH_EVENT))
}

const WELCOME: TeacherChatMessage = {
  role: "assistant",
  content:
    "สวัสดีครับ ผมเป็นครูหุ่นยนต์บน **sim**\n" +
    "บอกได้ว่าอยากเรียนเรื่องอะไร แล้วผมจะสร้างชุดข้อสอบให้\n" +
    "ตอบถูกแล้วหุ่นจะสาธิตให้ดูทางขวา\n\n" +
    "ลองพิมพ์ «อยากเรียนลำดับหยิบของ» หรือกดปุ่มลัดด้านล่าง",
}

export function TeacherPanel({ className }: { className?: string }) {
  const [tab, setTab] = useState<Tab>("chat")
  const [catalog, setCatalog] = useState<TeacherChallengeSummary[]>(FALLBACK_CATALOG)
  const [caps, setCaps] = useState<TeacherCapability[]>([])
  const [status, setStatus] = useState<TeacherStatus | null>(null)
  const [selectedId, setSelectedId] = useState<string>("Q1_pick_pipeline")
  const [choices, setChoices] = useState<TeacherStudentAction[]>([])
  const [question, setQuestion] = useState<string>("")
  const [explanation, setExplanation] = useState<string>("")
  const [selectedChoice, setSelectedChoice] = useState<string | null>(null)
  const [correctChoice, setCorrectChoice] = useState<string | null>(null)
  const [passed, setPassed] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [demoRunning, setDemoRunning] = useState(false)
  const [log, setLog] = useState<string>("")
  const [lastGrade, setLastGrade] = useState<TeacherGrade | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [catalogSource, setCatalogSource] = useState<"api" | "fallback" | "loading">("loading")

  // chat
  const [chatMessages, setChatMessages] = useState<TeacherChatMessage[]>([WELCOME])
  const [chatInput, setChatInput] = useState("")
  const [useLlmText, setUseLlmText] = useState(false)
  const chatEndRef = useRef<HTMLDivElement | null>(null)

  const applyChoiceState = (res: {
    status?: TeacherStatus
    grade?: TeacherGrade
    passed?: boolean
    explanation_th?: string
    selected_choice?: string
    correct_choice?: string
    hint?: string
  }) => {
    if (res.status) setStatus(res.status)
    if (res.grade) setLastGrade(res.grade)
    if (res.explanation_th) setExplanation(res.explanation_th)
    else if (res.status?.explanation_th) setExplanation(res.status.explanation_th)
    if (res.selected_choice) setSelectedChoice(res.selected_choice)
    else if (res.status?.selected_choice) setSelectedChoice(res.status.selected_choice ?? null)
    if (res.correct_choice) setCorrectChoice(res.correct_choice)
    if (typeof res.passed === "boolean") setPassed(res.passed)
    else if (res.grade && typeof res.grade.passed === "boolean") setPassed(res.grade.passed)
    else if (res.status?.last_grade && typeof res.status.last_grade.passed === "boolean") {
      setPassed(res.status.last_grade.passed)
    }
  }

  const loadActions = useCallback(
    async (challengeId?: string | null) => {
      const res = await teacherActions()
      if (res.ok && Array.isArray(res.actions) && res.actions.length > 0) {
        setChoices(res.actions)
        if (res.question_th) setQuestion(res.question_th)
        if (res.explanation_th) setExplanation(res.explanation_th)
        if (res.selected_choice) setSelectedChoice(res.selected_choice)
        if (res.correct_choice) setCorrectChoice(res.correct_choice)
        if (res.answered && res.correct_choice) setPassed(true)
        return
      }
      const cid = challengeId || selectedId
      const catItem = catalog.find(c => c.id === cid) || FALLBACK_CATALOG.find(c => c.id === cid)
      setChoices([])
      setQuestion(catItem?.question_th || catItem?.intro || "")
    },
    [selectedId, catalog],
  )

  const refresh = useCallback(async () => {
    const [cat, st, capRes] = await Promise.all([
      teacherCatalog(),
      teacherStatus(),
      teacherCapabilities(),
    ])

    if (cat.ok && Array.isArray(cat.challenges) && cat.challenges.length > 0) {
      setCatalog(cat.challenges)
      setCatalogSource("api")
      setError(null)
    } else {
      setCatalog(FALLBACK_CATALOG)
      setCatalogSource("fallback")
      const why = cat.error || (!cat.ok ? "sim offline" : "catalog ว่าง")
      setError(
        `โหลดโจทย์จากเซิร์ฟเวอร์ไม่ได้ (${why}) — ใช้รายการสำรอง · API: ${API_BASE || "(vite proxy /api)"}`,
      )
    }

    if (capRes.ok && Array.isArray(capRes.capabilities)) {
      setCaps(capRes.capabilities)
    }

    let activeId = selectedId
    if (st.ok) {
      setStatus(st)
      if (st.challenge_id) {
        setSelectedId(st.challenge_id)
        activeId = st.challenge_id
      }
      if (st.last_grade) {
        setLastGrade(st.last_grade)
        if (typeof st.last_grade.passed === "boolean") setPassed(st.last_grade.passed)
      }
      if (st.question_th) setQuestion(st.question_th)
      if (st.explanation_th) setExplanation(st.explanation_th)
      if (st.selected_choice) setSelectedChoice(st.selected_choice)
      if (st.answered && st.last_grade?.passed) setPassed(true)
    }
    await loadActions(activeId)
  }, [loadActions, selectedId])

  useEffect(() => {
    void refresh()
    const t = window.setTimeout(() => void refresh(), 1000)
    return () => window.clearTimeout(t)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [chatMessages, busy])

  const run = async (
    label: string,
    fn: () => Promise<{
      ok: boolean
      error?: string
      status?: TeacherStatus
      grade?: TeacherGrade
      passed?: boolean
      explanation_th?: string
      selected_choice?: string
      correct_choice?: string
      hint?: string
      demo?: unknown
    }>,
  ) => {
    setBusy(true)
    setError(null)
    try {
      const res = await fn()
      if (!res.ok) {
        setError(res.error || `${label} failed`)
        setLog(prev => `${label}: ล้มเหลว — ${res.error || "?"}\n` + prev)
      } else {
        applyChoiceState(res)
        if (res.demo) {
          setDemoRunning(true)
          setLog(prev => `${label}: ถูก! กำลังโชว์หุ่น…\n` + prev)
          bumpScene()
          window.setTimeout(() => {
            bumpScene()
            setDemoRunning(false)
          }, 1200)
        } else {
          const msg =
            res.explanation_th ||
            res.status?.message ||
            (res.grade?.passed ? "ผ่าน" : res.grade ? "ยังไม่ผ่าน" : "ok")
          setLog(prev => `${label}: ${msg}\n` + prev)
        }
        if (res.status?.challenge_id) setSelectedId(res.status.challenge_id)
        if (label.startsWith("เริ่ม")) {
          setExplanation("")
          setSelectedChoice(null)
          setCorrectChoice(null)
          setPassed(null)
          setLastGrade(null)
        }
      }
      bumpScene()
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const sendChat = async (raw: string) => {
    const text = raw.trim()
    if (!text || busy) return
    setChatInput("")
    setChatMessages(prev => [...prev, { role: "user", content: text }])
    setBusy(true)
    setError(null)
    try {
      const res = await teacherChat(text, {
        use_llm: useLlmText ? true : false,
      })
      if (!res.ok) {
        setChatMessages(prev => [
          ...prev,
          { role: "assistant", content: res.error || "คุยไม่สำเร็จ" },
        ])
        setError(res.error || "chat failed")
      } else {
        if (res.history && res.history.length > 0) {
          // keep welcome if history from server is only new turns — merge by appending reply
          setChatMessages(prev => {
            const base = prev[0]?.content === WELCOME.content ? [prev[0]] : []
            // server history is full session; prefer it if longer
            if (res.history && res.history.length >= 2) {
              return [WELCOME, ...res.history]
            }
            return [
              ...prev,
              {
                role: "assistant",
                content: res.reply || "…",
                generated: res.switched_to_lesson,
                challenge_ids: res.generate?.load?.challenge_ids,
              },
            ]
          })
        } else {
          setChatMessages(prev => [
            ...prev,
            {
              role: "assistant",
              content: res.reply || "…",
              generated: res.switched_to_lesson,
              challenge_ids: res.generate?.load?.challenge_ids,
            },
          ])
        }
        if (res.status) setStatus(res.status)
        if (res.switched_to_lesson) {
          setExplanation("")
          setSelectedChoice(null)
          setCorrectChoice(null)
          setPassed(null)
          setLastGrade(null)
          setTab("lesson")
          bumpScene()
          await refresh()
        }
        setLog(
          prev =>
            `แชท: ${text.slice(0, 40)} → ${res.switched_to_lesson ? "สร้างโจทย์แล้ว" : "ตอบแล้ว"}\n` +
            prev,
        )
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setChatMessages(prev => [
        ...prev,
        { role: "assistant", content: "เชื่อมต่อ sim ไม่ได้ — ตรวจพอร์ต 8770" },
      ])
    } finally {
      setBusy(false)
    }
  }

  const quickGenerate = async (capabilityId: string) => {
    setBusy(true)
    setError(null)
    setChatMessages(prev => [
      ...prev,
      { role: "user", content: `สร้างโจทย์: ${capabilityId}` },
    ])
    try {
      const res = await teacherGenerate({
        capability_id: capabilityId,
        use_llm: useLlmText,
        start_first: true,
      })
      if (!res.ok) {
        setChatMessages(prev => [
          ...prev,
          { role: "assistant", content: res.error || "สร้างไม่สำเร็จ" },
        ])
        setError(res.error || "generate failed")
      } else {
        const n = res.generated ?? res.load?.challenge_ids?.length ?? 0
        setChatMessages(prev => [
          ...prev,
          {
            role: "assistant",
            content:
              res.message_th ||
              `สร้าง ${n} ข้อจาก ${res.capability_id} แล้ว — ไปแท็บเรียนได้เลย`,
            generated: true,
            challenge_ids: res.load?.challenge_ids,
          },
        ])
        setExplanation("")
        setSelectedChoice(null)
        setCorrectChoice(null)
        setPassed(null)
        setLastGrade(null)
        setTab("lesson")
        bumpScene()
        await refresh()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const grade = lastGrade || status?.last_grade
  const selected = catalog.find(c => c.id === selectedId)
  const started = Boolean(status?.challenge_id)
  const isPassed = passed === true || grade?.passed === true
  const sessionN = status?.session_challenge_count || 0

  return (
    <div className={cn("flex h-full min-h-0 flex-col gap-2", className)}>
      <div className="flex shrink-0 items-start justify-between gap-2">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-semibold text-sm">ครูหุ่นยนต์ (UC4)</h2>
            <span className="rounded bg-sky-600 px-1.5 py-0.5 text-[10px] font-medium text-white">
              sim
            </span>
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            คุยหาหัวข้อ → ออกโจทย์ → เลือก A–D → หุ่นสาธิต
          </p>
        </div>
        <Link to="/" className="rounded-md border px-2 py-1 text-xs hover:bg-accent">
          ← หน้า ARC
        </Link>
      </div>

      {/* tabs */}
      <div className="flex shrink-0 gap-1 rounded-lg border p-0.5">
        <button
          type="button"
          className={cn(
            "flex-1 rounded-md px-2 py-1.5 text-xs font-semibold transition-colors",
            tab === "chat" ? "bg-sky-600 text-white" : "hover:bg-muted",
          )}
          onClick={() => setTab("chat")}
        >
          คุย / ออกโจทย์
        </button>
        <button
          type="button"
          className={cn(
            "flex-1 rounded-md px-2 py-1.5 text-xs font-semibold transition-colors",
            tab === "lesson" ? "bg-violet-600 text-white" : "hover:bg-muted",
          )}
          onClick={() => setTab("lesson")}
        >
          เรียน
          {sessionN > 0 ? ` (${sessionN})` : ""}
        </button>
      </div>

      {tab === "chat" ? (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          {/* capability chips */}
          {caps.length > 0 ? (
            <div className="shrink-0 space-y-1">
              <div className="text-[10px] font-medium text-muted-foreground">
                หัวข้อที่สอนได้บน sim (กดสร้างชุด)
              </div>
              <div className="flex flex-wrap gap-1">
                {caps.map(c => (
                  <button
                    key={c.id}
                    type="button"
                    disabled={busy}
                    onClick={() => void quickGenerate(c.id)}
                    className="rounded-full border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-[11px] font-medium hover:bg-sky-500/20 disabled:opacity-50"
                    title={c.title_th}
                  >
                    {c.title_th || c.id}
                    <span className="ml-1 text-muted-foreground">
                      ·{c.skeleton_count ?? "?"}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {/* messages */}
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto rounded-lg border bg-muted/10 p-2">
            {chatMessages.map((m, i) => (
              <div
                key={`${m.role}-${i}`}
                className={cn(
                  "max-w-[95%] rounded-lg px-2.5 py-2 text-xs leading-relaxed whitespace-pre-wrap",
                  m.role === "user"
                    ? "ml-auto bg-sky-600 text-white"
                    : "mr-auto border bg-background",
                )}
              >
                {m.content}
                {m.generated ? (
                  <div className="mt-1.5">
                    <button
                      type="button"
                      className="rounded bg-violet-600 px-2 py-1 text-[10px] font-semibold text-white"
                      onClick={() => setTab("lesson")}
                    >
                      ไปเรียนชุดนี้ →
                    </button>
                  </div>
                ) : null}
              </div>
            ))}
            <div ref={chatEndRef} />
          </div>

          {/* quick prompts */}
          <div className="flex shrink-0 flex-wrap gap-1">
            {QUICK_PROMPTS.map(p => (
              <button
                key={p}
                type="button"
                disabled={busy}
                onClick={() => void sendChat(p)}
                className="rounded-full border px-2 py-0.5 text-[10px] hover:bg-accent disabled:opacity-50"
              >
                {p}
              </button>
            ))}
          </div>

          <label className="flex shrink-0 items-center gap-2 text-[10px] text-muted-foreground">
            <input
              type="checkbox"
              checked={useLlmText}
              onChange={e => setUseLlmText(e.target.checked)}
            />
            ใช้ LLM แต่งข้อความโจทย์ (ปิด = seed ชัวร์บน sim)
          </label>

          <form
            className="flex shrink-0 gap-1.5"
            onSubmit={e => {
              e.preventDefault()
              void sendChat(chatInput)
            }}
          >
            <input
              value={chatInput}
              onChange={e => setChatInput(e.target.value)}
              disabled={busy}
              placeholder="เช่น อยากเรียนลำดับหยิบของ…"
              className="min-w-0 flex-1 rounded-md border bg-background px-2.5 py-2 text-xs outline-none focus:ring-1 focus:ring-sky-500"
            />
            <button
              type="submit"
              disabled={busy || !chatInput.trim()}
              className="rounded-md bg-sky-600 px-3 py-2 text-xs font-semibold text-white hover:bg-sky-700 disabled:opacity-50"
            >
              ส่ง
            </button>
          </form>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
          {/* รายการโจทย์ */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] font-medium text-muted-foreground">
                โจทย์ ({catalog.length})
                {catalogSource === "api" ? (
                  <span className="ml-1 text-emerald-600">· จากเซิร์ฟเวอร์</span>
                ) : catalogSource === "fallback" ? (
                  <span className="ml-1 text-amber-600">· รายการสำรอง</span>
                ) : null}
                {sessionN > 0 ? (
                  <span className="ml-1 text-violet-600">· ชุดคุย {sessionN} ข้อ</span>
                ) : null}
              </span>
              <button
                type="button"
                className="text-[10px] text-sky-700 underline dark:text-sky-300"
                onClick={() => void refresh()}
              >
                โหลดใหม่
              </button>
            </div>
            <div className="max-h-32 space-y-1 overflow-y-auto rounded-lg border p-1.5">
              {catalog.map(c => {
                const active = c.id === selectedId
                const done = status?.passed_ids?.includes(c.id)
                const generated = Boolean(c.source) || c.id.startsWith("gen_")
                return (
                  <button
                    key={c.id}
                    type="button"
                    disabled={busy}
                    onClick={() => setSelectedId(c.id)}
                    className={cn(
                      "flex w-full flex-col items-start rounded-md px-2.5 py-2 text-left text-xs transition-colors",
                      active ? "bg-sky-600 text-white shadow-sm" : "hover:bg-muted",
                      busy && "opacity-60",
                    )}
                  >
                    <span className="font-semibold">
                      {done ? "✓ " : ""}
                      {generated ? "✨ " : ""}
                      [L{c.level}] {c.title_th || c.id}
                    </span>
                  </button>
                )
              })}
            </div>
          </div>

          {/* คำถาม */}
          <div className="rounded-lg border-2 border-sky-500/40 bg-sky-500/5 p-3 text-xs space-y-2">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-sky-700 dark:text-sky-300">
              คำถาม
            </div>
            <p className="text-sm font-medium leading-relaxed">
              {question ||
                selected?.question_th ||
                selected?.intro ||
                selected?.title_th ||
                "— คุยออกโจทย์ หรือเลือกจากรายการแล้วกดเริ่ม —"}
            </p>
            {!started ? (
              <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-800 dark:text-amber-200">
                กด «เริ่มโจทย์» หรือคุยในแท็บซ้ายให้สร้างชุดก่อน
              </p>
            ) : null}
          </div>

          {/* Choice */}
          <div className="rounded-lg border-2 border-violet-500/50 bg-violet-500/5 p-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold text-sm text-violet-800 dark:text-violet-200">
                ตัวเลือก (Choice)
              </span>
            </div>
            <div className="flex flex-col gap-1.5">
              {choices.length === 0 ? (
                <p className="text-[11px] text-muted-foreground">
                  ยังไม่มีตัวเลือก — เริ่มโจทย์ก่อน
                </p>
              ) : (
                choices.map(c => {
                  const letter = c.id
                  const isSelected = selectedChoice === letter
                  const isCorrect = isPassed && correctChoice === letter
                  const isWrongPick = isSelected && passed === false
                  return (
                    <button
                      key={c.id}
                      type="button"
                      disabled={busy || !started || isPassed}
                      onClick={() => void run(`ตอบ ${letter}`, () => teacherAct(letter))}
                      className={cn(
                        "rounded-md px-3 py-2.5 text-left text-xs font-medium transition-colors shadow-sm border",
                        !started
                          ? "border-dashed bg-muted/30 text-muted-foreground cursor-not-allowed"
                          : isCorrect
                            ? "border-emerald-600 bg-emerald-600 text-white"
                            : isWrongPick
                              ? "border-rose-500 bg-rose-500/15 text-rose-900 dark:text-rose-100"
                              : isSelected
                                ? "border-violet-600 bg-violet-600 text-white"
                                : "border-transparent bg-violet-600/90 text-white hover:bg-violet-700 disabled:opacity-50",
                      )}
                    >
                      <span className="mr-2 inline-flex size-5 items-center justify-center rounded bg-black/15 text-[11px] font-bold">
                        {letter}
                      </span>
                      {c.label}
                    </button>
                  )
                })
              )}
            </div>
          </div>

          {explanation || grade ? (
            <div
              className={cn(
                "rounded-lg border px-3 py-2.5 text-xs space-y-1",
                isPassed
                  ? "border-emerald-600/40 bg-emerald-500/10"
                  : "border-amber-600/40 bg-amber-500/10",
              )}
            >
              <div className="font-semibold">
                {isPassed ? "✅ ถูกต้อง!" : "❌ ยังไม่ถูก"}
                {selectedChoice ? ` · เลือก ${selectedChoice}` : ""}
                {isPassed && correctChoice ? ` · คำตอบ ${correctChoice}` : ""}
              </div>
              {explanation ? (
                <p className="leading-relaxed text-muted-foreground">{explanation}</p>
              ) : grade?.hint ? (
                <p className="leading-relaxed text-muted-foreground">{grade.hint}</p>
              ) : null}
              {isPassed ? (
                <p className="text-[11px] font-medium text-emerald-800 dark:text-emerald-200">
                  {demoRunning
                    ? "🤖 กำลังให้หุ่นทำงานให้ดู…"
                    : "🤖 หุ่นโชว์ขั้นตอนให้ดูแล้ว (แผงขวา)"}
                </p>
              ) : null}
            </div>
          ) : null}

          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              disabled={busy || !selectedId}
              className="rounded-md bg-sky-600 px-2 py-2 text-xs font-semibold text-white hover:bg-sky-700 disabled:opacity-50"
              onClick={() =>
                void run("เริ่ม", async () => {
                  setExplanation("")
                  setSelectedChoice(null)
                  setCorrectChoice(null)
                  setPassed(null)
                  setLastGrade(null)
                  return teacherStart(selectedId || undefined)
                })
              }
            >
              เริ่มโจทย์
            </button>
            <button
              type="button"
              disabled={busy || !started}
              className="rounded-md border bg-background px-2 py-2 text-xs font-semibold hover:bg-accent disabled:opacity-50"
              onClick={() => void run("ตัวอย่าง", () => teacherDemo())}
            >
              ดูตัวอย่างหุ่น
            </button>
            <button
              type="button"
              disabled={busy || !started || !isPassed}
              className="rounded-md bg-emerald-600 px-2 py-2 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
              onClick={() => void run("ถัดไป", () => teacherNext())}
            >
              โจทย์ถัดไป
            </button>
            <button
              type="button"
              disabled={busy}
              className="rounded-md border bg-background px-2 py-2 text-xs font-semibold hover:bg-accent disabled:opacity-50"
              onClick={() =>
                void run("รีเซ็ต", async () => {
                  setChatMessages([WELCOME])
                  return teacherResetSession()
                })
              }
            >
              รีเซ็ตบทเรียน
            </button>
          </div>

          <button
            type="button"
            disabled={busy || !started}
            className="text-[10px] text-muted-foreground underline disabled:opacity-40"
            onClick={() => void run("ตรวจ", () => teacherCheck())}
          >
            ตรวจสถานะอีกครั้ง
          </button>
        </div>
      )}

      {error ? (
        <p className="shrink-0 text-xs text-amber-700 dark:text-amber-300 whitespace-pre-wrap">
          {error}
        </p>
      ) : null}

      {log ? (
        <pre className="max-h-16 shrink-0 overflow-auto rounded-md border bg-muted/20 p-1.5 text-[10px] text-muted-foreground whitespace-pre-wrap">
          {log}
        </pre>
      ) : null}

      <p className="shrink-0 text-[10px] text-muted-foreground leading-relaxed">
        โฟลว์: <strong>คุย</strong> อยากเรียนอะไร → ระบบสร้างชุด → แท็บ <strong>เรียน</strong>{" "}
        เลือก A–D → ถูกแล้วหุ่น demo บน sim
      </p>
    </div>
  )
}

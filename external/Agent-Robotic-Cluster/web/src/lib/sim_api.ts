// API client สำหรับ Python sim_server.py
// - ถ้าระบุ VITE_ARC_SIM_URL → ใช้ตรงๆ
// - ไม่งั้นใน dev ใช้ relative "" (vite proxy /api → :8770) แล้ว fallback absolute

function resolveApiBase(): string {
  const fromEnv = (import.meta.env.VITE_ARC_SIM_URL as string | undefined)?.replace(/\/$/, "")
  if (fromEnv) return fromEnv
  // dev ผ่าน vite proxy — same origin ลดปัญหา CORS / host
  if (import.meta.env.DEV) return ""
  return "http://127.0.0.1:8770"
}

export const API_BASE = resolveApiBase()

// ---- ประเภทข้อมูลที่รับจาก Python server ----

export interface SimObject {
  id: string
  color: string
  robot_plane: { x: number; y: number }
  per_arm_robot_plane?: Record<string, { x: number; y: number; calibration?: string }>
  reachable_by: string[]
  preferred_arm?: string
  block?: string | null
  zone?: { name: string; role: string; bounds_mm: Record<string, number> } | null
  inside_workspace?: boolean
  area?: number
  detection_source?: string
  radius_mm?: number
}

export interface SimZone {
  name: string
  role: string
  bounds_mm: { x_min: number; x_max: number; y_min: number; y_max: number }
}

export interface SimArmPose {
  x: number
  y: number
  z: number
  r: number
}

/** Trajectory segment จาก sim — UI เล่นอนิเมชันเองที่ 60fps */
export interface SimMotionSegment {
  from: SimArmPose
  to: SimArmPose
  t0_ms: number
  duration_ms: number
  easing?: string
  speed_mm_s?: number
  server_now_ms?: number
}

export interface SimMotionProfile {
  speed_mm_s?: number
  fps?: number
  dt_s?: number
  min_s?: number
  max_s?: number
  easing?: string
}

export interface SimArmState {
  arm_id: string
  pose: SimArmPose
  robot_mode_name?: string
  enable_status?: boolean
  running_status?: boolean
  error_status?: boolean
  motion?: SimMotionSegment | null
  server_now_ms?: number
  /** server ติด holding ต่อแขน — HeldObject อ่านจาก bus */
  holding?: string
  holding_color?: string | null
}

export interface SceneState {
  ok: boolean
  objects: SimObject[]
  held: SimObject | null
  /** arm_id ที่ถือ held อยู่ (Arm1/Arm2) */
  held_by?: string | null
  zones: SimZone[]
  /** Live arm poses from SimRobot — required for 3D motion */
  arms?: SimArmState[]
  sim_mode?: boolean
  server_now_ms?: number
  motion_profile?: SimMotionProfile
  error?: string
}

export interface ArmsMotionState {
  ok: boolean
  arms: SimArmState[]
  /** held ติดมากับ arms poll — ไม่พึ่ง scene cache 1.5s */
  held?: SimObject | null
  held_by?: string | null
  server_now_ms?: number
  motion_profile?: SimMotionProfile
  any_running?: boolean
  error?: string
}

export interface ApiResult {
  ok: boolean
  error?: string
  [key: string]: unknown
}

// ---- low-level fetch wrapper ----

export async function api(
  path: string,
  method: "GET" | "POST" = "GET",
  body?: Record<string, unknown>,
): Promise<ApiResult> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    const data = (await res.json()) as ApiResult
    return data
  } catch (err) {
    // Python server offline หรือ network error
    return {
      ok: false,
      error: err instanceof Error ? err.message : "Python sim server is offline",
    }
  }
}

// ---- high-level helpers ----

/** GET /api/sim/scene → สถานะฉากปัจจุบัน */
export async function refreshScene(): Promise<SceneState> {
  const result = await api("/api/sim/scene", "GET")
  return result as unknown as SceneState
}

/** GET /api/sim/arms → pose + trajectory เบาๆ (poll เร็ว ≥25 FPS) */
export async function refreshArms(): Promise<ArmsMotionState> {
  const result = await api("/api/sim/arms", "GET")
  return result as unknown as ArmsMotionState
}

/** POST /api/sim/spawn → สร้างวัตถุสีที่กำหนด */
export async function spawnObject(color: string): Promise<ApiResult> {
  return api("/api/sim/spawn", "POST", { color })
}

/** POST /api/sim/remove → ลบวัตถุตาม id */
export async function removeObject(id: string): Promise<ApiResult> {
  return api("/api/sim/remove", "POST", { id })
}

/** POST /api/sim/move → ย้ายวัตถุ (drag-drop) */
export async function moveObject(id: string, x: number, y: number): Promise<ApiResult> {
  return api("/api/sim/move", "POST", { id, x, y })
}

/** POST /api/sim/reset → รีเซ็ตฉากกลับ default */
export async function resetScene(): Promise<ApiResult> {
  return api("/api/sim/reset", "POST", {})
}

/** POST /api/sim/toggle → สลับ sim/real mode; on=undefined → toggle */
export async function toggleSim(on?: boolean): Promise<ApiResult> {
  const body: Record<string, unknown> = {}
  if (on !== undefined) body.on = on
  return api("/api/sim/toggle", "POST", body)
}

/** POST /api/sim/chat → ส่งคำสั่งภาษาธรรมชาติ */
export async function sendChat(message: string): Promise<ApiResult> {
  return api("/api/sim/chat", "POST", { message })
}

// ---- UC4 Teacher (sim tutor: chat → generate → MCQ + demo) ----

export interface TeacherChallengeSummary {
  id: string
  level?: number
  mode?: "mcq" | "procedure" | "robot" | string
  title_th?: string
  intro?: string
  question_th?: string
  source?: string
  capability_id?: string
  skeleton_id?: string
}

export interface TeacherStatus {
  ok: boolean
  module?: string
  version?: string
  runtime?: string
  llm?: boolean
  student_id?: string
  state?: string
  level?: number
  challenge_id?: string | null
  mode?: "mcq" | "procedure" | "robot" | string | null
  title_th?: string | null
  intro?: string | null
  question_th?: string | null
  message?: string
  selected_choice?: string | null
  answered?: boolean
  explanation_th?: string | null
  consecutive_pass?: number
  consecutive_fail?: number
  passed_ids?: string[]
  waypoints_logged?: number
  procedure_log?: string[]
  procedure_expected_count?: number
  last_grade?: TeacherGrade | null
  auto_demo_suggested?: boolean
  session_challenge_count?: number
  session_challenge_ids?: string[]
  session_meta?: Record<string, unknown>
  chat_turns?: number
  error?: string
}

export interface TeacherCapability {
  id: string
  title_th?: string
  level_min?: number
  level_max?: number
  sim_proven?: boolean
  keywords?: string[]
  skeleton_count?: number
  skeleton_ids?: string[]
}

export interface TeacherChatMessage {
  role: "user" | "assistant" | string
  content: string
  intent?: string
  capability_id?: string
  generated?: boolean
  challenge_ids?: string[] | null
}

export interface TeacherGrade {
  ok?: boolean
  passed?: boolean
  score?: number
  checks?: unknown[]
  hint?: string | null
  challenge_id?: string
  explanation_th?: string
  selected_choice?: string
  correct_choice?: string
}

export async function teacherCatalog(level?: number): Promise<ApiResult & { challenges?: TeacherChallengeSummary[] }> {
  const q = level != null ? `?level=${level}` : ""
  return api(`/api/teacher/catalog${q}`, "GET")
}

export async function teacherStatus(): Promise<TeacherStatus> {
  return (await api("/api/teacher/status", "GET")) as unknown as TeacherStatus
}

export async function teacherStart(challengeId?: string): Promise<ApiResult & { status?: TeacherStatus; challenge?: TeacherChallengeSummary }> {
  const body: Record<string, unknown> = {}
  if (challengeId) body.challenge_id = challengeId
  return api("/api/teacher/start", "POST", body)
}

export async function teacherDemo(): Promise<ApiResult & { status?: TeacherStatus }> {
  return api("/api/teacher/demo", "POST", {})
}

export async function teacherCheck(): Promise<ApiResult & { grade?: TeacherGrade; status?: TeacherStatus }> {
  return api("/api/teacher/check", "POST", {})
}

export async function teacherNext(): Promise<ApiResult & { status?: TeacherStatus }> {
  return api("/api/teacher/next", "POST", {})
}

export async function teacherResetSession(): Promise<ApiResult & TeacherStatus> {
  return api("/api/teacher/reset", "POST", {}) as Promise<ApiResult & TeacherStatus>
}

export interface TeacherStudentAction {
  id: string
  label: string
  order?: number
}

export async function teacherActions(): Promise<
  ApiResult & {
    actions?: TeacherStudentAction[]
    choices?: TeacherStudentAction[]
    challenge_id?: string | null
    mode?: string | null
    question_th?: string
    procedure_log?: string[]
    expected_count?: number
    selected_choice?: string | null
    answered?: boolean
    explanation_th?: string | null
    correct_choice?: string | null
    hint?: string
  }
> {
  return api("/api/teacher/actions", "GET")
}

/** เลือก Choice (mcq) / ขั้น (procedure) / สั่งหุ่น (robot) */
export async function teacherAct(actionId: string): Promise<
  ApiResult & {
    status?: TeacherStatus
    procedure_log?: string[]
    mode?: string
    passed?: boolean
    explanation_th?: string
    correct_choice?: string
    selected_choice?: string
    grade?: TeacherGrade
    demo?: unknown
    hint?: string
  }
> {
  return api("/api/teacher/act", "POST", { action_id: actionId })
}

export async function teacherCapabilities(): Promise<
  ApiResult & {
    version?: string
    capabilities?: TeacherCapability[]
    allowed_demo_ops?: string[]
  }
> {
  return api("/api/teacher/capabilities", "GET")
}

/** คุยกับครู → map หัวข้อ → สร้างชุดโจทย์ (sim) */
export async function teacherChat(
  message: string,
  opts?: { use_llm?: boolean; count?: number },
): Promise<
  ApiResult & {
    reply?: string
    intent?: Record<string, unknown>
    generate?: {
      ok?: boolean
      generated?: number
      capability_id?: string
      load?: { challenge_ids?: string[]; start?: unknown }
    }
    history?: TeacherChatMessage[]
    status?: TeacherStatus
    switched_to_lesson?: boolean
  }
> {
  const body: Record<string, unknown> = { message }
  if (opts?.use_llm != null) body.use_llm = opts.use_llm
  if (opts?.count != null) body.count = opts.count
  return api("/api/teacher/chat", "POST", body)
}

/** สร้างชุดโจทย์ตรงๆ จาก capability / topic */
export async function teacherGenerate(opts: {
  capability_id?: string
  topic?: string
  count?: number
  use_llm?: boolean
  start_first?: boolean
  skeleton_ids?: string[]
}): Promise<
  ApiResult & {
    capability_id?: string
    generated?: number
    message_th?: string
    results?: unknown[]
    load?: { challenge_ids?: string[]; start?: unknown }
    status?: TeacherStatus
  }
> {
  return api("/api/teacher/generate", "POST", opts as Record<string, unknown>)
}

// Panel ครอบ 3D scene + Simulator controls
// - scene (objects) → React state ช้าๆ
// - arms/motion → armMotionBus อย่างเดียว (ไม่ setState ทุก poll) → 3D สมูท
import { useCallback, useEffect, useRef, useState } from "react"
import { Scene } from "./scene/Scene"
import { cn } from "@/lib/utils"
import {
  refreshArms,
  refreshScene,
  resetScene,
  spawnObject,
  type SceneState,
} from "@/lib/sim_api"
import { transformToSnapshot } from "@/lib/sim_transform"
import { busAnyRunning, publishArms } from "./armMotionBus"
import type { WorkspaceSnapshot } from "./types"

const EMPTY_SNAPSHOT: WorkspaceSnapshot = { scan: null, state: null }
/** objects/zones */
const SCENE_POLL_MS = 1500
/** ซิงก์ trajectory เข้า bus — อนิเมชันจริงเล่น 60fps ใน useFrame */
const ARMS_POLL_MS = 80
const ARMS_IDLE_POLL_MS = 400
/** อัปเดตข้อความ pose hint (ไม่กระทบ 3D) */
const HINT_UI_MS = 200

export const ARC_SIM_REFRESH_EVENT = "arc-sim-refresh"

function pushArmsToBus(sceneLike: {
  arms?: Array<{
    arm_id: string
    pose: { x: number; y: number; z: number; r: number }
    motion?: {
      from: { x: number; y: number; z: number; r: number }
      to: { x: number; y: number; z: number; r: number }
      t0_ms: number
      duration_ms: number
      easing?: string
      speed_mm_s?: number
      server_now_ms?: number
    } | null
    running_status?: boolean
    /** server ติดให้ต่อแขนแล้ว — เชื่ออันนี้ก่อน */
    holding?: string
    holding_color?: string | null
  }>
  server_now_ms?: number
  held?: { id: string; preferred_arm?: string; color?: string } | null
  held_by?: string | null
}) {
  const heldId = sceneLike.held?.id
  const heldBy =
    sceneLike.held_by ||
    sceneLike.held?.preferred_arm ||
    (heldId ? "Arm1" : null)
  const arms = sceneLike.arms ?? []
  publishArms(
    arms.map(a => {
      const p = a.pose
      // local → workspace เดียวกับ armLocalToShared (facing ทั้ง x,y)
      const wx = a.arm_id === "Arm2" ? 695 - p.x : -95 + p.x
      const wy = a.arm_id === "Arm2" ? 200 - p.y : 200 + p.y
      const occupies_shared = wx >= 200 && wx <= 400 && wy >= 0 && wy <= 400
      let zone: "own" | "shared" | "other" | "unknown" = "own"
      if (occupies_shared) zone = "shared"
      else if (wx < 0 || wx > 600) zone = "other"

      const motion = a.motion
        ? {
            ...a.motion,
            server_now_ms: a.motion.server_now_ms ?? sceneLike.server_now_ms,
          }
        : null

      // 1) holding บน arm entry จาก server  2) fallback held/held_by
      const fromArm = a.holding && a.holding !== "unknown" ? a.holding : null
      const isHolder = Boolean(
        fromArm || (heldId && heldBy === a.arm_id),
      )
      const holding = isHolder
        ? String(fromArm || heldId)
        : "unknown"
      const holding_color = isHolder
        ? String(
            a.holding_color ||
              sceneLike.held?.color ||
              "green",
          ).toLowerCase()
        : undefined

      return {
        arm_id: a.arm_id,
        pose: { x: p.x, y: p.y, z: p.z, r: p.r },
        motion,
        running: Boolean(a.running_status || motion),
        zone,
        occupies_shared,
        holding,
        holding_color,
      }
    }),
  )
}

export function WorkspacePanel({ className }: { className?: string }) {
  const [snapshot, setSnapshot] = useState<WorkspaceSnapshot>(EMPTY_SNAPSHOT)
  const [serverOnline, setServerOnline] = useState(false)
  const [spawnColor, setSpawnColor] = useState<"green" | "red" | "blue">("green")
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const [armPoseHint, setArmPoseHint] = useState("")
  const [speedHint, setSpeedHint] = useState("")
  const [objectCount, setObjectCount] = useState(0)
  const [heldLabel, setHeldLabel] = useState("")

  const sceneDataRef = useRef<SceneState | null>(null)
  const anyRunningRef = useRef(false)
  const armsTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const sceneTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const armsInflight = useRef(false)
  const lastHintAt = useRef(0)
  /** ระหว่างลาก — ห้าม scene poll ดึง object กลับจุดเก่า */
  const draggingIdRef = useRef<string | null>(null)

  const updateHintFromBus = useCallback((profile?: { speed_mm_s?: number; fps?: number }) => {
    const now = performance.now()
    if (now - lastHintAt.current < HINT_UI_MS) return
    lastHintAt.current = now

    const data = sceneDataRef.current
    const arms = data?.arms ?? []
    const hint = arms
      .map(a => {
        const p = a.pose
        const run = a.running_status || a.motion ? "▶" : ""
        return `${a.arm_id}${run}:(${Math.round(p.x)},${Math.round(p.y)},${Math.round(p.z)})`
      })
      .join(" · ")
    setArmPoseHint(hint)
    if (profile?.speed_mm_s) {
      setSpeedHint(` · ${Math.round(profile.speed_mm_s)} mm/s`)
    }
  }, [])

  const fetchScene = useCallback(async () => {
    // กำลังลาก — อย่าทับตำแหน่ง object จาก server
    if (draggingIdRef.current) return
    const data = await refreshScene()
    if (!data.ok) {
      setServerOnline(false)
      return
    }
    // ถ้าเริ่มลากระหว่างรอ response — ทิ้ง snapshot นี้
    if (draggingIdRef.current) return
    sceneDataRef.current = data
    setServerOnline(true)
    // objects → React (ช้า); arms → bus (ทันที)
    pushArmsToBus(data)
    anyRunningRef.current = Boolean(
      (data.arms ?? []).some(a => a.running_status || a.motion) || busAnyRunning(),
    )
    setSnapshot(transformToSnapshot(data))
    setObjectCount(data.objects?.length ?? 0)
    setHeldLabel(
      data.held
        ? ` · ถือ ${data.held.id}${data.held_by ? `@${data.held_by}` : ""}`
        : "",
    )
    updateHintFromBus(data.motion_profile)
  }, [updateHintFromBus])

  const fetchArmsFast = useCallback(async () => {
    if (armsInflight.current) return
    armsInflight.current = true
    try {
      const armsState = await refreshArms()
      if (!armsState.ok) {
        setServerOnline(false)
        return
      }
      setServerOnline(true)

      // held จาก arms API โดยตรง (server ส่งทุก poll) — กัน cache scene เก่า
      // ล้าง holding ตอน arms poll ทับ → วัตถุไม่ติดปลายแขน
      const held =
        armsState.held !== undefined
          ? armsState.held
          : (sceneDataRef.current?.held ?? null)
      const held_by =
        armsState.held_by !== undefined
          ? armsState.held_by
          : (sceneDataRef.current?.held_by ?? null)

      // ★ หัวใจ: ใส่ bus อย่างเดียว — ไม่ setSnapshot → Canvas ไม่ re-render
      pushArmsToBus({
        arms: armsState.arms,
        server_now_ms: armsState.server_now_ms,
        held,
        held_by,
      })

      // เก็บ arms + held ล่าสุดใน ref สำหรับ hint / scene sync
      if (sceneDataRef.current) {
        sceneDataRef.current = {
          ...sceneDataRef.current,
          arms: armsState.arms,
          held: held ?? null,
          held_by: held_by ?? null,
          server_now_ms: armsState.server_now_ms,
          motion_profile: armsState.motion_profile ?? sceneDataRef.current.motion_profile,
        }
      }

      // อัปเดตป้ายถือจาก arms poll (ไม่รอ scene 1.5s)
      setHeldLabel(
        held
          ? ` · ถือ ${held.id}${held_by ? `@${held_by}` : ""}`
          : "",
      )

      anyRunningRef.current = Boolean(armsState.any_running || busAnyRunning())
      updateHintFromBus(armsState.motion_profile)
    } finally {
      armsInflight.current = false
    }
  }, [updateHintFromBus])

  useEffect(() => {
    void fetchScene()

    sceneTimerRef.current = setInterval(() => {
      void fetchScene()
    }, SCENE_POLL_MS)

    let cancelled = false
    const scheduleArms = () => {
      if (cancelled) return
      const delay = anyRunningRef.current || busAnyRunning() ? ARMS_POLL_MS : ARMS_IDLE_POLL_MS
      armsTimerRef.current = setTimeout(async () => {
        await fetchArmsFast()
        scheduleArms()
      }, delay)
    }
    scheduleArms()

    const onRefresh = () => {
      anyRunningRef.current = true
      void fetchArmsFast()
      void fetchScene()
    }
    window.addEventListener(ARC_SIM_REFRESH_EVENT, onRefresh)

    return () => {
      cancelled = true
      if (sceneTimerRef.current) clearInterval(sceneTimerRef.current)
      if (armsTimerRef.current) clearTimeout(armsTimerRef.current)
      window.removeEventListener(ARC_SIM_REFRESH_EVENT, onRefresh)
    }
  }, [fetchScene, fetchArmsFast])

  const handleReset = async () => {
    await resetScene()
    await fetchScene()
  }

  const handleSpawn = async () => {
    await spawnObject(spawnColor)
    await fetchScene()
  }

  return (
    <div className={cn("flex h-full min-h-0 flex-col gap-2", className)}>
      <div className={cn("flex shrink-0 items-center justify-between gap-2")}>
        <div className="flex min-w-0 flex-col gap-0.5">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm">มุมมองหุ่นยนต์ 3D</span>
            {/* ป้ายนี้ยืนยันว่าโหลดโค้ดสมูทชุดใหม่แล้ว — ถ้าไม่เห็น = browser ยัง cache เก่า */}
            <span
              className="rounded bg-emerald-600 px-1.5 py-0.5 text-[10px] font-medium text-white"
              title="animation bus + joint damp"
            >
              hold-v6
            </span>
            {!serverOnline ? (
              <span className="rounded bg-slate-500 px-1.5 py-0.5 text-[10px] font-medium text-white">
                ออฟไลน์
              </span>
            ) : null}
          </div>
          {armPoseHint ? (
            <span className="truncate text-[10px] text-muted-foreground" title={armPoseHint + speedHint}>
              {armPoseHint}
              {speedHint}
            </span>
          ) : null}
        </div>

        <div className="flex items-center gap-1.5">
          <button
            className="rounded-md border bg-background px-2 py-1 text-xs hover:bg-accent disabled:opacity-50"
            disabled={!serverOnline}
            onClick={() => void handleReset()}
            type="button"
            title="รีเซ็ตวัตถุในฉาก"
          >
            Reset ฉาก
          </button>
        </div>
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden rounded-lg border bg-[#0f172a]">
        <Scene
          snapshot={snapshot}
          draggingId={draggingId}
          onObjectDragStart={id => {
            draggingIdRef.current = id
            setDraggingId(id)
          }}
          onObjectDragEnd={() => {
            draggingIdRef.current = null
            setDraggingId(null)
            void fetchScene()
          }}
        />
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <select
          className="rounded-md border bg-background px-2 py-1 text-xs"
          value={spawnColor}
          onChange={e => setSpawnColor(e.target.value as "green" | "red" | "blue")}
        >
          <option value="green">เขียว</option>
          <option value="red">แดง</option>
          <option value="blue">น้ำเงิน</option>
        </select>
        <button
          className="rounded-md border bg-background px-2 py-1 text-xs hover:bg-accent disabled:opacity-50"
          disabled={!serverOnline}
          onClick={() => void handleSpawn()}
          type="button"
        >
          วางวัตถุ
        </button>
        <span className="text-[10px] text-muted-foreground">
          วัตถุ {objectCount}
          {heldLabel}
        </span>
      </div>
    </div>
  )
}

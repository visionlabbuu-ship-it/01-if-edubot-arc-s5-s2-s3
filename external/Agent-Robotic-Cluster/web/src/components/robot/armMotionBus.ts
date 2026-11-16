// Motion bus — แหล่งความจริงของ pose/trajectory สำหรับ 3D
// อัปเดตจาก poll โดยไม่ผ่าน React state → RobotArm อ่านใน useFrame ทุกเฟรม
// เป้าหมาย: สมูทขั้นสุด ไม่กระตุกจาก re-render

export interface BusPose {
  x: number
  y: number
  z: number
  r: number
}

export interface BusMotion {
  from: BusPose
  to: BusPose
  /** ระยะเวลาทั้ง segment (ms) */
  duration_ms: number
  easing?: string
  speed_mm_s?: number
  /**
   * เวลา local (performance.now) ที่ progress=0
   * คำนวณครั้งเดียวตอนรับ segment ใหม่ — ไม่ใช้ server clock ทุกเฟรม
   * (กัน jitter จาก clock skew / poll)
   */
  local_t0_ms: number
  /** server segment id (t0_ms จาก server) สำหรับตรวจ segment ใหม่ */
  server_t0_ms: number
}

export interface BusArm {
  arm_id: string
  pose: BusPose
  motion: BusMotion | null
  running: boolean
  zone: "own" | "shared" | "other" | "unknown"
  occupies_shared: boolean
  holding: string
  /** สีวัตถุที่ถือ (สำหรับวาดติดปลายทันที ไม่รอ React snapshot) */
  holding_color?: string
  /** เวลารับข้อมูลล่าสุด (performance.now) */
  updated_at: number
}

type Listener = () => void

const arms = new Map<string, BusArm>()
const listeners = new Set<Listener>()

function notify() {
  for (const fn of listeners) {
    try {
      fn()
    } catch {
      /* ignore */
    }
  }
}

export function subscribeArmBus(fn: Listener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function getBusArm(armId: string): BusArm | undefined {
  return arms.get(armId)
}

export function getAllBusArms(): BusArm[] {
  return Array.from(arms.values())
}

function defaultArm(armId: string): BusArm {
  return {
    arm_id: armId,
    pose: { x: 350, y: 0, z: 0, r: 0 },
    motion: null,
    running: false,
    zone: "own",
    occupies_shared: false,
    holding: "unknown",
    holding_color: undefined,
    updated_at: performance.now(),
  }
}

function posesClose(a: BusPose, b: BusPose, eps = 0.05): boolean {
  return (
    Math.abs(a.x - b.x) < eps &&
    Math.abs(a.y - b.y) < eps &&
    Math.abs(a.z - b.z) < eps &&
    Math.abs(a.r - b.r) < eps
  )
}

export interface PublishArmInput {
  arm_id: string
  pose: BusPose
  motion?: {
    from: BusPose
    to: BusPose
    t0_ms: number
    duration_ms: number
    easing?: string
    speed_mm_s?: number
    server_now_ms?: number
  } | null
  running?: boolean
  zone?: BusArm["zone"]
  occupies_shared?: boolean
  holding?: string
  holding_color?: string
}

/**
 * รับข้อมูลจาก poll — สร้าง/ต่อ trajectory ด้วย local clock
 * ไม่ trigger React re-render (listeners ใช้ optional)
 */
export function publishArm(input: PublishArmInput): void {
  const prev = arms.get(input.arm_id) ?? defaultArm(input.arm_id)
  const now = performance.now()
  let motion: BusMotion | null = null

  if (input.motion && input.motion.duration_ms > 0) {
    const m = input.motion
    const sameSegment =
      prev.motion &&
      prev.motion.server_t0_ms === m.t0_ms &&
      Math.abs(prev.motion.duration_ms - m.duration_ms) < 1 &&
      posesClose(prev.motion.to, m.to, 0.5)

    if (sameSegment && prev.motion) {
      // คง local_t0 เดิม — อนิเมชันไม่กระตุกตอน poll
      motion = {
        ...prev.motion,
        from: m.from,
        to: m.to,
        duration_ms: m.duration_ms,
        easing: m.easing ?? prev.motion.easing,
        speed_mm_s: m.speed_mm_s ?? prev.motion.speed_mm_s,
      }
    } else {
      // segment ใหม่: จัด local clock ให้ตรง progress ปัจจุบันครั้งเดียว
      let progress = 0
      if (m.server_now_ms != null && m.duration_ms > 0) {
        progress = (m.server_now_ms - m.t0_ms) / m.duration_ms
        progress = Math.max(0, Math.min(0.999, progress))
      }
      motion = {
        from: { ...m.from },
        to: { ...m.to },
        duration_ms: m.duration_ms,
        easing: m.easing,
        speed_mm_s: m.speed_mm_s,
        local_t0_ms: now - progress * m.duration_ms,
        server_t0_ms: m.t0_ms,
      }
    }
  }

  // holding ต้องเชื่อ input ทุกครั้งที่ส่งมา (รวม "unknown") — ห้ามค้าง prev
  // เดิมใช้ ?? ทำให้บาง path ไม่เคลียร์/ไม่อัปเดต → HeldObject ไม่โชว์
  const holding =
    input.holding !== undefined && input.holding !== null
      ? input.holding
      : prev.holding
  const holding_color =
    holding && holding !== "unknown"
      ? input.holding_color !== undefined
        ? input.holding_color
        : prev.holding_color
      : undefined

  arms.set(input.arm_id, {
    arm_id: input.arm_id,
    pose: { ...input.pose },
    motion,
    running: Boolean(input.running || motion),
    zone: input.zone ?? prev.zone,
    occupies_shared: input.occupies_shared ?? prev.occupies_shared,
    holding,
    holding_color,
    updated_at: now,
  })
}

export function publishArms(list: PublishArmInput[]): void {
  for (const item of list) publishArm(item)
  notify()
}

/** ease สำหรับ trajectory */
export function easeProgress(t: number, mode?: string): number {
  const x = Math.max(0, Math.min(1, t))
  if (mode === "smoothstep" || mode === "ease" || mode === "ease_in_out") {
    // smootherstep — อนุพันธ์ที่ปลาย = 0 ลื่นกว่า smoothstep
    return x * x * x * (x * (x * 6 - 15) + 10)
  }
  return x
}

export function sampleBusPose(arm: BusArm, now = performance.now()): BusPose {
  const m = arm.motion
  if (!m || m.duration_ms <= 0) return { ...arm.pose }

  const t = (now - m.local_t0_ms) / m.duration_ms
  if (t >= 1) return { ...m.to }
  if (t <= 0) return { ...m.from }
  const u = easeProgress(t, m.easing)
  return {
    x: m.from.x + (m.to.x - m.from.x) * u,
    y: m.from.y + (m.to.y - m.from.y) * u,
    z: m.from.z + (m.to.z - m.from.z) * u,
    r: m.from.r + (m.to.r - m.from.r) * u,
  }
}

export function busAnyRunning(): boolean {
  for (const a of arms.values()) {
    if (a.running) return true
    if (a.motion) {
      const t = (performance.now() - a.motion.local_t0_ms) / a.motion.duration_ms
      if (t < 1) return true
    }
  }
  return false
}

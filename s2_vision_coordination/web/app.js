const state = {
  scan: null,
  objects: [],
  selectedObjectId: null,
  activeArmIds: [],
  steps: [],
  plan: null,
  mode: "snapshot",
  destinationCatalog: new Map(),
  liveTimer: null,
  busy: false,
  displayFrame: null,
}

const $ = (id) => document.getElementById(id)
const scanImage = $("scan-image")
const visionFrame = $("vision-frame")

function showToast(message, isError = false) {
  const toast = $("toast")
  toast.textContent = message
  toast.style.background = isError ? "#a54c35" : "#1b3342"
  toast.classList.add("is-visible")
  window.clearTimeout(showToast.timer)
  showToast.timer = window.setTimeout(() => toast.classList.remove("is-visible"), 3200)
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok && !payload.error) {
    payload.error = `Request failed (${response.status})`
  }
  return payload
}

function setBusy(value) {
  state.busy = value
  $("scan-now").disabled = value
  $("confirm-plan").disabled = value || state.steps.length === 0 || state.plan?.status !== "draft"
  $("execute-plan").disabled = value || state.plan?.status !== "confirmed"
}

function colorClass(color) {
  const value = String(color || "unknown").toLowerCase()
  return ["green", "red", "blue"].includes(value) ? `color-${value}` : "color-unknown"
}

function selectedObject() {
  return state.objects.find((object) => String(object.id) === state.selectedObjectId) || null
}

function objectPosition(object) {
  const displayPosition = object.display_position
  if (displayPosition && Number.isFinite(Number(displayPosition.x)) && Number.isFinite(Number(displayPosition.y))) {
    return {
      left: Number(displayPosition.x) * 100,
      top: Number(displayPosition.y) * 100,
    }
  }
  const center = object.center_px
  const frame = state.displayFrame || state.scan?.frame || {}
  if (center && frame.width && frame.height) {
    return {
      left: (Number(center.x) / Number(frame.width)) * 100,
      top: (Number(center.y) / Number(frame.height)) * 100,
    }
  }
  return null
}

function updateFrameGeometry(frame) {
  const width = Number(frame?.width)
  const height = Number(frame?.height)
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return
  state.displayFrame = { width, height }
  visionFrame.style.aspectRatio = `${width} / ${height}`
}

function renderMarkers() {
  const container = $("object-markers")
  container.replaceChildren()
  state.objects.forEach((object) => {
    const marker = document.createElement("div")
    marker.className = `object-marker${String(object.id) === state.selectedObjectId ? " is-selected" : ""}`
    const position = objectPosition(object)
    if (!position || !Number.isFinite(position.left) || !Number.isFinite(position.top)) return
    marker.style.left = `${position.left}%`
    marker.style.top = `${position.top}%`
    const label = document.createElement("span")
    label.className = "marker-label"
    label.textContent = object.id
    const dot = document.createElement("span")
    dot.className = "marker-dot"
    marker.append(label, dot)
    marker.addEventListener("click", () => selectObject(String(object.id)))
    container.append(marker)
  })
}

function renderObjects() {
  const list = $("object-list")
  list.replaceChildren()
  $("object-count").textContent = String(state.objects.length)
  if (!state.objects.length) {
    const empty = document.createElement("div")
    empty.className = "empty-state"
    empty.textContent = "No scan loaded."
    list.append(empty)
    return
  }
  state.objects.forEach((object) => {
    const button = document.createElement("button")
    button.className = `object-row${String(object.id) === state.selectedObjectId ? " is-selected" : ""}`
    button.type = "button"
    button.addEventListener("click", () => selectObject(String(object.id)))
    const swatch = document.createElement("span")
    swatch.className = `color-swatch ${colorClass(object.color)}`
    const primary = document.createElement("span")
    primary.className = "object-primary"
    const id = document.createElement("span")
    id.className = "object-id"
    id.textContent = String(object.id)
    const detail = document.createElement("span")
    detail.className = "object-detail"
    detail.textContent = `${String(object.color || "unknown").toUpperCase()}${object.block ? ` / Block ${object.block}` : ""}`
    primary.append(id, detail)
    const arm = document.createElement("span")
    arm.className = "object-arm"
    arm.textContent = (object.reachable_by || []).join(", ") || "No arm"
    button.append(swatch, primary, arm)
    list.append(button)
  })
}

function selectObject(objectId) {
  state.selectedObjectId = objectId
  renderObjects()
  renderMarkers()
  const object = selectedObject()
  const summary = $("selection-summary")
  if (!object) {
    summary.textContent = "Select an object to configure a step."
    populateArms([])
    return
  }
  summary.textContent = `${object.id} / ${String(object.color || "unknown").toUpperCase()}${object.block ? ` / Block ${object.block}` : ""}`
  const arms = (object.reachable_by || []).filter((arm) => state.activeArmIds.includes(arm))
  populateArms(arms)
}

function populateArms(arms) {
  const select = $("arm-select")
  select.replaceChildren()
  if (!arms.length || state.plan?.status === "confirmed") {
    const option = document.createElement("option")
    option.textContent = arms.length ? "Plan is confirmed" : "No reachable active arm"
    select.append(option)
    select.disabled = true
    populateDestinations(null)
    return
  }
  arms.forEach((arm, index) => {
    const option = document.createElement("option")
    option.value = arm
    option.textContent = arm
    if (index === 0) option.selected = true
    select.append(option)
  })
  select.disabled = false
  populateDestinations(select.value)
}

async function populateDestinations(armId) {
  const select = $("destination-select")
  select.replaceChildren()
  state.destinationCatalog = new Map()
  if (!armId || state.plan?.status === "confirmed") {
    const option = document.createElement("option")
    option.textContent = armId ? "Plan is confirmed" : "Select an arm first"
    select.append(option)
    select.disabled = true
    $("custom-point").hidden = true
    $("add-step").disabled = true
    return
  }
  const options = await request(`/api/options?arm_id=${encodeURIComponent(armId)}`)
  const destinations = [...(options.named || []), ...(options.zones || []), { kind: "point", name: "custom", label: "Custom point", frame: "workspace" }]
  destinations.forEach((destination, index) => {
    const key = `${destination.kind}:${destination.name}`
    state.destinationCatalog.set(key, destination)
    const option = document.createElement("option")
    option.value = key
    option.textContent = destination.label || destination.name
    if (index === 0) option.selected = true
    select.append(option)
  })
  select.disabled = false
  renderDestinationInputs()
  $("add-step").disabled = !selectedObject()
}

function renderDestinationInputs() {
  const destination = state.destinationCatalog.get($("destination-select").value)
  $("custom-point").hidden = !destination || destination.name !== "custom"
}

function destinationForForm() {
  const destination = state.destinationCatalog.get($("destination-select").value)
  if (!destination) return null
  if (destination.name === "custom") {
    const x = Number($("destination-x").value)
    const y = Number($("destination-y").value)
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null
    return { kind: "point", name: "custom point", frame: "workspace", x, y }
  }
  if (destination.kind === "point") {
    return { kind: "point", name: destination.name, frame: "workspace", ...destination.point }
  }
  return { kind: "named", name: destination.name, frame: "arm" }
}

async function syncPlan() {
  if (!state.steps.length) {
    state.plan = null
    renderPlan()
    return true
  }
  const result = await request("/api/plan", {
    method: "POST",
    body: JSON.stringify({ steps: state.steps }),
  })
  if (!result.ok) {
    showToast(result.error || "Plan validation failed", true)
    return false
  }
  state.plan = result
  renderPlan()
  return true
}

async function addStep() {
  if (state.plan?.status === "confirmed") return
  const object = selectedObject()
  const armId = $("arm-select").value
  const destination = destinationForForm()
  if (!object || !armId || !destination) {
    showToast("Choose an object, arm, and destination", true)
    return
  }
  state.steps.push({ object_id: String(object.id), arm_id: armId, destination })
  if (!(await syncPlan())) state.steps.pop()
  else showToast(`${object.id} added as step ${state.steps.length}`)
}

function renderPlan() {
  const list = $("step-list")
  list.replaceChildren()
  const plan = state.plan
  const steps = plan?.steps || []
  $("metric-plan").textContent = plan?.status ? String(plan.status).replace("_", " ") : "Draft"
  $("plan-meta").textContent = steps.length ? `${steps.length} sequential step${steps.length === 1 ? "" : "s"}` : "No steps"
  if (!steps.length) {
    const empty = document.createElement("div")
    empty.className = "empty-state"
    empty.textContent = "Add a selected object to create the first step."
    list.append(empty)
  } else {
    steps.forEach((step, index) => {
      const row = document.createElement("div")
      row.className = "step-row"
      const number = document.createElement("span")
      number.className = "step-number"
      number.textContent = String(index + 1)
      const main = document.createElement("span")
      main.className = "step-main"
      const title = document.createElement("span")
      title.className = "step-title"
      title.textContent = `${step.object_id} / ${String(step.color || "").toUpperCase()}`
      const subtitle = document.createElement("span")
      subtitle.className = "step-subtitle"
      subtitle.textContent = step.block ? `Block ${step.block}` : "Vision object"
      main.append(title, subtitle)
      const arm = document.createElement("span")
      arm.className = "step-arm"
      arm.textContent = step.arm_id
      const destination = document.createElement("span")
      destination.className = "step-destination"
      destination.textContent = step.destination?.name || "Destination"
      const status = document.createElement("span")
      status.className = `step-status ${step.status || ""}`
      status.textContent = step.status || "pending"
      const controls = document.createElement("span")
      controls.className = "step-controls"
      const up = document.createElement("button")
      up.className = "icon-button"
      up.type = "button"
      up.title = "Move step up"
      up.textContent = "^"
      up.disabled = index === 0 || plan?.status !== "draft"
      up.addEventListener("click", () => moveStep(index, -1))
      const down = document.createElement("button")
      down.className = "icon-button"
      down.type = "button"
      down.title = "Move step down"
      down.textContent = "v"
      down.disabled = index === steps.length - 1 || plan?.status !== "draft"
      down.addEventListener("click", () => moveStep(index, 1))
      const remove = document.createElement("button")
      remove.className = "icon-button"
      remove.type = "button"
      remove.title = "Remove step"
      remove.textContent = "x"
      remove.disabled = plan?.status !== "draft"
      remove.addEventListener("click", () => removeStep(index))
      controls.append(up, down, remove)
      row.append(number, main, arm, destination, status, controls)
      list.append(row)
    })
  }
  const editable = !plan || plan.status === "draft"
  $("confirm-plan").disabled = state.busy || !steps.length || !editable
  $("execute-plan").disabled = state.busy || plan?.status !== "confirmed"
  $("clear-plan").disabled = state.busy || !steps.length
  $("execution-note").textContent = plan?.status === "confirmed"
    ? "Confirmed. Execute will re-scan before every step."
    : "Plan is read-only until you confirm it."
}

async function moveStep(index, direction) {
  const next = index + direction
  if (next < 0 || next >= state.steps.length || state.plan?.status !== "draft") return
  const [step] = state.steps.splice(index, 1)
  state.steps.splice(next, 0, step)
  await syncPlan()
}

async function removeStep(index) {
  if (state.plan?.status !== "draft") return
  state.steps.splice(index, 1)
  await syncPlan()
}

function updateScan(payload) {
  if (!payload?.ok || !payload.scan) {
    showToast(payload?.error || "Camera scan failed", true)
    return
  }
  state.scan = payload.scan
  state.objects = payload.scan.objects || []
  updateFrameGeometry(payload.scan.display_frame || payload.scan.frame)
  state.activeArmIds = payload.active_arm_ids || state.activeArmIds
  if (!state.objects.some((object) => String(object.id) === state.selectedObjectId)) state.selectedObjectId = null
  renderObjects()
  renderMarkers()
  $("metric-objects").textContent = String(state.objects.length)
  $("metric-reachable").textContent = String(state.objects.filter((object) => (object.reachable_by || []).length).length)
  $("metric-calibration").textContent = payload.scan.workspace?.saved ? "Ready" : "Review"
  $("frame-source").textContent = `${payload.capture_mode || "snapshot"} / ${payload.scan.camera_id || "Cam1"}`
  $("frame-time").textContent = payload.scan.timestamp ? new Date(payload.scan.timestamp).toLocaleTimeString() : "--"
  scanImage.src = `${payload.image_url || "/api/snapshot-image"}?t=${Date.now()}`
  scanImage.hidden = false
  $("image-placeholder").hidden = true
  if (state.selectedObjectId) selectObject(state.selectedObjectId)
}

scanImage.addEventListener("load", () => {
  if (!state.scan?.display_frame && scanImage.naturalWidth && scanImage.naturalHeight) {
    updateFrameGeometry({ width: scanImage.naturalWidth, height: scanImage.naturalHeight })
  }
  renderMarkers()
})

async function scanNow(silent = false, force = false) {
  if (state.busy && !force) return
  setBusy(true)
  try {
    const payload = await request("/api/scan", {
      method: "POST",
      body: JSON.stringify({ camera_id: "Cam1", mode: state.mode }),
    })
    if (payload.ok) {
      updateScan(payload)
      if (!silent) showToast(`Scan complete: ${state.objects.length} object${state.objects.length === 1 ? "" : "s"}`)
    } else if (!silent) {
      showToast(payload.error || "Camera scan failed", true)
    }
  } catch (error) {
    if (!silent) showToast(String(error), true)
  } finally {
    setBusy(false)
    renderPlan()
  }
}

async function confirmPlan() {
  if (!state.steps.length) return
  setBusy(true)
  const result = await request("/api/plan/confirm", { method: "POST", body: "{}" })
  if (result.ok) {
    state.plan = result
    showToast("Plan confirmed. Ready to execute.")
    renderPlan()
    selectObject(state.selectedObjectId)
  } else showToast(result.error || "Could not confirm plan", true)
  setBusy(false)
}

async function executePlan() {
  if (state.plan?.status !== "confirmed") return
  if (!window.confirm("Re-scan and execute this sequence through ARC guardrails?")) return
  setBusy(true)
  const result = await request("/api/execute", { method: "POST", body: "{}" })
  if (result.plan) state.plan = result.plan
  if (result.ok) {
    showToast("Sequence completed")
    await scanNow(true, true)
  } else showToast(result.error || result.plan?.steps?.find((step) => step.status === "failed")?.step_id || "Sequence stopped", true)
  setBusy(false)
  renderPlan()
}

async function clearPlan() {
  if (state.busy) return
  await request("/api/plan/reset", { method: "POST", body: "{}" })
  state.steps = []
  state.plan = null
  renderPlan()
  selectObject(state.selectedObjectId)
  showToast("Plan cleared")
}

async function resetScene() {
  if (state.busy || !window.confirm("Reset the simulator scene and clear the plan?")) return
  const result = await request("/api/sim/reset", { method: "POST", body: "{}" })
  if (result.ok) {
    state.steps = []
    state.plan = null
    renderPlan()
    await scanNow()
    showToast("Simulation reset")
  } else showToast(result.error || "Scene reset failed", true)
}

function setMode(mode) {
  state.mode = mode
  document.querySelectorAll(".mode-button").forEach((button) => button.classList.toggle("is-active", button.dataset.mode === mode))
  if (state.liveTimer) window.clearInterval(state.liveTimer)
  state.liveTimer = null
  if (mode === "live") {
    scanNow()
    state.liveTimer = window.setInterval(() => scanNow(true), 2500)
  }
}

async function init() {
  scanImage.hidden = true
  $("image-placeholder").hidden = false
  try {
    const health = await request("/api/health")
    if (health.ok) {
      $("runtime-status").textContent = health.simulation_mode ? "Simulation" : "Live runtime"
      $("runtime-status").style.color = health.simulation_mode ? "#2a876d" : "#b9822e"
      state.activeArmIds = health.active_arm_ids || []
    } else throw new Error(health.error || "Backend unavailable")
    await request("/api/options")
    await scanNow()
  } catch (error) {
    $("runtime-status").textContent = "Offline"
    $("runtime-status").style.color = "#c76442"
    showToast(String(error), true)
  }
}

$("scan-now").addEventListener("click", () => scanNow())
$("reset-scene").addEventListener("click", resetScene)
$("arm-select").addEventListener("change", (event) => populateDestinations(event.target.value))
$("destination-select").addEventListener("change", renderDestinationInputs)
$("add-step").addEventListener("click", addStep)
$("confirm-plan").addEventListener("click", confirmPlan)
$("execute-plan").addEventListener("click", executePlan)
$("clear-plan").addEventListener("click", clearPlan)
document.querySelectorAll(".mode-button").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)))

renderPlan()
init()

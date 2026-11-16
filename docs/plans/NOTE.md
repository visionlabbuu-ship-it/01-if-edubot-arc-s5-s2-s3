# ARC Simulator — Visual Scene Editor & Play Mode

## Context (for session handoff)

This is a continuation session. Previous work established:
- `sim_world.py` — WorldModel singleton, persists to `runtime_state/world_state.json`
- `sim_robot.py` — SimRobot subclassing MG400Robot, overrides I/O layer only
- `camera_sim.py` — CameraSimulator generates scan payloads from world state
- `fleet_controller.py` — has SIM_MODE branches in `pick_at`, `place_at`, `place_to_named_target`
- `camera_controller.py` — `simulate_scan` respects color filter
- `arc.py` — CLI entry point, wires everything together
- `config.py` — has `SIM_MODE` env var
- `_sim_integration.py` — standalone world model tests (PASS)
- `_sim_arc_test.py` — full arc.py simulation tests (PASS)

Current sim capabilities: scan, pick, place (named + pose), motion simulation.
Missing: spawn/remove/move objects, visual editor UI, play mode, sim toggle.

## Architecture Overview

```
Browser UI (scene_editor.html + scene_editor.js)
  ├── Chat Panel (left) — user sends natural language commands
  └── Scene View (right) — canvas showing objects, workspace zones, trash icon
       └── Drag & Drop: hold object → trash appears bottom-right → drag to delete

arc.py (CLI + API entry point)
  ├── GET /api/sim/scene → current scene state
  ├── POST /api/sim/spawn → spawn object
  ├── POST /api/sim/remove → remove object
  ├── POST /api/sim/move → move object (drag drop)
  ├── POST /api/sim/reset → reset to default world
  ├── POST /api/sim/toggle → toggle sim mode
  └── Existing commands: camera scan, arm pick/place, etc.

FleetController (SIM_MODE)
  ├── WorldModel singleton (disk-persisted)
  ├── SimRobot instances
  ├── SimCamera
  └── SIM_MODE flag (config or env)
```

## 1. Scene Editor — Visual Layout

### Layout
```
┌──────────────────────────────────────────────────────────────┐
│  ARC Simulator                                                │
├──────────────────────────┬───────────────────────────────────┤
│                          │                                   │
│  [Chat Panel]            │  [Scene View Canvas]              │
│                          │                                   │
│  ┌────────────────────┐  │  ┌─────────────────────────────┐  │
│  │ User: pick obj-1   │  │  │  ┌───┐ ┌───┐ ┌───┐         │  │
│  │                    │  │  │  │obj │ │obj │ │obj │         │  │
│  │                    │  │  │  │-1  │ │-2  │ │-3  │         │  │
│  │                    │  │  │  └───┘ └───┘ └───┘         │  │
│  │                    │  │  │                             │  │
│  │                    │  │  │  ┌─────────────────────────┐│  │
│  │                    │  │  │  │  Shared Zone            ││  │
│  │                    │  │  │  └─────────────────────────┘│  │
│  └────────────────────┘  │  │                             │  │
│  ┌────────────────────┐  │  │  ┌─────────────────────────┐│  │
│  │ Input:             │  │  │  │  Storage Zone           ││  │
│  │ [type...]          │  │  │  └─────────────────────────┘│  │
│  └────────────────────┘  │  │                             │  │
│                          │  │                             │  │
│                          │  │  [Trash Icon]               │  │
│                          │  │  (appears when dragging)    │  │
│                          │  └─────────────────────────────┘  │
├──────────────────────────┴───────────────────────────────────┤
│  [Sim Mode: ON/OFF Toggle]  [Reset Scene]  [Spawn Menu ▼]  │
└──────────────────────────────────────────────────────────────┘
```

### Controls
- **Sim Mode Toggle** — switches between SIM and real mode (toggles `SIM_MODE` in config)
- **Reset Scene** — resets world to DEFAULT_OBJECTS (calls `WorldModel.reset()`)
- **Spawn Menu** — dropdown with color options: Green, Red, Blue
- **Drag Object** — click + hold on any object → mouse cursor follows it
- **Trash Icon** — appears bottom-right of canvas when dragging → drag object onto it to delete
- **Chat Panel** — left side, shows conversation history + input field

## 2. Drag & Drop Interaction

### Sequence
1. User clicks on object icon (hold)
2. Object follows mouse cursor
3. Trash icon appears at bottom-right of canvas (icon: 🗑️)
4. User drags object over trash area
5. Object snaps to trash, is removed from world
6. Trash icon disappears

### Implementation
- Use HTML5 Drag & Drop API or mousedown/mousemove/mouseup events
- Track `dragging` state in JS
- Render trash icon conditionally when `dragging`
- On drop on trash: call `POST /api/sim/remove` with object ID
- Remove from world state + update canvas

### Object Rendering
- Objects are colored circles (green/red/blue)
- Labels below: `obj-1`, `obj-2`, etc.
- Position calculated from `robot_plane.x` / `robot_plane.y`
- Scaled to canvas coordinates

## 3. Spawn / Remove / Move API

### `GET /api/sim/scene`
Return current scene state for rendering.

```json
{
  "objects": [
    {
      "id": "obj-1",
      "color": "green",
      "robot_plane": {"x": 158.4, "y": -19.8}
    }
  ],
  "held": null,
  "zones": [...]
}
```

### `POST /api/sim/spawn`
Spawn a new object with a given color.

Request:
```json
{"color": "green"}
```

Response:
```json
{
  "ok": true,
  "object": {
    "id": "obj-4",
    "color": "green",
    "robot_plane": {"x": 250, "y": -50}
  }
}
```
- Auto-generate ID: `obj-{n+1}` where n = max existing
- Default position: center of workspace (250, -50)
- Add to `DEFAULT_OBJECTS` or directly to world state
- Return new object with its ID

### `POST /api/sim/remove`
Remove an object from the scene.

Request:
```json
{"id": "obj-3"}
```

Response:
```json
{"ok": true}
```
- Remove from world's `_objects` list
- Persist to disk

### `POST /api/sim/move`
Move an object to a new position (used when dragging on canvas).

Request:
```json
{"id": "obj-1", "x": 187.41, "y": -311.67}
```

Response:
```json
{"ok": true, "object": {...}}
```
- Update `robot_plane` of the object
- Persist to disk

### `POST /api/sim/reset`
Reset world to DEFAULT_OBJECTS.

Response:
```json
{"ok": true}
```
- Calls `WorldModel.reset()`

### `POST /api/sim/toggle`
Toggle SIM_MODE.

Response:
```json
{"ok": true, "sim_mode": true}
```
- Updates `config.SIM_MODE`
- Re-initializes FleetController with SimRobot if turning on
- Falls back to MG400Robot if turning off

## 4. Chat Integration

### Flow
1. User types command in chat input
2. Send to `arc.py` via subprocess/API
3. Display response in chat
4. Refresh scene view after any action that changes world state

### Supported Commands
Commands are the same as arc.py CLI:
- `scan` / `scan green` — camera scan
- `pick Arm1 at obj-1` — pick an object by name
- `place Arm1 at storage_zone` — place at named target
- `place Arm1 at x=100, y=-100` — place at pose
- `move Arm1 to home` — move to named pose

### Natural Language Parsing
Simple keyword matching:
- "scan" → `arc.py camera scan`
- "scan <color>" → `arc.py camera scan --color <color>`
- "pick <arm> at <object>" → `arc.py arm pick-at <arm> --x <obj.x> --y <obj.y>`
- "place <arm> at <destination>" → `arc.py arm place-at-named <arm> --destination <destination>`
- "reset scene" → `POST /api/sim/reset`
- "add <color>" → `POST /api/sim/spawn {"color": "<color>"}`
- "remove <object>" → `POST /api/sim/remove {"id": "<object>"}`

### Response Handling
- Parse JSON output from arc.py
- Display formatted in chat
- If world state changed → refresh scene view

## 5. Handoff Logic

### Principle
Handoff follows the real system logic: if one arm cannot reach an object, the other arm must assist.

### Reachability Rules
Defined in `DEFAULT_OBJECTS` → `reachable_by` field:
- `obj-1` → only Arm1
- `obj-2` → Arm1 + Arm2
- `obj-3` → Arm1 + Arm2

### Handoff Flow (when AI detects need)
1. User requests: "place obj-2 at Arm2's drop_zone"
2. AI checks: obj-2 reachable by Arm2 ✓ → Arm2 picks directly
3. If obj-2 was only reachable by Arm1:
   - Arm1 picks obj-2
   - Arm1 moves to handoff_zone (shared)
   - Arm2 picks from handoff_zone
   - Arm2 moves to drop_zone
   - Arm2 places at drop_zone

### Handoff Zone
Defined in `ARM_NAMED_TARGETS["Arm1"]["handoff_zone"]`:
```json
{
  "x": 395.0,
  "y": 0.0,
  "hover_z": 83.41,
  "place_z": -152.0
}
```

### Implementation
- In `fleet_controller.py`, when `pick_at` is called, check `reachable_by`
- If preferred arm can't reach → pick with available arm
- If destination requires different arm → plan handoff
- Use `build_handoff_plan` from `workspace_model.py`

## 6. Sim Mode Toggle

### Option A: Config-based toggle (simplest)
Add `SIM_MODE = True` to config.py (or env var).
- Pro: simplest
- Con: requires code change or env var

### Option B: Runtime toggle via API (recommended)
`POST /api/sim/toggle` updates `config.SIM_MODE` in memory.
- Pro: live toggle, no restart
- Con: requires FleetController reinitialization

### Implementation
```python
# config.py
SIM_MODE = os.environ.get("ARC_SIM_MODE", "0") == "1"
SIM_MODE_FILE = Path(__file__).parent / "runtime_state" / "sim_mode.json"

def set_sim_mode(on):
    config.SIM_MODE = on
    SIM_MODE_FILE.write_text(json.dumps({"sim_mode": on}))
```

On toggle:
- Turn ON: destroy existing MG400Robot instances, create SimRobot instances
- Turn OFF: destroy SimRobot, create MG400Robot (will fail silently since no hardware)

### Option C: Both
Default to env var, but allow runtime override via API/UI.

## 7. Persistence Decision

### Current: Disk-persisted singleton
Problem: world state persists across sessions. For a playground, this is bad — you want fresh scenes.

### Solution
- During scene setup: world is in-memory only (not persisted)
- During play mode: world persists within session
- On "reset scene": full reset to DEFAULT_OBJECTS
- On sim toggle: reset world to prevent stale state

Alternative: persist only when explicitly saved.

## 8. Files to Create/Modify

### New Files
- `browser_ui/scene_editor.html` — scene editor UI
- `browser_ui/scene_editor.js` — scene editor logic (drag & drop, chat, API calls)
- `browser_ui/static/scene_editor.css` — styles

### Modified Files
- `arc.py` — add `/api/sim/*` route handlers
- `fleet_controller.py` — add scene management methods
- `config.py` — add `set_sim_mode()` helper, maybe `SIM_MODE_FILE`
- `sim_world.py` — add `spawn_object()`, `remove_object()`, `move_object()` methods

### Key Dependencies
- `camera_controller.py` — already has `simulate_scan`
- `sim_robot.py` — already has world-aware pick/place
- `workspace_model.py` — has `build_handoff_plan`
- `task_system.py` — already has task infrastructure

## 9. Implementation Order

1. **sim_world.py** — add `spawn_object()`, `remove_object()`, `move_object()` methods
2. **fleet_controller.py** — expose scene management through controller
3. **arc.py** — add `/api/sim/*` routes
4. **scene_editor.html + .js** — visual editor with drag & drop
5. **chat integration** — wire chat to arc.py commands
6. **sim toggle** — wire toggle button
7. **handoff logic** — verify handoff works with sim mode
8. **polish** — animations, error states, loading indicators

## 10. Edge Cases & Considerations

- **Object ID collision**: spawn generates `obj-{n+1}`, but what if objects were removed? Should use highest existing ID + 1.
- **Dragging off-canvas**: handle mouse leaving canvas area.
- **Trash icon positioning**: bottom-right of scene canvas, not entire page.
- **Sim toggle mid-play**: if toggling from OFF to ON, reset world to avoid mixing real/sim state.
- **Color palette**: currently Green, Red, Blue — defined in DEFAULT_OBJECTS.
- **Canvas coordinate mapping**: need to map robot plane coordinates (mm) to canvas pixels.
- **World state file**: if playing in sim mode, should NOT persist to disk between commands. Or persist but with explicit reset.
- **Multiple users**: single-user only for now.
- **Performance**: scene re-render on every action, not continuous.

## 11. Testing

After implementing:
1. Test spawn/remove via API directly
2. Test drag & drop interaction
3. Test chat commands with visual feedback
4. Test sim toggle
5. Test handoff flow
6. Test reset scene
7. Test persistence behavior

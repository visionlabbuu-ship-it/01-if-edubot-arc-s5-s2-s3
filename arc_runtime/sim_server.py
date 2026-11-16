"""ARC Simulator — HTTP API + static server สำหรับ Scene Editor / Play Mode

รันด้วย:  python sim_server.py            (ค่าเริ่มต้น 127.0.0.1:8770)
หรือผ่าน:  python arc.py serve --port 8770

ทุก endpoint ทำงานบน WorldModel singleton ที่ persist ลงดิสก์ ดังนั้นสถานะฉาก
จะตรงกันทั้ง UI และ CLI (arc.py) เสมอ

Routes:
  GET  /                       → browser_ui/scene_editor.html
  GET  /static/<file>          → browser_ui/static/<file>
  GET  /api/sim/scene          → สถานะฉากปัจจุบัน (objects, held, zones, sim_mode)
  POST /api/sim/spawn          → {"color","x?","y?"}        สร้างวัตถุ
  POST /api/sim/remove         → {"id"}                     ลบวัตถุ
  POST /api/sim/move           → {"id","x","y"}             ย้ายวัตถุ (drag-drop)
  POST /api/sim/reset          → รีเซ็ตโลกกลับ DEFAULT_OBJECTS
  POST /api/sim/toggle         → {"on?"}                    สลับ sim mode
  POST /api/sim/chat           → {"message"}                สั่งงานด้วยภาษาธรรมชาติ
  GET  /api/teacher/lock       → ขอบเขต sim + author (capabilities/skeletons)
  GET  /api/teacher/capabilities → capability + demo skeleton
  GET  /api/teacher/skeletons  → question skeletons (optional ?capability_id=)
  GET  /api/teacher/catalog    → รายการโจทย์ UC4 (catalog + session)
  GET  /api/teacher/status     → สถานะบทเรียน
  POST /api/teacher/chat       → คุย tutor {message} → ออกโจทย์ + เริ่มเรียนได้
  POST /api/teacher/generate   → สร้างชุด MCQ จาก skeleton {capability_id|topic, use_llm?}
  POST /api/teacher/load_set   → โหลด challenges ที่ validate แล้วเข้า session
  POST /api/teacher/validate   → ตรวจ challenge / MCQ text
  POST /api/teacher/start      → {"challenge_id?"}
  POST /api/teacher/demo       → ครูโชว์ท่า
  POST /api/teacher/check      → ตรวจจาก scene/pose
  POST /api/teacher/next       → โจทย์ถัดไป
  POST /api/teacher/reset      → รีเซ็ต session นักเรียน
  POST /api/teacher/waypoint   → {"arm","x","y","z?"} log มุม L3
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config
from .config import ROBOT_FLEET_CONFIG
from .fleet_controller import FleetController
from .chat_router import handle_chat
from .state_paths import project_path


def _llm_info() -> dict:
    info = {
        "ok": True,
        "enabled": config.SIM_LLM_ENABLED,
        "base_url": config.SIM_LLM_BASE_URL,
        "model": config.SIM_LLM_MODEL,
        "has_api_key": bool(config.SIM_LLM_API_KEY),
        "sidecar_url": config.JETSON_SIDECAR_BASE_URL,
        "markers_enabled": config.JETSON_MARKERS_ENABLED,
    }
    try:
        from .jetson_sidecar import probe

        jetson = probe()
        info["jetson"] = {
            "ok": jetson.get("ok"),
            "condition_id": jetson.get("condition_id"),
            "quantization_label": jetson.get("quantization_label"),
            "api_alias": jetson.get("api_alias") or jetson.get("model"),
            "model_filename": jetson.get("model_filename"),
            "model_path": jetson.get("model_path"),
            "model_family": jetson.get("model_family"),
            "llama_ready": jetson.get("llama_ready"),
            "litellm_ready": jetson.get("litellm_ready"),
            "chat_url": jetson.get("chat_url"),
            "sidecar": jetson.get("sidecar"),
        }
        info["loaded_q"] = jetson.get("quantization_label") or jetson.get("condition_id")
    except Exception as exc:  # noqa: BLE001
        info["jetson"] = {"ok": False, "error": str(exc)}
    return info

PROJECT_ROOT = project_path()
BROWSER_UI = PROJECT_ROOT / "browser_ui"

# modules/ (UC4 teacher fork) lives next to arc_runtime at project root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# กัน controller/world ถูกแก้พร้อมกันจากหลาย request (ThreadingHTTPServer)
_LOCK = threading.RLock()

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _build_controller():
    """สร้าง FleetController ใหม่ — sim หรือ real ตาม config.SIM_MODE ปัจจุบัน"""
    return FleetController(ROBOT_FLEET_CONFIG)


class _State:
    """ถือ controller ปัจจุบัน สร้างใหม่เมื่อ toggle โหมด"""
    controller = None

    @classmethod
    def get(cls):
        if cls.controller is None:
            cls.controller = _build_controller()
        return cls.controller

    @classmethod
    def rebuild(cls):
        if cls.controller is not None:
            try:
                cls.controller.disconnect_all()
            except Exception:
                pass
        cls.controller = _build_controller()
        return cls.controller


class SimRequestHandler(BaseHTTPRequestHandler):
    server_version = "ARCSim/1.0"

    # ---- helpers ----
    def _add_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path):
        if not path.exists() or not path.is_file():
            self._send_json({"ok": False, "error": "Not found"}, status=404)
            return
        data = path.read_bytes()
        ctype = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None  # signal malformed body

    def _safe_static(self, rel: str) -> Path:
        """กัน path traversal — รับเฉพาะไฟล์ใต้ browser_ui/static"""
        base = (BROWSER_UI / "static").resolve()
        target = (base / rel).resolve()
        if base not in target.parents and target != base:
            return BROWSER_UI / "scene_editor.html"
        return target

    def log_message(self, fmt, *args):
        pass  # เงียบ — ไม่ spam stdout

    # ---- routing ----
    def do_OPTIONS(self):
        """CORS preflight — ตอบ 204 พร้อม headers"""
        self.send_response(204)
        self._add_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route in ("/", "/index.html", "/scene_editor.html"):
                self._send_file(BROWSER_UI / "scene_editor.html")
                return
            if route.startswith("/static/"):
                self._send_file(self._safe_static(route[len("/static/"):]))
                return
            if route == "/scene_editor.js":
                self._send_file(BROWSER_UI / "scene_editor.js")
                return
            if route == "/api/sim/scene":
                # ไม่ถือ _LOCK ยาว — ต้องอ่าน pose กลางทางขณะแขน animate
                # (chat/motion ถือ robot.lock เป็นช่วงสั้นๆ เท่านั้น)
                ctrl = _State.get()
                self._send_json(ctrl.scene_state())
                return
            if route == "/api/sim/arms":
                # lightweight pose + trajectory (~25–50 FPS poll จาก UI)
                self._send_json(_State.get().arms_motion_state())
                return
            if route == "/api/sim/llm_info":
                self._send_json(_llm_info())
                return
            if route.startswith("/api/teacher/"):
                from modules.teacher.api import handle_teacher_get

                qs = {k: (v[0] if v else "") for k, v in parse_qs(parsed.query).items()}
                payload, status = handle_teacher_get(_State.get(), route, qs)
                self._send_json(payload, status=status)
                return
            self._send_json({"ok": False, "error": f"Unknown route {route}"}, status=404)
        except Exception as exc:  # noqa: BLE001 — surface any handler error as JSON
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def do_POST(self):
        parsed = urlparse(self.path)
        route = parsed.path
        body = self._read_json_body()
        if body is None:
            self._send_json({"ok": False, "error": "Malformed JSON body"}, status=400)
            return
        try:
            # chat/motion/teacher demo ใช้เวลานาน (animate) — ห้ามถือ _LOCK ทั้ง request
            # ไม่งั้น GET /api/sim/scene จะค้างจน move จบ = 3D ยังวาป
            if route == "/api/sim/chat" or route.startswith("/api/teacher/"):
                payload, status = self._dispatch_post(route, body)
            else:
                with _LOCK:
                    payload, status = self._dispatch_post(route, body)
            self._send_json(payload, status=status)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _dispatch_post(self, route: str, body: dict):
        if route == "/api/sim/spawn":
            ctrl = _State.get()
            result = ctrl.spawn_object(
                color=body.get("color", "green"),
                x=float(body.get("x", 250.0)),
                y=float(body.get("y", -50.0)),
            )
            return result, (200 if result.get("ok") else 400)

        if route == "/api/sim/remove":
            obj_id = body.get("id")
            if not obj_id:
                return {"ok": False, "error": "Missing 'id'"}, 400
            result = _State.get().remove_object(obj_id)
            return result, (200 if result.get("ok") else 400)

        if route == "/api/sim/move":
            obj_id = body.get("id")
            if not obj_id or "x" not in body or "y" not in body:
                return {"ok": False, "error": "Require 'id', 'x', 'y'"}, 400
            result = _State.get().move_object(obj_id, float(body["x"]), float(body["y"]))
            return result, (200 if result.get("ok") else 400)

        if route == "/api/sim/reset":
            result = _State.get().reset_scene()
            return result, (200 if result.get("ok") else 400)

        if route == "/api/sim/toggle":
            target = body.get("on")
            if config.sim_mode_env_locked():
                # Process started with ARC_SIM_MODE=… — chat SIM/REAL is the real control.
                # Ignore accidental panel toggles that would blank the 3D scene.
                return {
                    "ok": True,
                    "sim_mode": config.SIM_MODE,
                    "locked_by_env": True,
                    "message": "sim_mode locked by ARC_SIM_MODE env; use header SIM/REAL for chat routing",
                }, 200
            new_mode = (not config.SIM_MODE) if target is None else bool(target)
            config.set_sim_mode(new_mode)
            _State.rebuild()
            return {"ok": True, "sim_mode": config.SIM_MODE, "locked_by_env": False}, 200

        if route == "/api/sim/chat":
            message = body.get("message", "")
            if not str(message).strip():
                return {"ok": False, "error": "Empty message"}, 400
            result = handle_chat(_State.get(), str(message))
            return result, 200

        if route == "/api/sim/llm_info":
            return _llm_info(), 200

        if route.startswith("/api/teacher/"):
            from modules.teacher.api import handle_teacher_post

            return handle_teacher_post(_State.get(), route, body)

        return {"ok": False, "error": f"Unknown route {route}"}, 404


def serve(host: str = "0.0.0.0", port: int = 8770):
    httpd = ThreadingHTTPServer((host, port), SimRequestHandler)
    mode = "SIM" if config.SIM_MODE else "REAL"
    llm = (
        f"LLM {config.SIM_LLM_MODEL} @ {config.SIM_LLM_BASE_URL}"
        if config.SIM_LLM_ENABLED
        else "LLM off (keyword only)"
    )
    print(f"ARC Simulator server -> http://{host}:{port}  (mode: {mode}, {llm})")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        httpd.server_close()
        if _State.controller is not None:
            try:
                _State.controller.disconnect_all()
            except Exception:
                pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ARC Simulator HTTP server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--bind", default=None, help="Bind host (overrides --host)")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    serve(args.bind or args.host, args.port)

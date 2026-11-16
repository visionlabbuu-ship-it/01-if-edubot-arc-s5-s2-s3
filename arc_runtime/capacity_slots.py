"""Named capacity slots built from black marker scan.

Model:
  black marker (geometry) -> named slot (like inner-box labels)
  optional logical group over many slots
  occupancy: black visible => empty; colored object => occupied; otherwise unknown

This is separate from ARM_NAMED_TARGETS storage_zone/drop_zone single poses.
Those remain park/place fallbacks; capacity routing should prefer empty named slots.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .capacity_scan import (
    DEFAULT_OUTPUT_DIR as CAPACITY_SCAN_DIR,
    scan_capacity_image,
    write_json as write_scan_json,
)
from .state_paths import runtime_state_path

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


DEFAULT_SLOTS_PATH = runtime_state_path("capacity_slots.json")
DEFAULT_LATEST_SCAN = CAPACITY_SCAN_DIR / "latest_report.json"
CAPACITY_VERSION = 2

# Default layout for the current 16-marker board:
# left/right columns are storage groups; each has top/bottom clusters
# each side has top/bottom clusters of 4.
DEFAULT_GROUP_BY_SIDE = {
    "left": "storage",
    "right": "storage",
}
DEFAULT_CLUSTER_PREFIX = {
    ("left", "top"): "LT",
    ("left", "bottom"): "LB",
    ("right", "top"): "RT",
    ("right", "bottom"): "RB",
}

MATCH_DISTANCE_PX = 80.0
OCCUPANCY_MATCH_PX = 90.0
OCCUPANCY_COLOR_FRACTION = 0.10
OCCUPANCY_COLOR_RADIUS_SCALE = 0.78
OCCUPANCY_COLOR_HSV_RANGES = {
    "red": ((0, 55, 25), (12, 255, 255)),
    "green": ((35, 35, 20), (95, 255, 255)),
    "blue": ((85, 35, 20), (145, 255, 255)),
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def empty_registry():
    return {
        "ok": True,
        "version": CAPACITY_VERSION,
        "purpose": "named_capacity_slots",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "source_scan": None,
        "expected_count": 16,
        "slot_count": 0,
        "groups": {
            "storage": {
                "name": "storage",
                "label": "Storage",
                "side": "left",
                "description": "Named capacity slots on the left board edge",
            },
            "drop": {
                "name": "drop",
                "label": "Drop",
                "side": "right",
                "description": "Named capacity slots on the right board edge",
            },
        },
        "slots": [],
        "summary": {
            "empty": 0,
            "occupied": 0,
            "unknown": 0,
            "by_group": {},
        },
    }


def load_slots(path=DEFAULT_SLOTS_PATH):
    target = Path(path)
    if not target.exists():
        return empty_registry()
    payload = load_json(target)
    payload.setdefault("ok", True)
    payload.setdefault("version", CAPACITY_VERSION)
    payload.setdefault("groups", empty_registry()["groups"])
    payload.setdefault("slots", [])
    payload.setdefault("summary", empty_registry()["summary"])
    return payload


def save_slots(registry, path=DEFAULT_SLOTS_PATH):
    payload = deepcopy(registry)
    payload["updated_at"] = utc_now()
    payload["slot_count"] = len(payload.get("slots") or [])
    payload["summary"] = summarize_slots(payload.get("slots") or [])
    write_json(path, payload)
    return payload


def summarize_slots(slots):
    summary = {
        "empty": 0,
        "occupied": 0,
        "unknown": 0,
        "by_group": {},
        "occupied_by_color": {},
    }
    for slot in slots:
        status = str(slot.get("occupancy") or "unknown")
        if status not in summary:
            summary[status] = 0
        summary[status] = int(summary.get(status, 0)) + 1
        group = str(slot.get("group") or "ungrouped")
        bucket = summary["by_group"].setdefault(
            group,
            {"total": 0, "empty": 0, "occupied": 0, "unknown": 0},
        )
        bucket["total"] += 1
        if status in bucket:
            bucket[status] += 1
        else:
            bucket[status] = 1
        color = str(slot.get("occupancy_color") or slot.get("object_color") or "").lower()
        if status == "occupied" and color:
            summary["occupied_by_color"][color] = int(summary["occupied_by_color"].get(color, 0)) + 1
            bucket.setdefault("occupied_by_color", {})
            bucket["occupied_by_color"][color] = int(bucket["occupied_by_color"].get(color, 0)) + 1
    return summary


def _center(obj):
    center = obj.get("center_px") or {}
    return float(center.get("x") or 0.0), float(center.get("y") or 0.0)


def _distance(a, b):
    return math.sqrt((float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2)


def _detect_slot_colors(image_path, slots):
    """Estimate a colored object inside each slot ROI from the source image."""
    if cv2 is None or np is None or not image_path:
        return {}
    image = cv2.imread(str(image_path))
    if image is None:
        return {}
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    height, width = hsv.shape[:2]
    result = {}
    for slot in slots or []:
        center = slot.get("center_px") or {}
        x = int(round(float(center.get("x") or 0.0)))
        y = int(round(float(center.get("y") or 0.0)))
        radius = float(slot.get("radius_px") or 45.0) * OCCUPANCY_COLOR_RADIUS_SCALE
        if x < 0 or y < 0 or x >= width or y >= height:
            continue
        yy, xx = np.ogrid[:height, :width]
        roi = (xx - x) ** 2 + (yy - y) ** 2 <= radius ** 2
        pixels = hsv[roi]
        if pixels.size == 0:
            continue
        candidates = []
        for color, (lower, upper) in OCCUPANCY_COLOR_HSV_RANGES.items():
            mask = (
                (pixels[:, 0] >= lower[0]) & (pixels[:, 0] <= upper[0])
                & (pixels[:, 1] >= lower[1]) & (pixels[:, 1] <= upper[1])
                & (pixels[:, 2] >= lower[2]) & (pixels[:, 2] <= upper[2])
            )
            candidates.append((float(mask.mean()), color))
        fraction, color = max(candidates, default=(0.0, None))
        if color and fraction >= OCCUPANCY_COLOR_FRACTION:
            result[str(slot.get("name") or "")] = {"color": color, "confidence": round(fraction, 4)}
    return result


def _board_side(obj, image_width=None):
    side = str(obj.get("board_side") or "").strip().lower()
    if side in {"left", "right"}:
        return side
    x, _ = _center(obj)
    if image_width:
        return "left" if x < float(image_width) * 0.5 else "right"
    return "left" if x < 1600 else "right"


def _split_top_bottom(markers):
    """Split a side's markers into top/bottom clusters by largest y-gap (or half)."""
    ordered = sorted(markers, key=lambda item: _center(item)[1])
    if len(ordered) <= 1:
        return {"top": ordered, "bottom": []}
    if len(ordered) <= 4:
        return {"top": ordered, "bottom": []}

    best_index = len(ordered) // 2
    best_gap = -1.0
    for index in range(1, len(ordered)):
        gap = _center(ordered[index])[1] - _center(ordered[index - 1])[1]
        # Prefer a split near the middle when gaps are similar.
        middle_bonus = 1.0 - abs((index / len(ordered)) - 0.5)
        score = gap + middle_bonus * 10.0
        if gap > best_gap and score >= best_gap:
            # use pure gap primarily
            pass
        if gap > best_gap:
            best_gap = gap
            best_index = index

    # If markers are evenly spaced (no clear gap), fall back to half split.
    ys = [_center(item)[1] for item in ordered]
    spans = [ys[i] - ys[i - 1] for i in range(1, len(ys))]
    median_span = sorted(spans)[len(spans) // 2] if spans else 0.0
    if best_gap < max(120.0, median_span * 1.8):
        best_index = len(ordered) // 2

    return {
        "top": ordered[:best_index],
        "bottom": ordered[best_index:],
    }


def cluster_markers(markers, image_width=None):
    """Group markers into side + vertical cluster, sorted top-to-bottom in cluster."""
    left = []
    right = []
    for marker in markers or []:
        side = _board_side(marker, image_width=image_width)
        (left if side == "left" else right).append(marker)

    clusters = []
    for side, group in (("left", left), ("right", right)):
        split = _split_top_bottom(group)
        for cluster_name in ("top", "bottom"):
            members = split.get(cluster_name) or []
            if not members:
                continue
            members = sorted(members, key=lambda item: _center(item)[1])
            clusters.append(
                {
                    "side": side,
                    "cluster": cluster_name,
                    "group": DEFAULT_GROUP_BY_SIDE.get(side, side),
                    "prefix": DEFAULT_CLUSTER_PREFIX.get((side, cluster_name), f"{side[:1].upper()}{cluster_name[:1].upper()}"),
                    "markers": members,
                }
            )
    return clusters



def _point_in_region(center, region):
    corners = region.get("corners_px") or region.get("transform_corners_px") or []
    if len(corners) != 4:
        return False
    x, y = float(center[0]), float(center[1])
    inside = False
    for index, current in enumerate(corners):
        previous = corners[index - 1]
        xi, yi = float(current[0]), float(current[1])
        xj, yj = float(previous[0]), float(previous[1])
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi:
            inside = not inside
    return inside


def _explicit_storage_clusters(markers, storage_regions, image_width=None):
    regions = [
        region for region in (storage_regions or [])
        if str((region or {}).get("group") or "").upper() in {"LT", "LB", "RT", "RB"}
    ]
    groups = [str(region.get("group")).upper() for region in regions]
    if len(regions) != 4 or set(groups) != {"LT", "LB", "RT", "RB"}:
        return None
    clusters = []
    for region in sorted(regions, key=lambda item: ("LT", "LB", "RT", "RB").index(str(item.get("group")).upper())):
        members = [marker for marker in markers or [] if _point_in_region(_center(marker), region)]
        group = str(region.get("group")).upper()
        clusters.append({
            "side": _board_side(region, image_width=image_width),
            "cluster": str(region.get("cluster") or group.lower()),
            "group": group,
            "prefix": group,
            "region_id": region.get("region_id"),
            "markers": sorted(members, key=lambda item: _center(item)[1]),
        })
    return clusters
def default_slot_name(prefix, index):
    return f"{prefix}{index}"


def marker_to_slot_fields(marker):
    center = marker.get("center_px") or {}
    return {
        "center_px": {
            "x": float(center.get("x") or 0.0),
            "y": float(center.get("y") or 0.0),
        },
        "workspace_xy": deepcopy(marker.get("workspace_xy")),
        "robot_plane": deepcopy(marker.get("robot_plane")),
        "per_arm_robot_plane": deepcopy(marker.get("per_arm_robot_plane")),
        "bbox": deepcopy(marker.get("bbox")),
        "radius_px": float(marker.get("radius_px") or 0.0),
        "area": float(marker.get("area") or 0.0),
        "circularity": float(marker.get("circularity") or 0.0),
        "detection_source": marker.get("detection_source"),
        "mean_hsv": deepcopy(marker.get("mean_hsv")),
        "marker_id": marker.get("id") or marker.get("slot_index"),
    }


def build_slots_from_markers(
    markers,
    previous_registry=None,
    image_width=None,
    image_path=None,
    source_scan=None,
    storage_regions=None,
):
    """Create/update named slots from black marker detections."""
    previous = previous_registry or empty_registry()
    previous_slots = list(previous.get("slots") or [])
    clusters = _explicit_storage_clusters(markers, storage_regions, image_width=image_width) or cluster_markers(markers, image_width=image_width)

    used_previous = set()
    slots = []
    for cluster in clusters:
        prefix = cluster["prefix"]
        group = cluster["group"]
        side = cluster["side"]
        cluster_name = cluster["cluster"]
        for index, marker in enumerate(cluster["markers"], start=1):
            fields = marker_to_slot_fields(marker)
            center = (fields["center_px"]["x"], fields["center_px"]["y"])

            matched = None
            matched_key = None
            best_distance = None
            for prev_index, prev in enumerate(previous_slots):
                if prev_index in used_previous:
                    continue
                prev_center = prev.get("center_px") or {}
                distance = _distance(
                    center,
                    (prev_center.get("x") or 0.0, prev_center.get("y") or 0.0),
                )
                if distance > MATCH_DISTANCE_PX:
                    continue
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    matched = prev
                    matched_key = prev_index

            if matched is not None:
                used_previous.add(matched_key)
                name = str(matched.get("name") or default_slot_name(prefix, index))
                label = str(matched.get("label") or name)
                role = "storage"
                group_name = prefix
                notes = matched.get("notes")
            else:
                name = default_slot_name(prefix, index)
                label = name
                role = "storage"
                group_name = prefix
                notes = None

            slot = {
                "name": name,
                "label": label,
                "role": role,
                "group": group_name,
                "side": side,
                "cluster": cluster_name,
                "index_in_cluster": index,
                "storage_region_id": cluster.get("region_id"),
                "occupancy": "empty",
                "occupancy_source": "black_marker_visible",
                "held_object": None,
                **fields,
            }
            if notes is not None:
                slot["notes"] = notes
            if best_distance is not None:
                slot["matched_previous_distance_px"] = float(best_distance)
            slots.append(slot)

    # Keep every calibrated slot that was not rematched; a missing marker means
    # occupancy changed or could not be verified, not that the slot vanished.
    for prev_index, prev in enumerate(previous_slots):
        if prev_index in used_previous:
            continue
        missing = deepcopy(prev)
        missing["occupancy"] = "unknown"
        missing["occupancy_source"] = "marker_missing_color_unconfirmed"
        slots.append(missing)

    slots.sort(
        key=lambda item: (
            0 if item.get("side") == "left" else 1,
            0 if item.get("cluster") == "top" else 1,
            int(item.get("index_in_cluster") or 0),
            str(item.get("name") or ""),
        )
    )

    registry = empty_registry()
    registry["created_at"] = previous.get("created_at") or registry["created_at"]
    registry["groups"] = deepcopy(previous.get("groups") or registry["groups"])
    registry["expected_count"] = int(previous.get("expected_count") or 16)
    registry["slots"] = slots
    registry["slot_count"] = len(slots)
    registry["source_scan"] = source_scan or previous.get("source_scan")
    registry["image_path"] = image_path or previous.get("image_path")
    registry["summary"] = summarize_slots(slots)
    registry["naming"] = {
        "style": "inner_box_like",
        "default_prefixes": {
            "LT": "left top",
            "LB": "left bottom",
            "RT": "right top",
            "RB": "right bottom",
        },
        "groups": DEFAULT_GROUP_BY_SIDE,
        "note": "Rename any slot with rename_slot(); names persist across rescans by nearest center match.",
    }
    return registry


def rename_slot(registry, name_or_label, new_name, new_label=None, new_group=None):
    """Rename a slot (and optionally reassign group). Names must stay unique."""
    payload = deepcopy(registry)
    target = None
    key = str(name_or_label or "").strip()
    for slot in payload.get("slots") or []:
        if str(slot.get("name")) == key or str(slot.get("label")) == key:
            target = slot
            break
    if target is None:
        raise KeyError(f"Capacity slot not found: {name_or_label}")

    new_name = str(new_name or "").strip()
    if not new_name:
        raise ValueError("new_name is required")
    for slot in payload.get("slots") or []:
        if slot is target:
            continue
        if str(slot.get("name")) == new_name:
            raise ValueError(f"Slot name already exists: {new_name}")

    target["name"] = new_name
    target["label"] = str(new_label or new_name).strip()
    if new_group is not None:
        group = str(new_group).strip()
        target["group"] = group
        groups = payload.setdefault("groups", {})
        if group and group not in groups:
            groups[group] = {
                "name": group,
                "label": group,
                "side": target.get("side"),
                "description": f"User group '{group}'",
            }
    payload["summary"] = summarize_slots(payload.get("slots") or [])
    payload["updated_at"] = utc_now()
    return payload


def assign_slot_group(registry, name_or_label, group_name):
    payload = deepcopy(registry)
    key = str(name_or_label or "").strip()
    group = str(group_name or "").strip()
    if not group:
        raise ValueError("group_name is required")
    target = None
    for slot in payload.get("slots") or []:
        if str(slot.get("name")) == key or str(slot.get("label")) == key:
            target = slot
            break
    if target is None:
        raise KeyError(f"Capacity slot not found: {name_or_label}")
    target["group"] = group
    groups = payload.setdefault("groups", {})
    if group not in groups:
        groups[group] = {
            "name": group,
            "label": group,
            "side": target.get("side"),
            "description": f"User group '{group}'",
        }
    payload["summary"] = summarize_slots(payload.get("slots") or [])
    payload["updated_at"] = utc_now()
    return payload


def list_slots(registry, group=None, occupancy=None, side=None):
    slots = list(registry.get("slots") or [])
    if group is not None:
        group_key = str(group).strip()
        slots = [slot for slot in slots if str(slot.get("group")) == group_key]
    if occupancy is not None:
        occ_key = str(occupancy).strip().lower()
        slots = [slot for slot in slots if str(slot.get("occupancy") or "").lower() == occ_key]
    if side is not None:
        side_key = str(side).strip().lower()
        slots = [slot for slot in slots if str(slot.get("side") or "").lower() == side_key]
    return slots


def find_slot(registry, name_or_label):
    key = str(name_or_label or "").strip()
    for slot in registry.get("slots") or []:
        if str(slot.get("name")) == key or str(slot.get("label")) == key:
            return slot
    return None


def first_empty_slot(registry, group=None, side=None):
    candidates = list_slots(registry, group=group, occupancy="empty", side=side)
    return candidates[0] if candidates else None



def _black_marker_visible_at_slot(image_path, slot):
    """Recover a black marker when contour/Hough misses it under glare."""
    if cv2 is None or np is None or not image_path:
        return False, {}
    image = cv2.imread(str(image_path))
    if image is None:
        return False, {}
    center = slot.get("center_px") or {}
    x = int(round(float(center.get("x") or 0.0)))
    y = int(round(float(center.get("y") or 0.0)))
    if x < 0 or y < 0 or x >= image.shape[1] or y >= image.shape[0]:
        return False, {}
    radius = float(slot.get("radius_px") or 45.0)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    yy, xx = np.ogrid[:gray.shape[0], :gray.shape[1]]
    inner_r = max(8, int(round(radius * 0.65)))
    ring_inner = max(inner_r + 1, int(round(radius * 1.05)))
    ring_outer = max(ring_inner + 2, int(round(radius * 1.55)))
    inner = (xx - x) ** 2 + (yy - y) ** 2 <= inner_r ** 2
    ring = (((xx - x) ** 2 + (yy - y) ** 2) <= ring_outer ** 2) & (((xx - x) ** 2 + (yy - y) ** 2) > ring_inner ** 2)
    inner_v = float(gray[inner].mean())
    ring_v = float(gray[ring].mean()) if np.any(ring) else 0.0
    inner_s = float(hsv[:, :, 1][inner].mean())
    stats = {"inner_mean_value": round(inner_v, 3), "ring_mean_value": round(ring_v, 3), "ring_contrast": round(ring_v - inner_v, 3), "inner_mean_saturation": round(inner_s, 3)}
    visible = inner_v <= 120.0 and (ring_v - inner_v) >= 45.0 and inner_s <= 55.0
    return visible, stats
def apply_occupancy_from_black_scan(registry, scan_report):
    """Return empty, occupied+color, or unknown for every calibrated slot."""
    payload = deepcopy(registry)
    markers = list((scan_report or {}).get("objects") or [])
    marker_centers = [_center(marker) for marker in markers]
    color_by_slot = _detect_slot_colors((scan_report or {}).get("image_path"), payload.get("slots") or [])

    for slot in payload.get("slots") or []:
        name = str(slot.get("name") or "")
        center = slot.get("center_px") or {}
        slot_xy = (center.get("x") or 0.0, center.get("y") or 0.0)
        nearest = None
        nearest_distance = None
        for marker_xy in marker_centers:
            distance = _distance(slot_xy, marker_xy)
            if nearest_distance is None or distance < nearest_distance:
                nearest_distance = distance
                nearest = marker_xy
        color_info = color_by_slot.get(name)
        slot["occupancy_color"] = None
        slot["object_color"] = None
        slot["color_confidence"] = None
        if color_info:
            slot["occupancy"] = "occupied"
            slot["occupancy_source"] = "colored_object_detected"
            slot["occupancy_color"] = color_info["color"]
            slot["object_color"] = color_info["color"]
            slot["color_confidence"] = color_info["confidence"]
            slot["occupancy_distance_px"] = float(nearest_distance) if nearest_distance is not None else None
            slot["held_object"] = {"color": color_info["color"]}
        elif nearest is not None and nearest_distance is not None and nearest_distance <= OCCUPANCY_MATCH_PX:
            slot["occupancy"] = "empty"
            slot["occupancy_source"] = "black_marker_visible"
            slot["occupancy_distance_px"] = float(nearest_distance)
            slot["held_object"] = None
        else:
            marker_visible, marker_stats = _black_marker_visible_at_slot((scan_report or {}).get("image_path"), slot)
            if marker_visible:
                slot["occupancy"] = "empty"
                slot["occupancy_source"] = "black_marker_geometry_fallback"
                slot["occupancy_distance_px"] = float(nearest_distance) if nearest_distance is not None else None
                slot["marker_verification"] = marker_stats
                slot["held_object"] = None
            else:
                slot["occupancy"] = "unknown"
                slot["occupancy_source"] = "marker_missing_color_unconfirmed"
                slot["occupancy_distance_px"] = float(nearest_distance) if nearest_distance is not None else None
                slot["marker_verification"] = marker_stats
                slot["held_object"] = None

    payload["source_scan"] = {
        "timestamp": (scan_report or {}).get("timestamp"),
        "image_path": (scan_report or {}).get("image_path"),
        "detected_count": (scan_report or {}).get("detected_count"),
        "path": str((scan_report or {}).get("outputs", {}).get("latest_report") or DEFAULT_LATEST_SCAN),
    }
    payload["summary"] = summarize_slots(payload.get("slots") or [])
    payload["updated_at"] = utc_now()
    return payload
def build_from_scan_report(
    scan_report,
    previous_path=DEFAULT_SLOTS_PATH,
    save_path=DEFAULT_SLOTS_PATH,
    persist=True,
    storage_regions=None,
):
    previous = load_slots(previous_path) if Path(previous_path).exists() else empty_registry()
    frame = (scan_report or {}).get("frame") or {}
    image_width = frame.get("width")
    if image_width is None and (scan_report or {}).get("objects"):
        # infer from max x
        xs = [float((obj.get("center_px") or {}).get("x") or 0.0) for obj in scan_report["objects"]]
        image_width = max(xs) * 2 if xs else None

    registry = build_slots_from_markers(
        markers=(scan_report or {}).get("objects") or [],
        previous_registry=previous,
        image_width=image_width,
        image_path=(scan_report or {}).get("image_path"),
        storage_regions=storage_regions,
        source_scan={
            "timestamp": (scan_report or {}).get("timestamp"),
            "detected_count": (scan_report or {}).get("detected_count"),
            "left_count": (scan_report or {}).get("left_count"),
            "right_count": (scan_report or {}).get("right_count"),
            "image_path": (scan_report or {}).get("image_path"),
            "report_path": str(((scan_report or {}).get("outputs") or {}).get("latest_report") or DEFAULT_LATEST_SCAN),
        },
    )
    registry = apply_occupancy_from_black_scan(registry, scan_report)
    if persist and save_path is not None:
        registry = save_slots(registry, save_path)
    return registry


def scan_and_build_slots(
    image_path=None,
    expected_count=16,
    slots_path=DEFAULT_SLOTS_PATH,
    rescan=True,
):
    """Run black capacity scan (optional) then build/update named slot registry."""
    if rescan:
        scan_report = scan_capacity_image(image_path=image_path, expected_count=expected_count)
        if not scan_report.get("ok"):
            return {"ok": False, "error": scan_report.get("error") or "capacity scan failed", "scan": scan_report}
    else:
        if not DEFAULT_LATEST_SCAN.exists():
            return {"ok": False, "error": f"No latest capacity scan at {DEFAULT_LATEST_SCAN}"}
        scan_report = load_json(DEFAULT_LATEST_SCAN)

    storage_regions = None
    try:
        from .workspace_calibration import load_workspace_calibration
        calibration = load_workspace_calibration()
        storage_regions = (calibration or {}).get("storage_regions") or (calibration or {}).get("storage_boxes")
        if storage_regions is None:
            storage_regions = [
                zone for zone in ((calibration or {}).get("zones") or [])
                if str((zone or {}).get("role") or "").lower() in {"storage", "storage_region", "storage_box", "drop"}
            ]
    except Exception:
        storage_regions = None
    registry = build_from_scan_report(scan_report, previous_path=slots_path, save_path=slots_path, persist=True, storage_regions=storage_regions)
    return {
        "ok": True,
        "scan": {
            "detected_count": scan_report.get("detected_count"),
            "left_count": scan_report.get("left_count"),
            "right_count": scan_report.get("right_count"),
            "image_path": scan_report.get("image_path"),
            "input_source": scan_report.get("input_source"),
            "overlay": ((scan_report.get("outputs") or {}).get("latest_overlay")),
        },
        "occupancy": {
            "empty": (registry.get("summary") or {}).get("empty", 0),
            "occupied": (registry.get("summary") or {}).get("occupied", 0),
            "unknown": (registry.get("summary") or {}).get("unknown", 0),
            "occupied_by_color": (registry.get("summary") or {}).get("occupied_by_color", {}),
        },
        "registry": registry,
        "slots_path": str(slots_path),
        "summary": registry.get("summary"),
    }


def write_slots_overlay(image_path, registry, output_path):
    """Draw named slots on an image for review."""
    if cv2 is None or np is None:
        return None
    image = cv2.imread(str(image_path))
    if image is None:
        return None
    overlay = image.copy()
    scale = max(0.7, min(2.0, overlay.shape[1] / 1600.0))
    line = max(2, int(round(scale * 2.5)))
    font = cv2.FONT_HERSHEY_SIMPLEX
    palette = {
        "empty": (0, 200, 255),
        "occupied": (40, 40, 220),
        "unknown": (180, 180, 180),
    }
    for slot in registry.get("slots") or []:
        center = slot.get("center_px") or {}
        cx = int(round(float(center.get("x") or 0.0)))
        cy = int(round(float(center.get("y") or 0.0)))
        radius = int(round(float(slot.get("radius_px") or 40.0)))
        status = str(slot.get("occupancy") or "unknown")
        color = palette.get(status, palette["unknown"])
        cv2.circle(overlay, (cx, cy), max(4, radius), color, line, cv2.LINE_AA)
        label = f"{slot.get('name')} [{slot.get('group')}] {status}"
        cv2.putText(
            overlay,
            label,
            (max(0, cx - 40), max(24, cy - radius - 8)),
            font,
            scale * 0.5,
            color,
            max(1, int(round(line * 0.8))),
            cv2.LINE_AA,
        )
    summary = registry.get("summary") or {}
    text = (
        f"capacity slots {registry.get('slot_count', 0)} | "
        f"empty {summary.get('empty', 0)} occupied {summary.get('occupied', 0)}"
    )
    cv2.rectangle(overlay, (12, 12), (12 + int(820 * scale), 12 + int(46 * scale)), (20, 20, 20), -1)
    cv2.putText(overlay, text, (22, 12 + int(32 * scale)), font, scale * 0.65, (0, 255, 255), max(1, line), cv2.LINE_AA)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(target), overlay):
        return None
    return str(target)


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(description="Named capacity slots from black marker scan")
    sub = parser.add_subparsers(dest="command", required=True)

    build_cmd = sub.add_parser("build", help="Scan black markers and build/update named slots")
    build_cmd.add_argument("--image", help="Calibration image (default: current calibration image)")
    build_cmd.add_argument("--expected-count", type=int, default=16)
    build_cmd.add_argument("--slots-path", default=str(DEFAULT_SLOTS_PATH))
    build_cmd.add_argument("--no-rescan", action="store_true", help="Reuse latest capacity scan report")

    list_cmd = sub.add_parser("list", help="List named capacity slots")
    list_cmd.add_argument("--slots-path", default=str(DEFAULT_SLOTS_PATH))
    list_cmd.add_argument("--group", help="Filter by group (storage/drop/...)")
    list_cmd.add_argument("--occupancy", choices=["empty", "occupied", "unknown"])
    list_cmd.add_argument("--side", choices=["left", "right"])

    rename_cmd = sub.add_parser("rename", help="Rename a slot like an inner box label")
    rename_cmd.add_argument("slot", help="Current slot name or label")
    rename_cmd.add_argument("new_name", help="New slot name")
    rename_cmd.add_argument("--label", help="Optional display label")
    rename_cmd.add_argument("--group", help="Optional group reassignment")
    rename_cmd.add_argument("--slots-path", default=str(DEFAULT_SLOTS_PATH))

    group_cmd = sub.add_parser("group", help="Assign slot to a group")
    group_cmd.add_argument("slot")
    group_cmd.add_argument("group_name")
    group_cmd.add_argument("--slots-path", default=str(DEFAULT_SLOTS_PATH))

    empty_cmd = sub.add_parser("first-empty", help="Show first empty slot")
    empty_cmd.add_argument("--group")
    empty_cmd.add_argument("--side", choices=["left", "right"])
    empty_cmd.add_argument("--slots-path", default=str(DEFAULT_SLOTS_PATH))

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "build":
        result = scan_and_build_slots(
            image_path=args.image,
            expected_count=args.expected_count,
            slots_path=args.slots_path,
            rescan=not args.no_rescan,
        )
        if not result.get("ok"):
            print(json.dumps(result, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        registry = result["registry"]
        image_path = result["scan"].get("image_path")
        if image_path:
            overlay_path = Path(args.slots_path).with_name("capacity_slots_overlay.jpg")
            # keep overlay next to slots json in runtime_state
            overlay_path = runtime_state_path("capacity_slots_overlay.jpg")
            written = write_slots_overlay(image_path, registry, overlay_path)
            result["overlay"] = written
        print(f"slots path: {result['slots_path']}")
        print(
            f"built {registry.get('slot_count')} named slots "
            f"(scan detected {result['scan'].get('detected_count')}: "
            f"L{result['scan'].get('left_count')}/R{result['scan'].get('right_count')})"
        )
        summary = registry.get("summary") or {}
        print(f"occupancy: empty={summary.get('empty')} occupied={summary.get('occupied')} unknown={summary.get('unknown')}")
        for slot in registry.get("slots") or []:
            center = slot.get("center_px") or {}
            print(
                f"  {slot.get('name'):6s}  group={str(slot.get('group')):8s}  "
                f"{slot.get('side'):5s}/{slot.get('cluster'):6s}  "
                f"occ={slot.get('occupancy'):8s}  "
                f"px=({int(center.get('x', 0))},{int(center.get('y', 0))})"
            )
        if result.get("overlay"):
            print(f"overlay: {result['overlay']}")
        return result

    registry = load_slots(args.slots_path)

    if args.command == "list":
        slots = list_slots(registry, group=args.group, occupancy=args.occupancy, side=args.side)
        print(f"slots: {len(slots)} (of {registry.get('slot_count', 0)})")
        for slot in slots:
            center = slot.get("center_px") or {}
            print(
                f"  {slot.get('name'):6s}  label={slot.get('label'):6s}  "
                f"group={slot.get('group'):8s}  occ={slot.get('occupancy'):8s}  "
                f"px=({int(center.get('x', 0))},{int(center.get('y', 0))})"
            )
        return {"ok": True, "slots": slots}

    if args.command == "rename":
        registry = rename_slot(
            registry,
            args.slot,
            args.new_name,
            new_label=args.label,
            new_group=args.group,
        )
        save_slots(registry, args.slots_path)
        slot = find_slot(registry, args.new_name)
        print(f"renamed -> {slot.get('name')} label={slot.get('label')} group={slot.get('group')}")
        return {"ok": True, "slot": slot}

    if args.command == "group":
        registry = assign_slot_group(registry, args.slot, args.group_name)
        save_slots(registry, args.slots_path)
        slot = find_slot(registry, args.slot)
        print(f"{slot.get('name')} => group {slot.get('group')}")
        return {"ok": True, "slot": slot}

    if args.command == "first-empty":
        slot = first_empty_slot(registry, group=args.group, side=args.side)
        if not slot:
            print("no empty slot")
            raise SystemExit(2)
        center = slot.get("center_px") or {}
        print(
            f"{slot.get('name')} group={slot.get('group')} "
            f"px=({center.get('x')},{center.get('y')}) "
            f"ws={slot.get('workspace_xy')}"
        )
        return {"ok": True, "slot": slot}

    parser.error(f"Unknown command: {args.command}")
    return None


if __name__ == "__main__":
    main()
def normalize_storage_registry(registry):
    from .storage_service import normalize
    return normalize(registry)


def resolve_storage_slot(registry=None, reference=None, color=None, arm_id=None, require_empty=True):
    from .storage_service import resolve
    if registry is not None:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "slots.json"
            save_slots(registry, path)
            return resolve(reference=reference, color=color, arm_id=arm_id, require_empty=require_empty, path=path)
    return resolve(reference=reference, color=color, arm_id=arm_id, require_empty=require_empty)


def set_storage_policy(registry=None, policy=None):
    from .storage_service import set_policy, load
    if registry is None:
        return set_policy(policy or {})
    payload = normalize_storage_registry(registry)
    payload["slot_policy"] = policy or {}
    return normalize_storage_registry(payload)


def update_storage_occupancy(slot_name, occupancy, held_object=None, source="runtime", verification=None):
    from .storage_service import update_occupancy
    return update_occupancy(slot_name, occupancy, held_object, source, verification)











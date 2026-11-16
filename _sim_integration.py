"""Integration test — pick → scan → place → scan (โลกเปลี่ยนตามการหยิบวาง)"""
import json
from sim_world import WorldModel
from camera_sim import CameraSimulator

# --- test 1: default world ---
w = WorldModel()  # โหลดใน __init__ แล้ว
scan = w.simulate_scan()
print(f"[1] default world: {scan['object_count']} objects")
for o in scan['objects']:
    print(f"  {o['id']} {o['color']} @ ({o['robot_plane']['x']:.0f},{o['robot_plane']['y']:.0f})")

# --- test 2: pick green obj-1 at its position ---
pose = scan['objects'][0]['robot_plane']  # obj-1 ที่ (158,-20)
result = w.pick_at(pose)
print(f"[2] pick obj-1: ok={result['ok']}, held={w._held['color']}")
assert w._held['id'] == 'obj-1', "FAIL: world._held ผิด!"

# --- test 3: scan หลัง pick — obj-1 ต้องหาย ---
scan2 = w.simulate_scan()
ids2 = [o['id'] for o in scan2['objects']]
print(f"[3] หลัง pick: {scan2['object_count']} objects, ids={ids2}")
assert 'obj-1' not in ids2, "FAIL: obj-1 ยังอยู่หลัง pick!"

# --- test 4: place obj-1 ที่ storage zone ---
storage_pose = {"x": 100.0, "y": -100.0}
result = w.place_at(storage_pose)
print(f"[4] place obj-1 at storage: ok={result['ok']}")
assert result['ok']
# ตรวจสอบว่าวางถูกตำแหน่ง
placed = w.get_object_by_id('obj-1')
assert placed['robot_plane']['x'] == 100.0, "FAIL: วางผิดตำแหน่ง!"

# --- test 5: scan หลัง place — obj-1 ต้องอยู่ที่ใหม่ ---
scan3 = w.simulate_scan()
obj1_after = [o for o in scan3['objects'] if o['id'] == 'obj-1'][0]
print(f"[5] หลัง place: obj-1 @ ({obj1_after['robot_plane']['x']:.0f},{obj1_after['robot_plane']['y']:.0f})")
assert abs(obj1_after['robot_plane']['x'] - 100.0) < 1.0, "FAIL: ตำแหน่งวางไม่ตรง!"

# --- test 6: world persistence (singleton โหลดจากไฟล์) ---
w2 = WorldModel()
scan4 = w2.simulate_scan()
ids4 = [o['id'] for o in scan4['objects']]
print(f"[6] singleton reload: {scan4['object_count']} objects, ids={ids4}")
assert 'obj-1' in ids4, "FAIL: singleton โหลดโลกไม่ถูก!"
obj1_reload = w2.get_object_by_id('obj-1')
assert abs(obj1_reload['robot_plane']['x'] - 100.0) < 1.0, "FAIL: singleton โหลดตำแหน่งไม่ถูก!"

# --- test 7: camera_sim ---
cs = CameraSimulator(w)
cam_scan = cs.scan(color='blue')
blue_objs = [o for o in cam_scan['objects'] if o['color'] == 'blue']
print(f"[7] camera_sim scan(blue): {len(blue_objs)} objects")
assert len(blue_objs) == 1, "FAIL: ควรเหลือ blue obj เดียว (obj-3)!"

print("\n[OK] ALL INTEGRATION TESTS PASSED")

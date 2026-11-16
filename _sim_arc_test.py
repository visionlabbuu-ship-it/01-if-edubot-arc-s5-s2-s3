"""Full arc.py simulation test — pick → scan → place → scan"""
import json
import subprocess
import sys
import os

PY = "./.venv/Scripts/python.exe"
env = os.environ.copy()
env["ARC_SIM_MODE"] = "1"
env["PYTHONPATH"] = os.getcwd()

def run(args):
    r = subprocess.run([PY] + args, capture_output=True, text=True, env=env, cwd=os.getcwd())
    out = r.stdout.strip()
    try:
        return json.loads(out), r.stderr.strip()
    except:
        return out, r.stderr.strip()

print("=== 1) scan ก่อน pick ===")
data, _ = run(["arc.py", "camera", "--json", "scan"])
print(f"  objects: {data['object_count']}")
for o in data['objects']:
    print(f"    {o['id']} {o['color']} @ ({o['robot_plane']['x']:.0f},{o['robot_plane']['y']:.0f})")

print("\n=== 2) pick obj-1 (สีเขียว) ที่ pose เดิม ===")
obj1 = data['objects'][0]
pose = obj1['robot_plane']
_, _ = run(["arc.py", "arm", "--json", "pick-at", "Arm1", "--x", str(pose['x']), "--y", "0"])
print(f"  (pick-at {pose['x']:.0f},0)")

print("\n=== 3) scan หลัง pick — obj-1 ควรหาย ===")
data2, _ = run(["arc.py", "camera", "--json", "scan"])
ids2 = [o['id'] for o in data2['objects']]
print(f"  objects: {data2['object_count']}, ids={ids2}")
assert "obj-1" not in ids2, f"FAIL: obj-1 ยังอยู่! ids={ids2}"
print("  [OK] obj-1 หาย (ถืออยู่)")

print("\n=== 4) place ที่ storage zone ===")
storage_x = 187.41
storage_y = -311.67
_, _ = run(["arc.py", "arm", "--json", "place-at-named", "Arm1", "--destination", "storage_zone"])
print(f"  (place-at-named storage_zone)")

print("\n=== 5) scan หลัง place — obj-1 ควรอยู่ที่ storage zone ===")
data3, _ = run(["arc.py", "camera", "--json", "scan"])
obj1_new = [o for o in data3['objects'] if o['id'] == 'obj-1']
print(f"  objects: {data3['object_count']}")
if obj1_new:
    p = obj1_new[0]['robot_plane']
    print(f"  obj-1 @ ({p['x']:.0f},{p['y']:.0f})")
    assert abs(p['x'] - storage_x) < 10, f"FAIL: x={p['x']:.0f} != {storage_x:.0f}"
    assert abs(p['y'] - storage_y) < 10, f"FAIL: y={p['y']:.0f} != {storage_y:.0f}"
    print("  [OK] obj-1 ย้ายไปที่ storage zone!")
else:
    print("  [FAIL] obj-1 ไม่อยู่ใน scan!")
    sys.exit(1)

print("\n=== 6) verify obj-1 at storage, not at original pose ===")
data4, _ = run(["arc.py", "camera", "--json", "scan"])
obj1_final = [o for o in data4['objects'] if o['id'] == 'obj-1']
green_at_origin = [o for o in data4['objects'] if o['color'] == 'green' and abs(o['robot_plane']['x'] - 158) < 10 and abs(o['robot_plane']['y'] - (-20)) < 10]
print(f"  obj-1 @ ({obj1_final[0]['robot_plane']['x']:.0f},{obj1_final[0]['robot_plane']['y']:.0f})")
print(f"  green objects at original pose: {len(green_at_origin)}")
assert len(obj1_final) == 1, "FAIL: obj-1 ไม่อยู่ใน scan!"
assert abs(obj1_final[0]['robot_plane']['x'] - storage_x) < 10, "FAIL: obj-1 ไม่อยู่ที่ storage!"
assert len(green_at_origin) == 0, "FAIL: ยังเห็นวัตถุที่ pose เดิม!"
print("  [OK] obj-1 ย้ายไปที่ storage zone, ไม่เหลือที่ pose เดิม!")

print("\n[SIMULATION INTEGRATION TESTS PASSED]")

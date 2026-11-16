# ARC Jetson Collection Optimization

**วันที่:** 7 กันยายน 2026  
**ขอบเขต:** ลด prompt/context, ลด tool-result token, แก้ Jetson streaming proxy และตรวจเส้นทางจริงแบบไม่สั่งหุ่นเคลื่อนที่

## Executive summary

วันนี้ปรับ `arc pipe jetson` ให้ใช้ system prompt แบบสั้นและคงที่ ลด tool schemas และย่อผลลัพธ์ที่ส่งกลับเข้าโมเดล พร้อมแก้บั๊ก HTTP streaming proxy ที่ทำให้ client รอ 120 วินาทีและยิง request ซ้ำทั้งที่โมเดลตอบเสร็จแล้ว

ผล smoke test เส้นทางจริง:

| รายการ | ก่อนแก้ proxy | หลังแก้ | ผลลัพธ์ |
|---|---:|---:|---:|
| เวลารวมของคำสั่ง | 141.9 วินาที | 3.8 วินาที | เร็วขึ้นประมาณ 37 เท่า |
| ARC duration | 138.4 วินาที | 0.765 วินาที | ตัดเวลารอผิดปกติออก |
| จำนวน model requests | 2 | 1 | ไม่มี retry ซ้ำ |
| Proxy round trip | 17.735 วินาทีใน request แรก | 0.656 วินาที | รอบหลังใช้ prefix cache |
| Robot tool calls | 0 | 0 | ไม่มีการสั่งหุ่น |
| คำตอบ | `OK` | `OK` | ผลลัพธ์เหมือนเดิม |

> ความเร็วที่เพิ่มแบบก้าวกระโดดมาจากการแก้ proxy timeout เป็นหลัก ส่วน prompt slimming ช่วยลด prefill, ทำให้ prefix cache ใช้งานได้ดีขึ้น และลด context ที่สะสมในงานหลายขั้นตอน

## สาเหตุที่เร็วขึ้นมาก

| ปัจจัย | ปัญหาเดิม | สิ่งที่เปลี่ยน | ผลต่อระบบ | ความสำคัญ |
|---|---|---|---|---|
| HTTP stream framing | Proxy ตัด `Transfer-Encoding` แต่ไม่ส่ง `Content-Length` และไม่ปิด connection ทำให้ Node client ไม่รู้ว่า response จบแล้ว | ส่ง `Connection: close` และปิด connection หลัง forward stream จบ | ตัดการรอ 120 วินาทีและไม่ retry | **สาเหตุหลักของ 141.9 → 3.8 วินาที** |
| Frozen collection prompt | Prompt เดิมรวม memory, Git/pentest และคำแนะนำทั่วไปจำนวนมาก | ใช้ prompt คงที่ที่มีเฉพาะ safety, planning, Storage และ dual-arm rules | ลด prefill และทำให้ prefix เหมือนเดิมทุกเทิร์น | สูง |
| ลด tool schemas | มี `AskUserQuestion` และ `TaskStop` ใน collection profile โดยไม่จำเป็น | ตัดสอง tool นี้ออก แต่คง robot tools ที่จำเป็น | ลด input token ก่อน inference | ปานกลาง |
| Compact tool results | ส่ง command, stdout, workspace path และ calibration data กลับเข้า context | ส่งเฉพาะ `ok`, tool, fault/guardrail และ planning data ที่จำเป็น | ลด context growth หลังทุก tool call | สูงสำหรับงานหลายขั้นตอน |
| Prefix cache | Prefix เดิมใหญ่และมีส่วนแปรผัน | Prompt และ schemas คงที่มากขึ้น | Smoke test อ่าน cache 3,834 tokens และ process ใหม่ 4 tokens | สูงใน warm-cache run |
| ป้องกัน retry | Client timeout แล้วส่ง request non-stream ซ้ำ | Streaming response จบอย่างถูกต้องตั้งแต่ครั้งแรก | ไม่เสีย inference ซ้ำและ telemetry ไม่ซ้ำ | สูง |

## แยกผลของ proxy ออกจากผลของ prompt

ไม่ควรกล่าวว่า “ลด prompt อย่างเดียวทำให้เร็วขึ้น 37 เท่า” เพราะผลก่อนแก้มี HTTP timeout ปะปนอยู่

| ช่วงก่อนแก้ | เวลา | ความหมาย |
|---|---:|---|
| Streaming request แรก | 17.735 วินาที | เวลาที่ proxy เห็นจน upstream ตอบจบ |
| ช่วงค้างหลังได้คำตอบ | ประมาณ 120 วินาที | Client รอจุดสิ้นสุด response ที่ไม่ถูกส่ง |
| Retry แบบ non-stream | 0.563 วินาที | Request ซ้ำและได้ prefix-cache hit |
| เวลารวม | 141.9 วินาที | รวม inference, timeout, retry และ launcher overhead |

หลังแก้มี request เดียว, proxy round trip 0.656 วินาที และเวลารวม 3.8 วินาที รอบหลังเป็น warm-cache run จึงยืนยันเรื่อง timeout/retry ได้ชัดเจน แต่ยังไม่ใช่ controlled cold-vs-cold benchmark

## ผลการลด token

### First-turn prompt

| รายการ | ก่อนปรับ | หลังปรับ | ลดลง |
|---|---:|---:|---:|
| ค่าประมาณ first-turn | 13,800 tokens | 3,382 tokens | 75.5% |
| ค่าที่วัดจาก Jetson request จริง | baseline ประมาณ 13,800 | 3,838 tokens | 72.2% |
| Warm-cache composition | — | 3,834 cached + 4 new | process prefix ใหม่เพียงเล็กน้อย |

### Tool-result return path

วัดจาก trace ตัวอย่างเดิม 13 tool calls:

| รายการ | ก่อนปรับ | หลังปรับ | ลดลง |
|---|---:|---:|---:|
| Characters | 62,367 | 19,553 | 68.6% |
| Tokens โดยประมาณ | 14,849 | 4,655 | 10,194 tokens หรือ 68.6% |

การลด return-path สำคัญมากกับงานหุ่นหลายขั้นตอน เพราะผล tool ทุกครั้งจะสะสมเข้า conversation และถูกอ่านซ้ำในเทิร์นถัดไป

## งานจาก Grok

อ้างอิง session `01a07ab5-b34e-7522-b845-a4f75e4277ae` ชื่อ **ARC pipe Jetson two-host collection wiring**

| งาน | รายละเอียด | สถานะ |
|---|---|---|
| วิเคราะห์ prompt budget | พบ system prompt สดประมาณ 42,239 characters, tool JSON ประมาณ 18,000 characters และ first-turn ราว 13.8k tokens | เสร็จ |
| สำรองไฟล์ก่อนแก้ | สำรอง source, RobotControl tools, CLI bundle และ `arc_pipe.mjs` | เสร็จ |
| สร้าง frozen prompt | เพิ่ม prompt ที่เน้น safety, `plan_task → validate_plan → execute_plan`, Storage และ dual-arm sequential | เสร็จ |
| เปิด collection profile | ให้ `arc pipe jetson` ตั้ง `ARC_COLLECTION_PROMPT=1` | เสร็จ |
| ลด tool set | ตัด `AskUserQuestion` และ `TaskStop` ใน collection mode | เสร็จ |
| Compact robot results | เพิ่ม model-facing result และเชื่อมกับ RobotControl tools โดยไม่ทิ้ง raw telemetry | เสร็จ |
| เพิ่มเครื่องมือวัด | เพิ่มสคริปต์, tests และ `prompt_budget.json` | เสร็จ |
| Build/test เบื้องต้น | Rebuild CLI และรัน focused tests | เสร็จ |
| Verification ขั้นสุดท้าย | ทำต่อไม่ได้เพราะ model `maxplus` ใช้ไม่ได้และโควต้า Grok หมด | ส่งต่อให้ Codex |

## งานที่ Codex รับช่วง

| งาน | รายละเอียด | ผลลัพธ์ |
|---|---|---|
| ตรวจ source และ bundle | ตรวจ collection prompt, UTF-8 ภาษาไทย, call sites และ built bundle | ตรงกัน |
| วัด token ซ้ำ | ตรวจค่าประมาณกับ runtime implementation | ยืนยันผลลด token |
| Build และ focused tests | Build CLI/SDK, Python tests, Bun tests และ compile checks | ผ่าน |
| Smoke test Jetson | สั่งตอบ `OK` เท่านั้นและห้ามเรียก tool | 0 tool calls, ไม่มี motion |
| วิเคราะห์ latency | อ่าน proxy trace แยก inference ออกจากเวลาค้าง | พบ HTTP framing bug |
| แก้ streaming proxy | เพิ่ม `Connection: close` และปิด connection เมื่อ stream จบ | ไม่มี timeout และ retry |
| เพิ่ม regression test | จำลอง HTTP/1.1 client ที่ขอ keep-alive | ผ่าน |
| Smoke test หลังแก้ | รันเส้นทางจริงอีกครั้ง | 1 request, 0.656 วินาที RTT, 3.8 วินาทีรวม |

## ไฟล์หลักที่เปลี่ยน

| ไฟล์/กลุ่มไฟล์ | การเปลี่ยนแปลง |
|---|---|
| `external/Agent-Robotic-Cluster/src/constants/collectionPrompt.ts` | Frozen Jetson collection prompt |
| `external/Agent-Robotic-Cluster/src/constants/prompts.ts` | เลือก collection prompt จาก environment flag |
| `external/Agent-Robotic-Cluster/src/tools.ts` | ลด collection tool set |
| `external/Agent-Robotic-Cluster/src/tools/RobotControl/shared.ts` | Compact model-facing tool result |
| `external/Agent-Robotic-Cluster/src/tools/RobotControl/*Tool.ts` | ใช้ compact result ใน RobotControl tools |
| `external/Agent-Robotic-Cluster/src/tools/RobotControl/shared.compact.test.ts` | Regression tests สำหรับ result compaction |
| `benchmark/telemetry/arc_pipe.mjs` | เปิด frozen prompt สำหรับ Jetson |
| `benchmark/telemetry/measure_prompt_budget.py` | วัด prompt/result ก่อนและหลัง |
| `benchmark/results/prompt_budget.json` | ผลการวัด token budget |
| `benchmark/telemetry/jetson_proxy.py` | แก้ response framing |
| `benchmark/tests/test_jetson_proxy.py` | Regression test สำหรับ keep-alive client |
| `external/Agent-Robotic-Cluster/dist/cli.mjs` | CLI bundle หลัง rebuild |

ไฟล์เดิมสำรองอยู่ที่ `runtime_state/backups/prompt-slim-20260907`

## ผลการตรวจสอบ

| การตรวจสอบ | ผล |
|---|---|
| Python proxy/prompt/collector tests | 11/11 ผ่าน |
| Bun compact-result tests | 3/3 ผ่าน |
| Python compile | ผ่าน |
| ARC CLI build | ผ่าน |
| ARC SDK build | ผ่าน |
| CLI version | ARC 0.9.2 |
| Jetson readiness | llama และ LiteLLM online |
| Loaded model | `Qwen3.8-27B-UD-Q4_K_XL.gguf` |
| Smoke test หลังแก้ | `OK`, 1 request, 0 tool calls |
| Full repository typecheck | ยังมี errors เดิมจาก source snapshot/optional modules แต่ focused tests และ production build ผ่าน |

## ความปลอดภัยและสิ่งที่ไม่เปลี่ยน

- ไม่ลดหรือ bypass Python robot guardrails
- เมื่อพบ `guardrail_blocked=true` หรือ `fault_type=guardrail_blocked` ต้องหยุด อธิบาย และรอ operator
- สองแขนยังทำงาน sequential เท่านั้น
- Storage placement ยังต้องใช้ `place_to_storage_slot`
- Raw tool output และ telemetry ยังเก็บไว้ครบ การลดเกิดเฉพาะ payload ที่ส่งเข้า model context
- Smoke tests ไม่มี robot tool call และไม่มีการเคลื่อนหุ่น

## สรุปงานวันนี้

งานวันนี้ปิดตามเป้าหมาย: ลด prompt/context, ลด tool-result token, rebuild และ verify collection profile, ตรวจ Jetson จริงแบบไม่ขยับหุ่น และแก้ proxy timeout ซึ่งเป็นสาเหตุหลักของการรอ 120 วินาที ผลสุดท้ายคือเส้นทางทดสอบตอบได้ในไม่กี่วินาที มี request เดียว และพร้อมใช้เก็บ benchmark รอบต่อไป

รอบ benchmark ถัดไปควรแยก cold-cache และ warm-cache หลายรอบต่อ condition แล้วรายงาน median/P95 เพื่อไม่ให้ผล cache hit รอบเดียวถูกตีความว่าเป็นความเร็วของโมเดลทั้งหมด

แนวคิด
เดิมเป็นฟังก์ชันลอย ๆ ที่ login ใหม่ทุกครั้ง → รวบเป็น client ตัวเดียวที่ถือ session + cache แล้วให้ทั้ง 2 section ใช้ตัวส่งฟอร์มร่วมกัน (_submit) เพราะตรรกะ 302 เหมือนกันเป๊ะ

ประสิทธิภาพ

จุด	เดิม	ใหม่
login	ทุก call (3 requests)	ครั้งเดียว แล้ว cache ต่อ cookie
ตาม redirect	ยิง GET เพิ่มทุกครั้ง	follow=False เป็นค่าเริ่มต้น
dropdown / ผลค้นหา	–	cache ในหน่วยความจำ
session หมดอายุ	เด้ง error ทันที	login ใหม่ + retry ให้อัตโนมัติ (เฉพาะกรณีใช้ user/pass)
เน็ตสะดุด	ตาย	retry backoff
ความยืดหยุ่น

ส่งแค่ชื่อ ไม่ต้องรู้ id — vehicle: "1ฒย-838" → helper ยิง autocomplete เติม vehicle_id, driver_id, mechanic_id และดึง owner_type_id จากทะเบียนรถให้เอง (ทดสอบแล้วได้ payload ตรงกับที่ Bew ส่งมาเป๊ะทุกฟิลด์)
ตำแหน่งยางใช้รหัสได้ — ["F1","RA3"] แทน [1,5]
ฟิลด์ใหม่ที่ ATMS เพิ่มมา ส่งผ่านได้เลย ไม่ต้องแก้โค้ด
payload รับได้ทั้ง dict และแบบ Postman [{key,value}]
รูปแนบรับ 4 แบบ — path / bytes / base64 / data-URI
dry_run=True ดูฟอร์มที่จะส่งโดยไม่สร้าง job จริง
หา id ไม่เจอ = ส่งเป็น free text เหมือนที่คนพิมพ์เองในเว็บ แล้วรายงานกลับใน unresolved (ไม่ล้มทั้ง request)
Endpoints
เส้น	ใช้ทำอะไร
POST /atms/openjob/	เปิด job — ใส่ items มาด้วยได้ จบใน call เดียว
POST /atms/openjob/item	เพิ่มรายการซ่อมแยก (ต่อจาก maintenance_request_id)
GET /atms/openjob/options	ค่า dropdown จริง — frontend ไม่ต้องฝัง magic number
GET /atms/openjob/lookup?kind=vehicle&q=	ค้นทะเบียนรถ / คนขับ / ช่าง
GET /atms/openjob/search?code=SBMR26070457	ค้นรายการ job (code / vehicle / ช่วงวันที่)
GET /atms/openjob/{ref}	ดึง job ที่เปิดไว้ — ref เป็น id (175039) หรือ code (SBMR26070457)

อ่านกลับ (GET /{ref})
ref เลขล้วน = maintenance_request_id ยิงเข้า /veh/maintenance.request/view/id/<id> ตรง ๆ · ไม่ใช่เลข = เลขที่แจ้งซ่อม ค้นจากหน้า index ให้ก่อนแล้วค่อยเปิด view (cache code→id ไว้ ไม่ค้นซ้ำ)
อ่าน 2 หน้าเพราะให้คนละอย่าง — view ได้ป้ายภาษาไทยแบบที่คนอ่าน (info), edit ได้ชื่อฟิลด์ตรงกับตอน POST (fields) เอาไปแก้แล้วยิงกลับได้เลย, labels แปลง id เป็นข้อความจาก dropdown ให้
ATMS เปลี่ยนหน้าเมื่อไหร่ → เรียกด้วย ?raw=true จะได้ HTML ดิบมาดูด้วย
ช่องค้นหาใน /search อ่านสดจากฟอร์มจริง — filter ที่ ATMS ไม่รู้จักจะไม่ล้ม แต่ถูกรายงานไว้ใน ignored (fields บอกชื่อช่องที่ใช้ได้จริง)

ผลทดสอบจริง (id 175039 = SBMR26070457, session จริง)
เส้น	ผล
GET /veh/maintenance.request/view/id/175039	200 — ใช้เป็นแหล่งหลัก
GET /veh/maintenance.request/edit/id/175039	200 — ได้ vehicle_id/branch_id/driver_id ครบ
GET /veh/maintenance.request/index?code=...	200 — ค้น code → id ได้ (SBMR26070457 → 175039)
GET /veh/maintenance.request.item/index/...	500 — ATMS ไม่ได้ทำหน้านี้ไว้ จึงกลืน error แล้วดึง items จากตารางในหน้า view แทน
id ที่ไม่มีจริง	ATMS ตอบ 500 ไม่ใช่ 404 — เราแปลงเป็น HTTP 404 ให้ผู้เรียก
ช่องค้นหาจริงในหน้า index: code, plate_no, vehicle_no, branch_id, project_id, flow, driver, mechanic, accident, supplier, owner_type_id, is_broken, from/to_schedule_at, from/to_garage_entry_at, from/to_garage_finish_at, from/to_estimate_finish_at, from/to_close_at, order_by
ตัวอย่างสั้นสุด:


c = AtmsClient(phpsessid="...")
c.create_job(
    {"schedule_at": "30/07/2026 16:51", "branch_id": "4",
     "vehicle": "1ฒย-838", "tire_positions": ["F1", "F2"]},
    items=[{"maintenance_type_id": "9", "problem": "ยางหน้าสึก"}],
)
ไฟล์: atms_openjob_service.py · openjob.py — wrapper ฟังก์ชันเดิม (open_job, add_job_items) ยังเรียกได้เหมือนเคย

⚠️ ทดสอบครั้งนี้เลี่ยงการสร้าง job ใหม่ ยิงเข้า job 175414 เดิม — ตอนนี้มี 3 items (252282, 252283, 252326) ค้างใน production
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
ตัวอย่างสั้นสุด:


c = AtmsClient(phpsessid="...")
c.create_job(
    {"schedule_at": "30/07/2026 16:51", "branch_id": "4",
     "vehicle": "1ฒย-838", "tire_positions": ["F1", "F2"]},
    items=[{"maintenance_type_id": "9", "problem": "ยางหน้าสึก"}],
)
ไฟล์: atms_openjob_service.py · openjob.py — wrapper ฟังก์ชันเดิม (open_job, add_job_items) ยังเรียกได้เหมือนเคย

⚠️ ทดสอบครั้งนี้เลี่ยงการสร้าง job ใหม่ ยิงเข้า job 175414 เดิม — ตอนนี้มี 3 items (252282, 252283, 252326) ค้างใน production
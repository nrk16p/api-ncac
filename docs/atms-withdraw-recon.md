# ATMS เบิก (WD) / รับคืน (DD) — ผลสำรวจ 31/10/2026

ใช้กับ `services/atms_withdraw_service.py` · fixture อยู่ใน `tests/fixtures/atms_withdraw/` (ลบชื่อคน username เบอร์ ทะเบียน ออกแล้ว — repo นี้เป็น public)
ทั้งหมดเปิดดูอย่างเดียว **ยังไม่ได้ลองเขียนจริง** (ระบบความปลอดภัยของเครื่องบล็อกการเขียน ATMS) ข้อที่มี ⚠️ = อนุมานจากลิงก์ในหน้า ยังไม่ได้ยืนยัน ต้องยืนยันใน Task 10

## ทั่วไป
- Zend form ไม่มี CSRF · ปุ่ม "บันทึก" เป็น `<a class="submit-link">` ที่ JS ส่ง `#basic-input-form` → POST field ตรง ๆ ไม่ต้องมี field submit
- session หมด = redirect ไป `/account/user/login` (ใช้ `_get_html` ของ openjob ที่จับได้อยู่แล้ว)
- หน้าทุกหน้า: หัวข้อมูลเป็น `<dt>label :</dt><dd>value</dd>` (ใช้ `_label_pairs`) · ตารางรายการเป็น `table.defaultTable`

## WD หัวใบ — `POST /inv/withdraw/add` (fixture `wd_add.html`)
`type=withdraw` · `inventory_id` (select 22 คลัง) · `pick_to_id` (select 60) · `extra_cost=0.00` · `remark` · `extra_type` (hidden ว่าง; หน้า edit มีค่า `normal`)
สำเร็จ ⚠️ = 302 Location มี `/inv/withdraw/view/id/<id>` (แพทเทิร์นเดียวกับฟอร์ม ATMS อื่น) — ถ้า Location ไม่มี id ให้หาจาก `find_withdraw_by_remark`
หมายเหตุใบจริงที่สโตร์ใช้: `"<MR code> <ประเภทรถ> <ลูกค้า> <ทะเบียน>/<ชื่อ>"` → ของเราใช้ `"<SW-no> <MR code> <ทะเบียน> <remark>"`

## WD รายการ — `POST /inv/withdraw.item/add/good_movement_id/<gm>/inventory_id/<inv>` (fixture `wd_item_add.html`)
`inventory_id` · `good_movement_id` · `maintenance_request_item` (text) + `maintenance_request_item_id` · `sku_tag_id` · `ajax_sku_tag_id` · `sku` (text = name จาก sku.json) + `sku_id` · `serial_no` + `serial_no_id` · `has_serial_no` · `amount` · `remark`
บังคับ: `good_movement_id`, `sku`/`sku_id`, `amount`

## WD view — `/inv/withdraw/view/id/<id>` (fixtures `wd_view_empty.html`, `wd_view_approved.html`)
label: รหัส · คลังสินค้า · จุดประสงค์ในการเบิก · ประเภทพิเศษ · ค่าใช้จ่ายเสริม · คืนอะไหล่ครบแล้ว · หมายเหตุ · บันทึกเมื่อ · ผู้ใช้งาน · **approver** (มีเฉพาะใบที่อนุมัติแล้ว)
ตารางรายการ: รายการแจ้งซ่อม (id ตัวเลข) · กลุ่มสินค้า · สินค้า (`<code> : <name>`) · serial no · amount · unit price · รวม · หมายเหตุ · แถวท้าย `grand total`
- ยังไม่อนุมัติ: ลิงก์ `แก้ไข` `/inv/withdraw/edit/id/<id>` · `เพิ่มสินค้า` `/inv/withdraw.item/add/...` · `อนุมัติ` `/inv/withdraw/approve/id/<id>`
- อนุมัติแล้ว: มี `approver :` · ลิงก์ `ไม่อนุมัติ` `/inv/withdraw/reject/id/<id>` · `คืนอะไหล่ครบแล้ว` `/inv/withdraw/spare.part.returned/type/y/id/<id>` · ไม่มีปุ่มเพิ่มสินค้า
- **วิธีบอกว่าอนุมัติแล้ว**: มี label `approver` ที่ค่าไม่ว่าง หรือมีลิงก์ `/inv/withdraw/reject/id/`

อนุมัติ ⚠️ = GET `/inv/withdraw/approve/id/<id>` (เป็นลิงก์ ไม่ใช่ฟอร์ม) แล้วตรวจผลด้วย view
คืนครบ ⚠️ = GET `/inv/withdraw/spare.part.returned/type/y/id/<id>`
⚠️ ใบจริง: ผู้สร้าง (ผู้ใช้งาน) กับ approver มักเป็นคนละคน → บัญชีสโตร์อาจไม่มีสิทธิ์อนุมัติ ต้องเช็กใน Task 10 ถ้าไม่มีสิทธิ์ หน้าจะไม่เปลี่ยนเป็นอนุมัติ → `issue_withdraw` ต้อง raise ข้อความชัด ("บัญชีนี้อนุมัติใบเบิกใน ATMS ไม่ได้")

## WD list — `GET /inv/withdraw/index/?inventory_id=&from_created_at=&to_created_at=` (fixture `wd_index.html`)
คอลัมน์: วันที่เบิก · รหัส · คลังสินค้า · จุดประสงค์ในการเบิก · ทะเบียน · พจส. · เลขไมค์แจ้งซ่อม · จำนวนเงิน · ใบรับสภาพหนี้ · จำนวนเงินรับสภาพหนี้ · approver · ผู้ใช้งาน · หมายเหตุ · (ลิงก์ view/edit)
filter อื่น: `code`, `pick_to`, `user`, `remark`, `spare_part_returned`, `order_by` · approver ว่าง = ยังไม่อนุมัติ

## DD คืนของ — `POST /inv/deposit/add` (fixture `dd_add.html`)
`type=deposit` · `purchase_order_id=""` · `inventory_id` · `supplier_ref_no` · `supplier_inv_date` (DD/MM/YYYY) · `extra_cost` · `withdraw` (text เลข WD) + `withdraw_id` · `remark` · `extra_type`
ใบจริงตัวอย่าง (`dd_view_return.html`): เบิก = LBWD…, เลขที่ใบส่งของ = `-`, หมายเหตุ = ทะเบียน

## DD รายการ — `POST /inv/deposit.item/add/good_movement_id/<dd>/inventory_id/<inv>` (fixture `dd_item_add.html`)
`inventory_id` · `good_movement_id` · `purchase_order_item_id` (ว่าง) · `sku_tag_id` · `ajax_sku_tag_id` · `sku` + `sku_id` · `serial_no` · `has_serial_no` · `amount` · **`unit_price` (บังคับ)** · `remark`
→ ของคืนใช้ `unit_price` = unit price ของบรรทัดใน WD เดิม

## DD view / อนุมัติ
อนุมัติแล้ว: มี `approver :` + ลิงก์ `ไม่อนุมัติ` `/inv/deposit/reject/id/<id>` · อนุมัติ ⚠️ = GET `/inv/deposit/approve/id/<id>`

## Lookups (JSON, GET, `?q=&limit=`)
- `/veh/maintenance.request.item/code.json/` → `[{id, name: "LBMR26100001 อู่ใน-CM-ระบบไฟ : 257041", driver, driver_id, mechanic, mechanic_id, accident, accident_id}]`
- `/inv/sku/sku.json/` + `inventory_id`, `sku_tag_id` → `[{id, code, name, inventory_id, sku_tag_id, has_serial_no}]`
- `/inv/withdraw/code.json/` + `sku_id` → `[{id, name: "LBWD26102206"}]`
- `/inv/deposit.item/serial.no.json/` + `sku_id` → `[{id, name}]`

## Profile — `/account/user/profile` (fixture `profile.html`, ไม่มีฟอร์ม)
label: ผู้ใช้งาน · อีเมล์ · ชื่อ · โทรศัพท์ · สาขา · แผนก · เป็น พจส. · เป็นช่าง · บริษัท

## Env
ใช้ค่าเดิมเป็นค่าเริ่มต้น: `WMS_BASE` ไม่ตั้ง = https://mena-wms.vercel.app · `WMS_STORE_WITHDRAW_KEY` ไม่ตั้ง = `PIPELINE_API_KEY`
· เส้นอ่าน (options/lookup/list/{gm_id}) ไม่ส่งบัญชี = ใช้ `ATMS_USERNAME`/`ATMS_PASSWORD` ของ api-ncac · เส้นเขียน + whoami ต้องมีบัญชีผู้ใช้เสมอ
· รัน uvicorn แบบ worker เดียว (ล็อกต่อผู้ใช้อยู่ใน process)

## PR (ใบขอซื้อ) — เพิ่ม 2/11/2026, kind=pr ใน doc/search และ doc/pr/{id}
⚠️ ยังไม่ได้ยืนยันกับ ATMS จริง (ไม่มีสิทธิ์เปิดหน้าจากเครื่องที่เขียนโค้ด) fixture `pr_index*.html` / `pr_view.html` เป็นของสังเคราะห์ตามโครงหน้า PO + คอลัมน์ที่ pipeline procurement เก็บ
- list: `GET /inv/purchase.request/index?from_t_date=&to_t_date=&inventory_id=&order_by=pr.code desc` (ค่าเริ่มต้นย้อน 180 วัน, 1 หน้า 1000 แถว, ตั้ง/คืน rows-per-page เหมือน PO/DD) · คอลัมน์ `ใบขอสั่งซื้อ (PR)` · `วันที่` · `รวม` · `is approved` (ไอคอน)
- ระบุรถจาก **หมายเหตุ** เท่านั้น: เทียบแบบ lowercase + ตัดช่องว่าง - . / ทั้งสองฝั่ง (ไม่ใช้ filter `remark` ของ ATMS เพราะเทียบตรงตัว) · คืน `remark` ดิบให้ผู้ขอตรวจ · เรียงใหม่สุด · limit เริ่มต้น 5 เพดาน 20
- ถ้าหน้า list ไม่มีคอลัมน์ `หมายเหตุ` จะเปิดหน้า view ทีละใบ (ใหม่→เก่า) หยุดเมื่อครบ limit หรือครบ 40 GET ต่อการค้นหนึ่งครั้ง เว้น 3 วินาทีต่อ GET
- view: `GET /inv/purchase.request/view/id/<id>` · ตาราง `defaultTable` โครงเดียวกับ PO แต่ไม่มี รับ/ค้างรับ → `received=0`, `remaining=amount` · `is_labour` = กลุ่มสินค้าขึ้นต้น ค่าแรง

## MR (ใบแจ้งซ่อม) — เพิ่ม 2/11/2026, `GET /atms/withdraw/mr/search?q=&limit=5`
ยืนยันโครงหน้ากับ ATMS จริงแล้ว (อ่านอย่างเดียว) fixture `mr_index.html` เป็นของสังเคราะห์ ชื่อคนเป็นชื่อสมมติ
- list: `GET /veh/maintenance.request/index?plate_no=|vehicle_no=&order_by=mr.id desc&submit=ค้นหา` · `table.defaultTable` · แมปคอลัมน์จากข้อความหัว: เลขที่แจ้งซ่อม · ขั้นตอน · สาขา · โปรเจค · เลขรถ · ยานพาหนะ (= ทะเบียน) · พจส. · Mechanic · แจ้งซ่อม (DD/MM/YYYY HH:MM, คืนแค่วันที่) · id จากลิงก์ `/veh/maintenance.request/view/id/<id>` แถวไม่มีลิงก์ถูกข้าม
- ยิง 2 GET (plate_no แล้ว vehicle_no) หน่วงตาม pace · ATMS กรอง substring เอง (`71-0626` ตรง `สบ.71-0626`) · รวม ตัดซ้ำด้วย id เรียง id มาก→น้อย · ไม่แตะ rows-per-page (หน้าละ 25 พอสำหรับ 5 ใบล่าสุด)
- q ต้องยาว 2 ตัวขึ้นไปหลัง trim ไม่งั้น `results: []` โดยไม่ยิง ATMS · limit เริ่มต้น 5 เพดาน 20 (limit < 1 = 422) · auth เหมือน doc/search
- หมายเหตุต่อใบ (เพิ่ม 2/11/2026): MR ไม่มีหมายเหตุหัวใบ → หลังเลือกผล ≤ limit ใบแล้ว GET หน้า view `/veh/maintenance.request/view/id/<id>` ทีละใบ (ไม่เกิน 5 GET ต่อการค้น ไม่ว่า limit เท่าไร · เว้นเท่า PR_DETAIL_PACE เหมือนหน้า view ของ PR) · `table.defaultTable` หัว `# | ประเภทการซ่อม | รายละเอียด | มีการอ้างอิง | ค่าใช้จ่ายโดยประมาณ | ค่าใช้จ่ายอื่นๆ | (ปุ่ม)` แถวละหนึ่งรายการซ่อม แมปจากข้อความหัว (ยืนยันกับ ATMS จริงแล้ว อ่านอย่างเดียว) · รายละเอียดมีขึ้นบรรทัด/แท็บ → ยุบช่องว่างเหลือเว้นวรรคเดียว
- ผลลัพธ์เพิ่ม 2 ฟิลด์ต่อใบ: `items: [{type, detail}]` (detail ตัด 120 ตัว) และ `remark` = `"<ประเภทการซ่อม>: <รายละเอียด>"` ต่อรายการ คั่นด้วย ` · ` รวมไม่เกิน 300 ตัว · หน้า view ใบไหนอ่านไม่ได้/ว่าง/พัง → `remark: ""`, `items: []` การค้นไม่ล้ม · เกิน 5 ใบแรก remark ว่าง · fixture `mr_view.html` สังเคราะห์

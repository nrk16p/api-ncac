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

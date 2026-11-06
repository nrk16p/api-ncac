# vehicle_daily

ดึงรายงานรถประจำวันของ ATMS (`/report/excel/index.excel/type/vehicle.daily.transaction`) ครบทุกกลุ่มฟลีท
ไปเก็บที่ `atms.vehicle_daily_transaction` โดยหนึ่งแถวคือรถหนึ่งคันในหนึ่งวัน
โค้ดอยู่ที่ `scripts/vehicle_daily/pipeline_vehicle_daily.py`

ค่าเริ่มต้นดึงเดือนที่แล้วกับเดือนนี้ (เดือนนี้ถึงวันนี้) ทุกกลุ่มที่มีในหน้าฟอร์ม ATMS เรียกกลุ่มละครั้งต่อเดือน
ห่างกัน 3 วินาที รวมราว 21 requests รอบละ 1–2 นาที ตั้งเวลาไว้ใน `main.py` ทุกวัน 07:40 เวลาไทย

การเขียนแทนที่ทีละก้อน (กลุ่ม × เดือน): upsert ตาม `_id` = `fleet_group_id|วันที่|ทะเบียน` แล้วลบแถวที่รอบนี้ไม่ได้แตะ
ถ้า ATMS ตอบกลับเป็นหน้า HTML จะ login ใหม่แล้วลองอีกครั้ง ถ้ายังไม่ได้จะข้ามกลุ่มนั้น ไม่ลบของเดิม และจบรอบเป็น failed
ถ้าได้ 0 แถวแต่ของเดิมมีเกิน 50 แถวก็ไม่ลบเช่นกัน

ฟิลด์: คอลัมน์ทั้ง 28 ช่องของรายงาน (`วันที่`, `ฟลีท`, `ผู้ว่าจ้าง`, `แพลนท์_รหัส/ชื่อ`, `ยี่ห้อ`, `เบอร์รถ`, `ทะเบียน`,
`หาง_เบอร์รถ/ทะเบียน`, `คนขับ_สถานะ/รหัส/ชื่อ/เบอร์โทร`, `สเตตัส_รถ/คนขับ`, `หัวหน้า`, `รถจีน`, `โอนรถข้ามแพลนท์_1/2`,
`Logoff`, `Logon`, `ลางาน`, `ซ่อม`, `หมายเหตุ`, `วัคซีน`) บวก `date` (Date), `year_month`, `plate_norm`
(ทะเบียนตัด "สบ."), `trailer_plate_norm`, `fleet_group_id`, `fleet_group_name`, `etl_run_id`, `etl_run_at`

index: `date` · `plate_norm+date` · `fleet_group_id+year_month` · `คนขับ_รหัส+date`

ย้อนหลังหรือเลือกกลุ่มเอง:

```
curl -X POST "$API_NCAC_URL/pipeline/run/vehicle_daily" -H "x-api-key: $PIPELINE_API_KEY" \
  -H "Content-Type: application/json" -d '{"months": "08/2026,09/2026", "fleet_groups": "1,2"}'
```

`atms.vehicle_daily_asia` (เฉพาะกลุ่ม 1–2) ยังมีคนอ่านอยู่ (driver-cost-pipeline, mena-backend-services driver-daily-sync)
ส่วน pipeline นี้ไม่แตะคอลเลกชันนั้น

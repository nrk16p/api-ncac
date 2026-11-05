# core-return-sync

Pipeline รายวันที่เติมฐานซากอะไหล่ `master_data.core_return_base` ด้วยบรรทัดเบิกใหม่จาก `atms.stockmovement_v5` (3 เดือนล่าสุดรวมเดือนนี้)
โค้ด: `scripts/core_return/pipeline_core_return_sync.py` · กฎกรองและการหาอู่พอร์ตมาจากสคริปต์ core-return-base (ไม่พอร์ต legacy)

- upsert ตาม `row_key` · `$set` ฟิลด์บรรยาย + `loadedAt` · `$setOnInsert` `coreStatus: "pending"`, `coreQtyReturned: 0`, `createdAt`
- ไม่ทับสถานะ/ข้อมูลรับซากที่สโตร์บันทึก · ไม่ลบอะไร · ข้ามบรรทัดที่ (wdCode, skuCode) มีแถว `wms.store-withdraw` อยู่แล้ว
- env: `CORE_RETURN_SYNC_MONTHS` (ค่าเริ่มต้น 3) · `CORE_RETURN_SYNC_DRY_RUN=1` = ไม่เขียน แค่นับ
- ผลลัพธ์ลง log: `months, fetched, after_filters, inserted, updated, skipped_wms, missing_in_atms, seconds` (ดูที่ `analytics.etl_jobs`, `GET /pipeline/status/core-return-sync`)

## Render cron (ผู้ใช้ตั้งเอง)

รันทุกวัน 02:30 เวลาไทย = UTC `30 19 * * *`

```
curl -X POST "$API_NCAC_URL/pipeline/run/core-return-sync" -H "x-api-key: $PIPELINE_API_KEY"
```

ลองแบบไม่เขียนก่อน: เพิ่ม `-H "Content-Type: application/json" -d '{"dry_run": 1}'` (runner แปลงเป็น env `DRY_RUN=1`)

# PDF Conversion (Office → PDF) ผ่าน Gotenberg (LibreOffice) — self-host

แปลง `.docx` (และไฟล์ Office อื่น ๆ) เป็น PDF ให้ตรงต้นฉบับ โดยใช้ **Gotenberg**
(เบื้องหลังคือ LibreOffice) รันเป็น container แยก แล้ว api-ncac ทำหน้าที่ proxy

```
[แอป เช่น mena-partner]  --docx-->  [api-ncac /pdf/convert]  --->  [Gotenberg/LibreOffice]  --PDF-->
```

## Endpoint

| Method | Path | รายละเอียด |
|---|---|---|
| `POST` | `/pdf/convert` | multipart form-data, field **`file`** = ไฟล์ .docx → ตอบกลับเป็น `application/pdf` |
| `GET`  | `/pdf/health` | เช็คว่า api ต่อ Gotenberg ได้ไหม |

ต้องตั้ง env **`GOTENBERG_URL`** = URL ของ Gotenberg (เช่น `http://gotenberg:3000`)

---

## Deploy บน Render (แนะนำ)

api-ncac อยู่บน Render อยู่แล้ว → เพิ่ม Gotenberg เป็นอีก service:

1. **สร้าง Gotenberg service**
   - Render → **New +** → **Private Service** (แนะนำ Private เพื่อไม่ให้เปิดสาธารณะ)
   - เลือก **Deploy an existing image** → image: `gotenberg/gotenberg:8`
   - Region: **เดียวกับ api-ncac** (ให้คุยกันภายในเร็ว)
   - ตั้งชื่อ เช่น `gotenberg` → Create
   - Gotenberg ฟัง port `3000` (Render จับให้อัตโนมัติ)

2. **ตั้ง env ที่ api-ncac**
   - api-ncac → **Environment** → เพิ่ม
     `GOTENBERG_URL = http://gotenberg:3000`
     (ใช้ internal address ของ Private Service; ถ้าเลือกเป็น Web Service สาธารณะ ให้ใช้ URL `https://<gotenberg>.onrender.com`)
   - Save → api-ncac จะ redeploy

3. **ทดสอบ**
   - `GET https://api-ncac.onrender.com/pdf/health` → `{"ok": true, ...}`

> หมายเหตุ: Gotenberg ไม่มี auth ในตัว — จึงควรตั้งเป็น **Private Service** (เข้าถึงได้เฉพาะภายใน Render)
> ส่วน `/pdf/convert` เปิดผ่าน api-ncac ตาม policy ปัจจุบันของ api

---

## Self-host บน VPS / เครื่องเดียว (ทางเลือก)

```bash
docker compose up -d          # รัน gotenberg + api พร้อมกัน
# api: http://localhost:8000   gotenberg (ภายใน): http://gotenberg:3000
```

---

## เรียกใช้ (ตัวอย่าง)

```bash
curl -X POST https://api-ncac.onrender.com/pdf/convert \
  -F "file=@contract.docx" \
  -o contract.pdf
```

จากฝั่ง Node/Next (เช่น mena-partner):

```ts
const fd = new FormData()
fd.append("file", new Blob([docxBuffer]), "contract.docx")
const r = await fetch(`${process.env.NCAC_API}/pdf/convert`, { method: "POST", body: fd })
const pdf = Buffer.from(await r.arrayBuffer())
```

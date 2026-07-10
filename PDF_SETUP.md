# PDF Conversion (Office → PDF) — LibreOffice ในตัว api-ncac

แปลง `.docx` (และไฟล์ Office อื่น ๆ) เป็น PDF ให้ตรงต้นฉบับ โดย **LibreOffice ติดตั้งมากับ
image ของ api-ncac เอง** — ไม่ต้องมี service แยก แปลงได้ในตัว service เดียว

```
[แอป เช่น mena-partner]  --docx-->  [api-ncac /pdf/convert  (LibreOffice ในตัว)]  --PDF-->
```

## Endpoint

| Method | Path | รายละเอียด |
|---|---|---|
| `POST` | `/pdf/convert` | multipart form-data, field **`file`** = ไฟล์ .docx → ตอบกลับ `application/pdf` |
| `GET`  | `/pdf/health` | เช็คว่าเครื่องแปลงพร้อมไหม (`{"ok": true, "engine": "libreoffice"}`) |

```bash
curl -X POST https://api-ncac.onrender.com/pdf/convert -F "file=@contract.docx" -o out.pdf
```

---

## Deploy บน Render — เปลี่ยนเป็น Docker (ครั้งเดียว)

โค้ด/route ทุกอย่างของ ncac **เหมือนเดิม** เปลี่ยนแค่วิธี build เป็น Docker เพื่อให้มี LibreOffice:

1. Render → service **api-ncac** → **Settings**
2. **Runtime / Build** → เปลี่ยนเป็น **Docker** (Render จะเจอ `Dockerfile` ใน repo อัตโนมัติ)
3. **Manual Deploy** → Deploy latest commit
4. เทส: `GET https://api-ncac.onrender.com/pdf/health` → `{"ok": true, "engine": "libreoffice"}`

> ไม่ต้องตั้ง `GOTENBERG_URL` — ถ้าเว้นว่าง จะใช้ LibreOffice ในตัว
> (ถ้าอยากแยกไปใช้ Gotenberg ทีหลัง แค่ตั้ง `GOTENBERG_URL` เดี๋ยว route จะ proxy ไปให้แทน)

**ฟอนต์:** image ติดตั้ง `fonts/cordia.ttc` (CordiaUPC) + fonts-thai-tlwg ให้แล้ว → สัญญาเรนเดอร์ตรงต้นฉบับ

**Build จะนานขึ้น** (ลง LibreOffice ~นาที+ ครั้งแรก) และ image ใหญ่ขึ้น — เป็นเรื่องปกติ

---

## Self-host บน VPS / เครื่องเดียว (ทางเลือก)

```bash
docker compose up -d          # api + LibreOffice ในตัว → http://localhost:8000
```

---

## เรียกจาก Node/Next (เช่น mena-partner)

```ts
const fd = new FormData()
fd.append("file", new Blob([docxBuffer]), "contract.docx")
const r = await fetch(`${process.env.NCAC_API}/pdf/convert`, { method: "POST", body: fd })
const pdf = Buffer.from(await r.arrayBuffer())
```

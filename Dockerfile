# api-ncac + LibreOffice ในตัว (แปลง Office→PDF ได้เอง ไม่ต้องมี service แยก)
# Render: ตั้ง Runtime = Docker แล้ว deploy ตามปกติ (โค้ด/route เดิมไม่เปลี่ยน)
FROM python:3.13-slim

# LibreOffice (แปลง docx→pdf) + ฟอนต์ไทยพื้นฐาน + fontconfig
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-calc \
        fonts-thai-tlwg fonts-liberation fontconfig \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ฟอนต์ CordiaUPC (ให้ LibreOffice เรนเดอร์สัญญาตรงต้นฉบับ)
COPY fonts/ /usr/share/fonts/truetype/mena/
RUN fc-cache -f

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render ส่ง $PORT มาให้ (default 10000) — ใช้ shell form เพื่ออ่าน env
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]

# สำหรับรัน api-ncac ใน docker-compose คู่กับ Gotenberg (self-host)
# (Render ปัจจุบันใช้ Python build เองไม่ต้องพึ่งไฟล์นี้)
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

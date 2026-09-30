# เปิด PaperRef Finder บนมือถือด้วย Streamlit Community Cloud

ใช้ GitHub repo นี้ deploy ได้ โดยเลือก **`streamlit_app.py`** เป็น main file ส่วน `main.py` เป็น FastAPI จึงไม่ใช่ entry point ของ Streamlit

## Deploy จาก GitHub

1. เปิด [Streamlit Community Cloud](https://share.streamlit.io/) แล้วเข้าสู่ระบบ/เชื่อม GitHub ที่มีสิทธิ์เข้าถึง repo
2. เลือกสร้างแอปจาก GitHub repository และกรอก:
   - Repository: `66050275-pun/subject-ai`
   - Branch: `main`
   - Main file path: `streamlit_app.py`
3. ใน Advanced settings เลือก Python **3.12** หากมีให้เลือก
4. ถ้ามีคีย์ฐานข้อมูลวิชาการ ให้เพิ่มในช่อง **Secrets** ตามตัวอย่างด้านล่าง ข้ามได้ถ้ายังไม่มี
5. กด Deploy ระบบจะติดตั้ง dependencies จาก `requirements.txt` และเปิดแอป
6. เปิด URL ที่ Streamlit ออกให้ เช่น `https://<ชื่อแอป>.streamlit.app` ใน Safari/Chrome บนมือถือ แชร์ URL ให้ผู้ใช้ได้ ไม่ต้องเปิด Mac หรือรัน Uvicorn ที่บ้าน

ไม่ต้องตั้ง CORS ให้เรียก FastAPI ข้ามเว็บ ไม่ต้องใส่ URL `localhost:8000` และไม่ต้องเปิดพอร์ต backend อีกพอร์ต ตัวเชื่อมเรียก FastAPI ภายใน process ของ Streamlit

## Secrets (ไม่บังคับ)

ตั้งเฉพาะค่าที่คุณมีจริง โดยใช้ชื่อเหล่านี้ตรงตัว:

```toml
PAPERREF_CONTACT_EMAIL = "you@example.com"
OPENALEX_API_KEY = "your-openalex-key"
SEMANTIC_SCHOLAR_API_KEY = "your-semantic-scholar-key"
CORE_API_KEY = "your-core-key"
```

ดูแม่แบบที่ `.streamlit/secrets.toml.example` คีย์ใน Cloud Secrets ไม่ต้องอยู่ใน GitHub และไฟล์ `.streamlit/secrets.toml` ถูก gitignore ไว้สำหรับการทดสอบในเครื่อง

คีย์วิชาการของเจ้าของแอปแชร์โควตากับผู้ใช้ทุกคน หากไม่ใส่ CORE key ระบบจะข้าม CORE และแจ้งสถานะตามเดิม คีย์ API ไม่ได้รับรองว่าจะไม่มี rate limit ส่วนอีเมลติดต่อ Unpaywall ใช้กรอกในหน้าแอปได้ ไม่ใช่การเข้าสู่ระบบมหาวิทยาลัย

**ไม่ต้องตั้ง AI key ของเจ้าของแอปใน Secrets** ผู้ใช้เลือก provider/model และกรอกคีย์ของตนใน sidebar การใช้ AI จึงคิดตามบัญชีผู้ใช้ แอปไม่ได้หยิบ shared AI key มาใช้

## ทดสอบบนเครื่อง

```bash
cd subject-ai
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

เปิด `http://localhost:8501` ส่วนหน้า FastAPI เดิมยังเปิดได้ด้วย `uvicorn main:app --reload` ตามปกติ หากต้องการลองจากโทรศัพท์ใน Wi-Fi เดียวกัน ใช้ `python -m streamlit run streamlit_app.py --server.address 0.0.0.0` แล้วเปิด `http://<IP เครื่อง Mac>:8501` บนมือถือ ไม่ต้องปิด CORS/XSRF protection ไม่ต้องเปิดพอร์ตออกอินเทอร์เน็ตเพื่อ deploy บน Cloud

## ฟีเจอร์และไฟล์

- `streamlit_app.py`: หน้า Streamlit สำหรับ PDF/DOI แยกชุดโค้ด/AI/DOI, ค้น OA เพิ่ม, เลือก references, export JSON/CSV/ลิงก์ PDF, สรุป, intent, synthesis, graph themes และ Q&A
- `streamlit_backend.py`: เรียก FastAPI เดิมผ่าน ASGI ภายใน process รับ multipart และรายงาน NDJSON ทีละ event สำหรับ AI extraction/OA search จึงไม่ต้องรอผลทั้งหมดเพื่อแสดงความคืบหน้า
- `streamlit_graph.py`: ฝัง SVG renderer เดิม รองรับลาก/ซูม/Fit และเปิดแหล่งต้นทาง เลือกเปเปอร์สำหรับ AI ผ่าน multiselect ของหน้า Streamlit
- `.streamlit/config.toml`: ธีม, จำกัด upload 50 MB, headless server และเปิด CORS/XSRF protections

หน้า Streamlit ใช้ widget ที่เหมาะกับมือถือ จึงมีหน้าตาต่างจาก Tailwind เดิม แต่ใช้ extractor/resolver/AI endpoints ชุดเดียวกัน ไม่ได้สร้างตรรกะค้นหาอีกชุด

## ข้อมูลผู้ใช้และข้อจำกัด

PDF, ผลค้นหา, แช็ต และคีย์ AI อยู่ใน session ของผู้ใช้ในหน่วยความจำเซิร์ฟเวอร์ ไม่ถูกเขียนเป็นไฟล์หรือ global cache คีย์ส่งผ่านเซิร์ฟเวอร์เพื่อเรียก provider ไม่ได้อยู่เฉพาะในเบราว์เซอร์ กดล้างคีย์/ล้าง session เมื่อใช้เสร็จ การรีเฟรชหรือเปลี่ยนการเชื่อมต่ออาจเริ่ม session ใหม่; ดาวน์โหลดผลก่อนปิดหน้า

Cloud อาจพักแอปเมื่อไม่มีการใช้งาน และมีข้อจำกัดทรัพยากร/การอัปโหลด งาน AI หรือการค้นหลายแหล่งอาจใช้เวลา/โควตา ไม่ได้รับรอง uptime หรือจำนวนผู้ใช้พร้อมกัน งาน PDF ใช้ได้สูงสุด 50 MB; intent/AI graph themes สูงสุด 100 references ส่วน synthesis เลือก 3–5 รายการ

แอป Cloud ไม่ได้เชื่อม VPN ของมหาวิทยาลัยคุณ เปิดหน้า publisher และเข้าสู่ระบบผ่านสถาบันจากเบราว์เซอร์ของผู้ใช้ตามเดิม ลิงก์ PDF เป็น provider-indexed และยังอาจเปิดไม่ได้

## การตรวจสอบ

```bash
python -m unittest discover -s tests -v
```

ครอบคลุม backend เดิม, ASGI multipart/NDJSON, streamed error, Streamlit reruns, ทุก action AI ด้วย API จำลอง, provenance/selection, PDF/DOI และการแยกคีย์ระหว่าง sessions ตรวจด้วย Chromium ที่ความกว้างมือถือ 390 px รวมการอัปโหลดและกราฟ ไม่ได้ deploy บัญชี Streamlit ของผู้ใช้หรือทดสอบคีย์จริง

ถ้า deploy แล้วเกิด error ให้ดู **Manage app → Logs** ก่อน ตรวจ main file path, dependencies และชื่อ Secrets ไม่ต้องส่งค่าคีย์จริงมาในแช็ต

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

## หน้าตาและไฟล์ที่ใช้ร่วมกัน

Streamlit และ FastAPI ใช้หน้าเว็บ **ชุดเดียวกัน** จาก `static/index.html`, `static/app.css` และ `static/graph.js` จึงมีดีไซน์ การ์ดรายการ กราฟลาก/ซูม กล่อง AI และการแยกชุดโค้ด/AI/DOI เหมือนกัน หน้าจอปรับตามความกว้างบนคอม โทรศัพท์ และ iPad โดยไม่มีหน้า widget อีกชุด

- `streamlit_app.py`: entry point ของ Cloud และตัวห่อหน้าเว็บแบบเต็มพื้นที่
- `streamlit_frontend.py`: อ่าน HTML/CSS/กราฟเดิมแล้วฝัง assets ภายใน component ไม่ต้องคัดลอกดีไซน์มาแก้สองที่
- `streamlit_component/index.html`: ตัวเชื่อม browser fetch, upload และ streaming ผ่าน Streamlit; เก็บหน้าเว็บเดิมไว้ระหว่าง reruns เพื่อไม่รีเซ็ตไฟล์ที่เลือก กราฟ แช็ต และ checkbox
- `streamlit_transport.py`: เรียก FastAPI เดิมผ่าน ASGI ภายใน process แยกคำขอแต่ละ session ส่ง NDJSON ทีละส่วน รองรับการยกเลิกและป้องกันการยิงคำขอซ้ำเมื่อ rerun
- `.streamlit/config.toml`: จำกัด upload 50 MB, headless server และเปิด CORS/XSRF protections

หากแอปที่ deploy อยู่ยังแสดงหน้าเก่า ให้ตรวจว่าเลือก branch `main` และ main file `streamlit_app.py` แล้วกด **Manage app → Reboot app** จากนั้นรีเฟรชเบราว์เซอร์ การเปลี่ยน UI ครั้งนี้ไม่ต้องสร้างแอปใหม่

## ข้อมูลผู้ใช้และข้อจำกัด

ไฟล์ที่เลือก ผลค้นหา แช็ต และคีย์ AI อยู่ในหน่วยความจำหน้าเว็บของผู้ใช้ เมื่อเรียก API ข้อมูลที่จำเป็นจะผ่านเซิร์ฟเวอร์ใน session นั้นเพื่อประมวลผลและเรียก provider ไม่ได้อยู่เฉพาะในเบราว์เซอร์ ตัวเชื่อมล้าง payload คำขอออกจาก component หลังเซิร์ฟเวอร์รับแล้ว และล้างส่วนคำตอบเมื่อเบราว์เซอร์ยืนยันรับ ไม่มีการเขียน PDF/คีย์ลงไฟล์หรือ global user-data cache การจำ provider/โมเดลใช้ local storage เฉพาะเมื่อผู้ใช้เปิดตัวเลือกในตั้งค่า ไม่รวมคีย์ เอกสาร หรือแช็ต

อย่าแชร์คีย์ AI ใน GitHub หรือแช็ต ล้างคีย์ในหน้าแอปเมื่อใช้เสร็จ การรีเฟรช ปิดแท็บ หรือหลุดจาก session ทำให้ไฟล์/แช็ตและผลที่ไม่บันทึกหาย ประวัติที่บันทึกไว้เปิดกลับได้จากหมวดประวัติ ดาวน์โหลดสำรองไว้ก่อนล้างข้อมูลเว็บไซต์

Cloud อาจพักแอปเมื่อไม่มีการใช้งาน และมีข้อจำกัดทรัพยากร/การอัปโหลด งาน AI หรือการค้นหลายแหล่งอาจใช้เวลา/โควตา ไม่ได้รับรอง uptime หรือจำนวนผู้ใช้พร้อมกัน งาน PDF ใช้ได้สูงสุด 50 MB; intent/AI graph themes สูงสุด 100 references ส่วน synthesis เลือก 3–5 รายการ

แอป Cloud ไม่ได้เชื่อม VPN ของมหาวิทยาลัยคุณ เปิดหน้า publisher และเข้าสู่ระบบผ่านสถาบันจากเบราว์เซอร์ของผู้ใช้ตามเดิม ลิงก์ PDF เป็น provider-indexed และยังอาจเปิดไม่ได้

## ตั้งค่าและความเป็นส่วนตัว

กดปุ่ม **⚙ ตั้งค่า** เพื่อเปิด drawer ฝั่งซ้าย มีหมวดเชื่อมต่อ AI, ทางลัดพื้นที่ทำงาน, ประวัติ และความเป็นส่วนตัว รองรับ Escape, โฟกัสด้วยคีย์บอร์ด และจอสัมผัส ตัวเลือกจำ provider/โมเดลปิดเป็นค่าเริ่มต้น ปิดตัวเลือกอีกครั้งเพื่อลบค่าที่จำไว้ คีย์ API ไม่ถูกเก็บใน local storage หรือคุกกี้

แอปไม่เพิ่มคุกกี้ติดตาม/โฆษณา Streamlit อาจมีคุกกี้จำเป็นเพื่อความปลอดภัยและการเชื่อมต่อ ใช้ข้อความอธิบายในตั้งค่าแทนการขอ consent สำหรับคุกกี้ที่แอปไม่ได้ตั้ง ฟอนต์มาจาก Google Fonts; นโยบายของโฮสต์/AI provider/เว็บไซต์บทความยังใช้ตามบริการนั้น ๆ

Tailwind เป็นไฟล์ CSS ภายในแอป (`static/utilities.css`) ไม่โหลดสคริปต์ CDN ตัว frontend ใช้ CSP จำกัดสคริปต์ด้วย hash FastAPI เพิ่ม no-store สำหรับ API, nosniff และ no-referrer ดูขอบเขตและวิธีสร้าง CSS ใหม่ใน [SECURITY.md](SECURITY.md)

## ประวัติในเครื่อง (IndexedDB)

เปิด **⚙ ตั้งค่า → ประวัติ** เพื่อดูผลค้นหาจากโค้ด AI และ DOI พร้อมวันเวลา ประวัติบันทึกเฉพาะ metadata/รายการอ้างอิงและลิงก์ ไม่รวมไฟล์ PDF, API key, แช็ต หรือข้อความบริบท citation ในเนื้อหา แต่เก็บ metadata ผู้แต่ง/ปี/abstract สรุป AI และพิกัด/ธีมกราฟ เก็บล่าสุดสูงสุด 50 รายการ / 8 MB และสูงสุด 1 MB ต่อผล เมื่อเต็มนำรายการเก่าสุดออก หากเบราว์เซอร์ไม่อนุญาต storage ยังค้นหาได้ตามปกติ

เปิดผลเดิมได้โดยไม่เรียก API อีก การวิเคราะห์ AI ที่ต้องใช้เนื้อหา PDF ต้องแนบไฟล์ต้นฉบับใหม่ ผลที่เปิดจากประวัติไม่สร้างรายการซ้ำ การค้น OA เพิ่มอัปเดตลิงก์ในประวัติชุดนั้นเมื่อจบ

ปิดตัวเลือกบันทึกเพื่อหยุดบันทึกใหม่ (ไม่ลบประวัติเดิม) ลบรายรายการหรือกดล้างทั้งหมดได้ กดดาวน์โหลดสำรองเพื่อ export JSON และนำเข้า JSON เพื่อย้ายผลมาอีกเบราว์เซอร์/อุปกรณ์ รองรับไฟล์สำรองไม่เกิน 16 MB/50 รายการ การนำเข้าที่รูปแบบผิดหรือเกินพื้นที่จะไม่เปลี่ยนประวัติเดิม

IndexedDB เป็นข้อมูลของ **browser profile + URL origin** ไม่ใช่บัญชีผู้ใช้ ไม่ซิงก์คลาวด์ ประวัติ local กับ Streamlit แยกกัน ผู้ใช้ browser profile เดียวกันอาจเห็นข้อมูลร่วมกัน และโหมดส่วนตัว/การล้างข้อมูลเว็บไซต์อาจลบประวัติ ใช้ export/import เมื่อต้องการย้ายเครื่องหรือเปลี่ยน URL

## การตรวจสอบ

```bash
python -m unittest discover -s tests -v
```

Unit tests ครอบคลุม backend เดิม, frontend assets ชุดเดียวกัน, ASGI multipart/JSON/NDJSON, cancellation, การยืนยันรับผล, คำขอซ้ำจาก reruns และการแยก session ทดสอบเบราว์เซอร์ Chromium กับ Streamlit จริงและ API จำลองที่ขนาด 1440×1000, 390×844, 768×1024 และ 1024×768 รวมอัปโหลด, streaming, ทุก action AI, export และการรักษาข้อมูลเมื่อปรับขนาดหน้าจอ ไม่ได้ deploy บัญชี Streamlit ของผู้ใช้หรือทดสอบคีย์จริง

Browser regression สำหรับ IndexedDB (ติดตั้ง Playwright เฉพาะเครื่องทดสอบ ไม่จำเป็นตอน deploy):

```bash
python -m pip install playwright
python -m playwright install chromium
python tests/browser_history.py
```

ทดสอบด้วย Streamlit จริงและ API จำลอง ครอบคลุม reload, เปิดผลเก่า, export/import, ลบประวัติ, จำกัดจำนวน, quota rollback, storage ถูกบล็อก, ไม่เก็บคีย์ และมือถือ/iPad

ถ้า deploy แล้วเกิด error ให้ดู **Manage app → Logs** ก่อน ตรวจ main file path, dependencies และชื่อ Secrets ไม่ต้องส่งค่าคีย์จริงมาในแช็ต

## Research Workspace

ในหมวดประวัติ ค้นหาตามชื่อ/ผู้แต่ง/ปี เลือกหลายงานเพื่อหาอ้างอิงร่วมหรือ export BibTeX และเลือก 2–3 งานเพื่อเปรียบเทียบด้วย provider/key เดิม มีโหมด Incognito สำหรับหยุดทั้งบันทึกและอัปเดตในแท็บนี้ รายละเอียด architecture, API และข้อจำกัดอยู่ใน [HISTORY_WORKSPACE.md](HISTORY_WORKSPACE.md)

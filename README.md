# 🚌 SmartBus Balancer — Süni İntellekt Əsaslı Nəqliyyat İdarəetmə Sistemi

> **Digital Azerbaijan Challenge 2026 – Yarımfinal Həlli**  
> **Komanda:** [Komanda Adı / Nömrəsi]  
> **Tarix:** 31 Oktyabr 2026  

---

## 📌 1. Layihə Haqqında Və Ümumi Konsept

**SmartBus Balancer**, şəhər ictimai nəqliyyatında sərnişin sıxlığını real vaxt rejimində monitorinq edən, süni intellekt və simulyasiya alqoritmləri vasitəsilə sıxlıq anomaliyalarını təsbit edib operatorlara optimal avtobus rebalanslaşdırılması tövsiyə edən proqram təminatıdır.

### 🌟 Əsas Modullar və Funksionallıqlar
* ⏱️ **Real-Vaxt ETA (Gəliş Vaxtı) Hesablanması:** Sərnişinlər marşrut və dayanacaq seçdikdə növbəti avtobusun dəqiq gəliş vaxtını və mövcud doluluğunu görür.
* 📊 **Sıxlıq Qiymətləndirilməsi:** Sıxlıq səviyyələri 3 kateqoriya üzrə dinamik qiymətləndirilir:
  * 🟢 **AŞAĞI** (< 40% doluluq)
  * 🟡 **ORTA** (40% - 70% doluluq)
  * 🔴 **YÜKSƏK** (> 70% doluluq)
* 🚨 **Anomaliya Təsbiti və Operator Bildirişləri:** Yüksək sıxlıq və ya xətt üzrə interval kəsintisi (gap/bunching) olduqda operator paneline avtomatik bildiriş və əlavə avtobus ayrılması tövsiyəsi düşür.
* ⚡ **Bir Kliklə Rebalanslaşdırma:** Operator tövsiyə olunan dəyişikliyi tətbiq etdikdə sistem resursları avtomatik yenidən paylayır.
* 🛡️ **Oflayn Təhlükəsizlik (Fallback Engine):** Şəbəkə və ya CDN bağlantısı kəsildikdə belə xəritə və analitika xətaya düşmədən işləməyə davam edir.

---

## 🏗️ 2. Arxitektura və API Axını

Sistem modul və layered (qatlı) arxitektura prinsipinə əsaslanır:
[ Frontend (HTML5/Tailwind/Leaflet) ]
│  HTTP / REST API
▼
[ API Qatı (FastAPI Routing & Exception Handlers) ]
│
▼
[ Biznes Məntiqi (LiveSimulation / Service Layer) ]
│
▼
[ Məlumat BAZASI (SQLite WAL Mode / In-Memory Repository) ]
### 🔌 Əsas API Endpoint-ləri
* `GET /api/passenger/eta` — Dayanacaq üzrə real-vaxt ETA və gözləyən sərnişin göstəricisi.
* `GET /api/operator/alerts` — Aktiv sıxlıq bildirişləri və sistem tövsiyələri.
* `POST /api/operator/alerts/{id}/apply` — Anomaliyanı həll etmək üçün avtobus yönləndirilməsi.
* `GET /api/recommendations` — Günün saatına uyğun optimal avtobus bölgüsü.
* `POST /api/sim/step` — Simulyasiyanı addım-addım irəli aparmaq.

---

## 🛠️ 3. Texnologiya Steki (Tech Stack)

* **Backend:** Python 3.10+, FastAPI, Uvicorn, Pydantic
* **Frontend:** Single-Page Application (SPA), HTML5, Tailwind CSS, Leaflet.js (Xəritə və Heatmap)
* **Verilənlər Bazası:** SQLite (WAL rejimində, yüksək oxuma/yazma performansı üçün)
* **Test və Etibarlılıq:** `unittest`, `httpx`, `pytest`
* **CORS & Təhlükəsizlik:** FastAPI Middleware, Konfiqurasiya edilə bilən X-API-Key qorunması.

---

## 🧪 4. Testlər və İşə Salınma Təlimatı

### Quraşdırma və Virtual Mühit
```bash
# 1. Backend qovluğuna keçin
cd smartbus/backend

# 2. Virtual mühiti yaradın və aktivləşdirin
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 3. Asılılıqları quraşdırın
pip install -r requirements-dev.txt
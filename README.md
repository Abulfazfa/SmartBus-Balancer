# 🚌 SmartBus Balancer — Real-Time City Transport Intelligence System

![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Framework](https://img.shields.io/badge/framework-FastAPI-emerald.svg)
![Tests Status](https://img.shields.io/badge/tests-117%20passed%20%7C%200%20failed-success.svg)
![Database](https://img.shields.io/badge/database-SQLite%20(WAL)-orange.svg)
![License](https://img.shields.io/badge/license-MIT-blue)

> **Digital Azerbaijan Challenge 2026 – Süni İntellekt Əsaslı Proqramlaşdırma Müsabiqəsi**  
> **Mərhələ:** Yarımfinal Həlli  
> **Tarix:** 31 Oktyabr 2026 | **Məkan:** Azərbaycan Texniki Universiteti (AzTU)  

---

## 📋 Məzmun

- [📌 1. Layihə Haqqında Və Həll Edilən Problem](#-1-layihə-haqqında-və-həll-edilən-problem)
- [🏛️ 2. Arxitektura Və Məlumat Axını](#️-2-arxitektura-və-məlumat-axını)
- [⚡ 3. API Sənədləşməsi Və Modullar (Swagger UI)](#-3-api-sənədləşməsi-və-modullar-swagger-ui)
- [🛠️ 4. Quraşdırma Və İşə Salınma Təlimatı](#️-4-quraşdırma-və-işə-salınma-təlimatı)
- [🕹️ 5. Demo Ssenarisi (3 Dəqiqəlik Yoxlama)](#️-5-demo-ssenarisi-3-dəqiqəlik-yoxlama)
- [🧮 6. Əsas Alqoritmlər Və Eşik Dəyərləri](#-6-əsas-alqoritmlər-və-eşik-dəyərləri)
- [🧪 7. Test Strategiyası, Defektlər Və Etibarlılıq](#-7-test-strategiyası-defektlər-və-etibarlılıq)
- [🤖 8. AI İstifadə Bəyannaməsi (AI Declaration)](#-8-ai-istifadə-bəyannaməsi-ai-declaration)

---

## 📌 1. Layihə Haqqında Və Həll Edilən Problem

Pik saatlarda şəhər ictimai nəqliyyatında bəzi xətlərdə həddən artıq sərnişin sıxlığı (*overload*) yarandığı halda, digər xətlərdə avtobuslar aşağı doluluq faizi ilə fəaliyyət göstərir. **SmartBus Balancer** bu disbalansı real vaxt rejimində təsbit edən, sərnişinlər, operatorlar və dispetçerlər üçün dörd əsas modulu özündə birləşdirən ağıllı nəqliyyat platformasıdır:

1. ⏱️ **Real-Vaxt Gəliş Vaxtı (ETA):** Sərnişinlərə avtobusun yerini, gözlənilən gəliş vaxtını (dəqiqə) və canlı sıxlığını rəng indikatorları ilə göstərir.
2. 🚨 **Anomaliya Təsbiti Və Operator Bildirişləri:** Xətt sıxlığı 10 dəqiqə fasiləsiz **YÜKSƏK (≥75%)** olduqda avtomatik bildiriş yaradır və boş xəttdən avtobus köçürülməsini təklif edir.
3. ⚖️ **Avtomatik Balanslaşdırma Mühərriki (Greedy Optimizer):** Xətlər üzrə gözləmə müddətini hesablayır, optimal avtobus rebalanslaşdırılması aparır və "Əvvəl / Sonra" KPI müqayisəsini təqdim edir.
4. 🛰️ **IoT Telemetriya İnteqrasiyası:** Real avtobus cihazlarından gələn GPS və sərnişin sayı verilənlərini qəbul edə bilən açıq API qatı (`POST /api/telemetry`).

---

## 🏛️ 2. Arxitektura Və Məlumat Axını

Sistem dörd müstəqil qatdan (*Layered Clean Architecture*) ibarətdir. Asılılıqlar yalnız yuxarıdan aşağıya doğrudur:

```text
       ┌─────────────────────────────────────────────────────────┐
       │     Frontend (HTML5 / Tailwind CSS / Leaflet.js)        │
       └────────────────────────────┬────────────────────────────┘
                                    │ HTTP / REST API (JSON)
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │       API Qatı (main.py - FastAPI / Swagger UI)        │
       │    • Pydantic Validasiya   • 422 Azeri Error Format     │
       └────────────────────────────┬────────────────────────────┘
                                    │
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │   Servis Və Simulyasiya Qatı (service.py / live.py)     │
       │    • Simulyasiya Saatı     • Thread-safe (RLock)        │
       └────────────────────────────┬────────────────────────────┘
                                    │
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │   Nüvə Və Data Qatı (engine.py / db.py / SQLite WAL)    │
       │    • Greedy Optimizer      • Atomic Transactions        │
       └─────────────────────────────────────────────────────────┘
```

### 📂 Layihənin Qovluq Quruluşu

```text
smartbus/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI tətbiqi, marşrutlaşdırma və static server
│   │   ├── service.py         # Balanslaşdırma xidmətləri və biznes məntiqi
│   │   ├── live_service.py    # Canlı simulyasiya idarəçisi
│   │   ├── live.py            # Canlı simulyasiya nüvəsi (GPS, doluluq, ETA)
│   │   ├── engine.py          # Metrika və Greedy rebalanslaşdırma alqoritmi
│   │   ├── forecast.py        # Tələbatın 7 günlük proqnozlaşdırılması
│   │   ├── geo.py             # Xətt həndəsəsi və GPS-xətt proyeksiyası
│   │   ├── db.py              # SQLite bağlantısı (WAL rejimində)
│   │   ├── simulator.py       # Tarixi data generasiyası və seed mexanizmi
│   │   ├── errors.py          # Vahid xəta formatlaşdırıcısı
│   │   └── static/            # Frontend resursları
│   │       └── index.html     # Single-Page Application (SPA) interfeysi
│   ├── tests/                 # 117 Avtomatlaşdırılmış unit və integration testləri
│   ├── requirements.txt
│   └── requirements-dev.txt
└── README.md
```

---

## ⚡ 3. API Sənədləşməsi Və Modullar (Swagger UI)

Tətbiq işə düşdükdən sonra interaktiv **Swagger UI** sənədləşməsi **`http://127.0.0.1:8000/docs`** ünvanında aktiv olur.

| Kateqoriya | Metod | Endpoint | Təsviri |
| :--- | :---: | :--- | :--- |
| **System** | `GET` | `/api/health` | Sistem sağlamlığının yoxlanılması |
| **Passenger** | `GET` | `/api/passenger/routes` | Aktiv marşrutlar və canlı sıxlıq rəngləri |
| | `GET` | `/api/passenger/routes/{id}/stops` | Marşrut üzrə dayanacaqlar |
| | `GET` | `/api/passenger/buses` | Avtobusların canlı xəritə koordinatları |
| | `GET` | `/api/passenger/eta` | Dayanacaq üzrə ETA, doluluq və sərnişin tövsiyəsi |
| **Operator** | `GET` | `/api/operator/overview` | Xətlər üzrə sıxlıq, trend və 10 dəq aktivlik |
| | `GET` | `/api/operator/heatmap` | Sıxlıq istilik xəritəsi nöqtələri (Heatmap) |
| | `GET` | `/api/operator/anomalies` | Qruplaşma, hərəkətsizlik və boşluq anomaliyaları |
| | `GET` | `/api/operator/alerts` | Açıq "Əlavə avtobus lazımdır" bildirişləri |
| | `POST`| `/api/operator/alerts/{id}/apply` | Bildirişdəki avtobus köçürməsini tətbiq etmək |
| | `POST`| `/api/operator/alerts/{id}/dismiss` | Bildirişi bağlamaq |
| **Balancing**| `GET` | `/api/routes?hour={h}` | Saatlıq tələbat, tutum, doluluq və gözləmə |
| | `GET` | `/api/routes/{id}/profile` | Xəttin gün boyu tələbat/tutum SVG qrafiki |
| | `GET` | `/api/recommendations` | Greedy alqoritminin avtobus köçürmə tövsiyələri |
| | `POST`| `/api/recommendations/apply` | Tək avtobus köçürməsini icra etmək |
| | `POST`| `/api/recommendations/apply-all` | Bütün optimal tövsiyələri bir kliklə tətbiq etmək |
| | `GET` | `/api/comparison?hour={h}` | "Əvvəl / Sonra" KPI və gözləmə müqayisəsi |
| **Simulation**| `GET` | `/api/sim/state` | Canlı simulyasiya saatı və statusu |
| | `POST`| `/api/sim/control` | Sürət (0-600x), pauza və vaxta keçid |
| | `POST`| `/api/sim/step` | Simulyasiyanı +N dəqiqə irəli aparmaq |
| | `POST`| `/api/sim/incident` | Demo üçün nasaz avtobus simulyasiyası yaratmaq |
| **Telemetry** | `POST`| `/api/telemetry` | Real IoT avtobus cihazından GPS/sərnişin qəbulu |

---

## 🛠️ 4. Quraşdırma Və İşə Salınma Təlimatı

### Quraşdırma Və Virtual Mühit

```bash
# 1. Backend qovluğuna daxil olun
cd smartbus/backend

# 2. Virtual mühit yaradın və aktivləşdirin
python -m venv .venv

# Windows (PowerShell):
.\.venv\Scripts\activate
# PowerShell skript bloklaması xətası verərsə:
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process

# Linux / macOS:
source .venv/bin/activate

# 3. Asılılıqları quraşdırın (Python 3.10+ tələb olunur)
pip install -r requirements-dev.txt
```

### Avtomatlaşdırılmış Testlərin İcrası (117 Test)

```bash
python -m unittest discover -s tests -t .
```
> **Gözlənilən nəticə:** `Ran 117 tests in ~6.3s — OK` (Bütün nüvə, servis və API testləri xətasız keçməlidir).

### Canlı Serverin Başladılması

```bash
uvicorn app.main:app --port 8000 --reload
```
* 🌐 **Canlı Veb İnterfeys:** `http://127.0.0.1:8000/`
* 📑 **İnteraktiv API (Swagger):** `http://127.0.0.1:8000/docs`

---

## 🕹️ 5. Demo Ssenarisi (3 Dəqiqəlik Yoxlama)

1. **Simulyasiya Saatı:** Üst paneldə vaxtı `07:30` seçin.
2. **Bildiriş Yaradılması:** **Operator** tabına keçin və `+10 dəq` düyməsini iki dəfə basın. Sıxlığı 75%-dən yuxarı olan xətt üçün bildiriş yarancaq.
3. **Rebalanslaşdırma:** Bildirişdəki `Tətbiq et` düyməsini sıxın. Avtobus boş xəttdən köçürüləcək, sıxlıq düşəcək və sayğac təhlükəsiz sıfırlanacaq.
4. **Anomaliya Testi:** "Nasazlıq yarat" bölməsindən bir avtobus seçib 10 dəqiqəlik dayandırın. 3 dəqiqə irəli getdikdən sonra "Hərəkətsiz" (*stopped*) anomaliyası çıxacaq.
5. **Balanslaşdırma Tabı:** `08:00` saatı üçün `Hamısını tətbiq et` düyməsini basın. "Əvvəl / Sonra" paneldə orta gözləmə vaxtının azalmasını izləyin.

---

## 🧮 6. Əsas Alqoritmlər Və Eşik Dəyərləri

### 6.1. Xətt Göstəriciləri Və Gözləmə Müddəti (Evristik Model)
$$\text{Dövrə Müddəti (dəq)} = 2 \times \frac{\text{Uzunluq (km)}}{18\text{ km/saat}} \times 60 + 6\text{ dəq (son dayanacaq)}$$

$$\text{Saatlıq Tutum} = \text{Avtobus Sayı} \times \left(\frac{60}{\text{Dövrə}}\right) \times 80\text{ sərnişin}$$

$$\text{Orta Gözləmə (dəq)} = \frac{\text{İnterval}}{2} \times \left(1 + \max\left(0, \text{Doluluq} - 0.8\right) \times 5\right)$$

### 6.2. Sabit Dəyərlər Və Eşiklər
* `LOAD_IDLE`: **0.40** (< 40% Boş / donor ola bilən xətt)
* `LOAD_BUSY`: **0.75** (≥ 75% Yüksək / Yüklü xətt)
* `LOAD_CRITICAL`: **0.85** (> 85% Kritik / Avtobus tələb edən xətt)
* `HIGH_WINDOW_S`: **600 saniyə** (10 dəqiqə fasiləsiz Yüksək olduqda bildiriş yaradılır)
* `HIGH_EXIT`: **0.70** (Histerezis həddi - titrəmənin qarşısını alır)
* `STOPPED_ALERT_S`: **180 saniyə** (3 dəqiqə hərəkətsizlik kritik anomaliya sayılır)

---

## 🧪 7. Test Strategiyası, Defektlər Və Etibarlılıq

Yarışma gedişində münsiflərin qiymətləndirmə meyarlarına uyğun olaraq 3 kritik defekt təsbit edilmiş və testləşdirilərək düzəldilmişdir:

1. **İdempotentlik Və Sayğac Düzəlişi (`LiveSimulation.acknowledge`):** Bildiriş tətbiq edildikdən dərhal sonra eyni xəttə təkrar bildirişin düşməsi xətası aradan qaldırıldı (sayğac sıfırlandı).
2. **Atomik Tranzaksiya (`Repository.record_move`):** Jurnal yazılışı uğursuz olduqda avtobus bölgüsünün yarıda dəyişməməsi üçün bazada atomik tranzaksiya rejimi quruldu.
3. **Yarış Vəziyyəti (Race Condition) Kilidi (`LiveManager`):** Paralel gələn `apply_move`, `reset` və `reseed` sorğuları simulyasiya saatının `RLock` kilidi daxilinə keçirildi.
4. **Oflayn Fallback Mexanizmi:** CDN şəbəkəsi kəsildikdə və ya `leaflet-heat.js` yüklənmədikdə interfeys çökmür, avtomatik daxili `L.circle` indikatorlarına keçir.

---

## 🤖 8. AI İstifadə Bəyannaməsi (AI Declaration)

Bu layihədə Generativ AI alətlərindən (Gemini / Anthropic Claude / AI Coding Assistants) **yalnız yarışma müddətində** aşağıdakı məqsədlər üçün istifadə olunmuşdur:

* **UI/UX İnterfeysi (`index.html`):** Sərnişin, Operator və Balanslaşdırma tablarının dizaynı, SVG qrafiklərinin tərtibi və `leaflet-heat` fallback rejiminin yazılması.
* **FastAPI Xəta İşləyiciləri (`errors.py`):** Pydantic 422 xəta mesajlarının vahid Azərbaycan dilində `{detail: mətn}` formatına çevrilməsi.
* **Test Ssenarilərinin Şaxələndirilməsi:** 117 unit və integration testlərinin tərtib edilməsi.

**Dürüstlük Qeydi:** *Biznes məntiqi, rebalanslaşdırma tövsiyələri və anomaliya təsbiti runtime zamanı heç bir xarici LLM API çağırışı etmir, tamamilə deterministik riyazi alqoritmlərlə hesablanır.*

# Smart Bus Balancer

**Problem:** pik saatlarda bəzi avtobus xətlərində sıxlıq həddən artıq olur, digərlərində avtobuslar boş gedir.
**Həll:** avtobuslardan real vaxtda GPS + sərnişin sayı toplanır, sıxlıq ölçülür; sərnişinə ETA və sıxlıq
göstərilir; operatora sıxlıq xəritəsi, anomaliya xəbərdarlığı və "əlavə avtobus lazımdır" bildirişi
(boş xəttdən hansı avtobusun yönləndiriləcəyi təklifi ilə) verilir.

## Modullar
**Sərnişin modulu**
- Xətt və dayanacaq seçildikdə avtobusların **xəritədə yerləşməsi** və **gəlmə vaxtı (ETA)**.
- Hər avtobusun **sıxlığı: Aşağı (<40%) / Orta (40-75%) / Yüksək (≥75%)**.
- Məsləhət: "Növbəti avtobus dolu olacaq, 4 dəq sonrakı daha boşdur".

**Operator modulu**
- **GPS əsaslı sıxlıq xəritəsi (heatmap):** avtobusların doluluğu + dayanacaqlarda gözləyən sərnişinlər.
- **Anomaliya xəbərdarlığı:** avtobusların qruplaşması, interval boşluğu, tam dolu / boş gedən avtobus,
  hərəkətsiz qalan avtobus (nasazlıq), sıxlığın qəfil artması.
- **10 dəqiqəlik qayda:** xətt üzrə sıxlıq **10 dəqiqə fasiləsiz Yüksək** olarsa, "Əlavə avtobus lazımdır"
  bildirişi yaranır. Bildiriş boş xətdən hansı avtobusun yönləndiriləcəyini təklif edir; operator
  **"Tətbiq et"** düyməsi ilə qəbul edir, sistem avtobusu köçürür və sıxlığın düşməsi canlı görünür.
  Sıxlıq 70%-dən aşağı düşəndə bildiriş avtomatik "həll olunub" statusu alır.
- **Balanslaşdırma mühərriki:** xətlər üzrə gözləmə müddəti, tövsiyələr, əvvəl/sonra müqayisəsi.

## İşə salma (3 əmr)
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```
Baza (`backend/smartbus.db`, SQLite) ilk işə salmada avtomatik yaranır. API sənədləri: http://localhost:8000/docs
`frontend/` qovluğundakı səhifələr eyni serverdən təqdim olunur.

## Testlər
```bash
cd backend
pip install -r requirements-dev.txt            # pytest + httpx (API testləri üçün)
python -m unittest discover -s tests -t .      # və ya: pytest
```
`fastapi`/`httpx` olmadan API testləri **atlanır** (nüvə və servis testləri yenə işləyir).
Testlər: nüvə (engine, forecast, live, geo), servis (müvəqqəti SQLite ilə bütün axınlar), xəta mesajları, API (TestClient).

## Təhlükəsizlik (isteğe bağlı API açarı)
Varsayılan olaraq açıqdır (demo). Açar təyin olunsa, bütün **dəyişdirən** endpointlər (`POST /api/*`) `X-API-Key` tələb edir,
oxuma (`GET`) açıq qalır:
```bash
SMARTBUS_API_KEY=gizli-acar uvicorn app.main:app --port 8000      # Windows (PowerShell): $env:SMARTBUS_API_KEY="gizli-acar"
curl -X POST localhost:8000/api/reset -H "X-API-Key: gizli-acar"
```

## "Real vaxt" necə təmin olunur
- **Real mənbə:** avtobusdakı cihaz `POST /api/telemetry` ilə `{bus_id, lat, lon, occupancy}` göndərir. GPS
  xəttə proyeksiya olunur (500 m-dən uzaq nöqtə rədd edilir), sərnişin sayı 5 dəq saxlanılır.
- **Demo mənbəyi:** real cihaz olmadığı üçün eyni məlumatı **canlı simulyator** istehsal edir
  (hər real saniyə = 1 simulyasiya dəqiqəsi, sürəti dəyişmək olar). ETA, xəritə, anomaliya və bildirişlər
  mənbədən asılı olmadan eyni işləyir.
- Simulyasiya modeli: avtobus mövqeyi xəttin dövrə fazasından hesablanır; doluluq xətt üzrə tələbat/tutum
  nisbətindən və avtobusun qarşısındakı interval uzunluğundan asılıdır (uzun interval = çox sərnişin);
  dolu avtobus bir qədər yavaş gedir (nəticədə avtobuslar zamanla qruplaşa bilir - real hadisə).

## Arxitektura
```
frontend (HTML/JS, Leaflet xəritəsi)
   │  REST (JSON)
app/main.py ............ FastAPI marşrutları + canlı saat (hər saniyə simulyasiyanı irəli aparır)
 ├─ app/service.py ...... saatlıq balanslaşdırma, tövsiyələr, tətbiq, müqayisə
 ├─ app/live_service.py . canlı idarəçi: sərnişin/operator sorğuları, bildirişlərin DB-yə yazılması
 │    └─ app/live.py .... canlı simulyasiya: GPS, doluluq, ETA, heatmap, anomaliya, 10 dəq qaydası
 ├─ app/engine.py ....... metrika (doluluq, gözləmə) + greedy yönləndirmə alqoritmi
 ├─ app/forecast.py ..... tələbat proqnozu (7 gün ortalaması x trend, gələcəyə baxış yoxdur)
 ├─ app/geo.py .......... xətt həndəsəsi, GPS -> xətt proyeksiyası
 ├─ app/db.py ........... SQLite (xətlər, dayanacaqlar, tələbat, bölgü, bildirişlər, jurnal)
 └─ app/simulator.py .... tarixi data (14 gün + bu gün), dayanacaqlar (koordinatlar təxminidir)
```
Hesablama qatları (`engine`, `forecast`, `live`, `geo`) verilənlər bazasından və veb-dən asılı deyil,
təmiz Python-dur, ona görə sürətli test olunur.

## API
**Sərnişin**
| Metod | Yol | Təsvir |
|---|---|---|
| GET | `/api/passenger/routes` | Xətlər + cari sıxlıq |
| GET | `/api/passenger/routes/{id}/stops` | Dayanacaqlar (xəritə xətti üçün) |
| GET | `/api/passenger/buses?route_id=` | Avtobusların canlı mövqeyi və sıxlığı |
| GET | `/api/passenger/eta?route_id=&stop_seq=&direction=forward\|backward` | Gələn avtobuslar, ETA, sıxlıq, məsləhət |

**Operator**
| Metod | Yol | Təsvir |
|---|---|---|
| GET | `/api/operator/overview` | Xətlər üzrə sıxlıq, Yüksək müddəti, trend, aktiv bildiriş sayı |
| GET | `/api/operator/heatmap` | Sıxlıq xəritəsi nöqtələri (lat, lon, weight, kind) |
| GET | `/api/operator/anomalies` | Aktiv anomaliyalar (critical/warning) |
| GET | `/api/operator/alerts?only_open=` | "Əlavə avtobus lazımdır" bildirişləri |
| POST | `/api/operator/alerts/{id}/apply` | Təklifi tətbiq et (avtobus köçürülür) |
| POST | `/api/operator/alerts/{id}/dismiss` | Bildirişi bağla |

**Simulyasiya / cihaz**
| Metod | Yol | Təsvir |
|---|---|---|
| GET | `/api/sim/state` | Simulyasiya saatı, sürət, pauza |
| POST | `/api/sim/control` | `{speed, paused, time:"SS:DD"}` (vaxt dəyişəndə simulyasiya yenidən qurulur) |
| POST | `/api/sim/step` | `{minutes}` - irəli sür (demo üçün) |
| POST | `/api/sim/incident` | `{bus_id, minutes}` - avtobus nasazlığı yarat (anomaliya demosu) |
| POST | `/api/telemetry` | `{bus_id, occupancy?, lat?, lon?}` - real cihaz məlumatı |

**Balanslaşdırma (saatlıq analiz)**
`GET /api/routes`, `GET /api/recommendations`, `POST /api/recommendations/apply`,
`POST /api/recommendations/apply-all`, `GET /api/comparison`, `GET /api/routes/{id}/profile`,
`GET /api/history`, `POST /api/reset`, `POST /api/reseed`.

Səhv kodları: 400 (qayda pozuntusu), 404 (tapılmadı), 422 (yanlış parametr), 401 (API açarı təyin olunub, amma yanlışdır).
Bütün xətalar eyni formadadır: `{"detail": "Azərbaycan dilində mətn"}`; 422-də əlavə olaraq `errors: [{field, message}]` verilir.

## Anomaliya qaydaları
| Növ | Şərt |
|---|---|
| `bunching` | Qonşu avtobuslar arasında interval normanın (dövrə/avtobus sayı) 25%-dən azdır |
| `gap` | İnterval normanın 2 mislindən çoxdur (3 mislindən çox = critical) |
| `overload` | Avtobus ≥95% doludur |
| `empty_run` | Avtobus <20% dolu, xətt üzrə orta doluluq ≥60% |
| `stopped` | Avtobus ≥3 dəqiqə hərəkətsizdir (critical) |
| `spike` | Xətt sıxlığı 10 dəqiqədə ≥25 faiz bəndi artıb |

## Etibarlılıq və performans
- Bölgünün dəyişməsi və jurnala yazılması **bir SQLite tranzaksiyasıdır** (yarımçıq vəziyyət qalmır).
- `apply`, `apply-all`, `reset`, `reseed` simulyasiya kilidi altında icra olunur və canlı parkı sinxronlaşdırır
  (saat axını ilə yarış vəziyyəti yoxdur). SQLite WAL rejimində, 10 san gözləmə vaxtı ilə işləyir.
- Operator bildirişi tətbiq edəndən sonra sayğac sıfırlanır: yeni bildiriş üçün yenidən 10 dəqiqə fasiləsiz "Yüksək" lazımdır.
- Ölçmə (HTTP olmadan, servis qatı, 1 kompüter): simulyasiyanın 1 saatı ≈ 65 ms; ETA ≈ 0.1 ms, operator panelləri
  (overview, heatmap, anomalies) < 1 ms, saatlıq analiz və tövsiyə ≈ 3-5 ms. HTTP gecikməsi və yük testi ayrıca ölçülməyib.

## Məlum məhdudiyyətlər (dürüst qeydlər)
- Yük testi (çoxlu paralel sorğu) aparılmayıb; autentifikasiya yalnız sadə API açarıdır (rollar, TLS, audit yoxdur).
- Real cihaz yoxdur; data simulyasiyadır. Koordinatlar və xətt həndəsəsi təxminidir, real marşrutlar deyil.
- Gözləmə müddəti evristik modeldir (interval/2, doluluq 80%-dən yuxarı olduqda artır).
- Tələbat tarixi saatlıq datadan götürülür; hava, tətil və hadisələr nəzərə alınmır.
- Avtobus köçürüləndə hərəkət müddəti (depoya/xəttə getmə) modelləşdirilmir - avtobus dərhal yeni xəttdə görünür.

## AI istifadəsi bəyannaməsi (komanda yoxlayıb özünə uyğunlaşdırmalıdır)
- **Alət:** Claude (Anthropic) - arxitekturanın planlaşdırılması, backend kodu (simulyator, alqoritm, API),
  testlər və sənədləşmə üçün istifadə olunub.
- **Komanda özü:** tələblərin müəyyənləşdirilməsi, nəticələrin yoxlanması, testlərin işə salınması,
  parametrlərin (eşik dəyərləri) seçimi və təqdimat.
- **Kənar mənbələr:** Smart-Bus-System GitHub repozitoriyasının README səhifəsi yalnız ümumi ideya üçün
  oxunub; ondan kod, arxitektura və ya data götürülməyib. *(Bu bənd faktiki vəziyyətə uyğun deyilsə, dəyişin.)*
- Açıq mənbəli kitabxanalar: FastAPI, Uvicorn, Pydantic (backend); Leaflet (xəritə, frontend).

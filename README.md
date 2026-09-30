# Orarul Sălilor — FMI

Orarul Facultății de Matematică și Informatică (Universitatea din București), navigabil
**pe grupe** și **pe săli**, cu ierarhia reală Specializare → Serie → Grupă → Semigrupă.

- `/grupa/244` — orarul unei formațiuni, inclusiv **cursurile moștenite de la serie și an**;
  în dreapta jos, **versiunile anterioare** ale orarului, cu ce s-a schimbat pentru grupă;
  din dropdown alegi ce **opționale / facultative / limbi** vezi (ținut minte în cookie)
- `/sala/701` — gradul de ocupare al unei săli: când e ocupată, ce materie, ce profesor, ce grupă
- `/sala` — toate sălile, cu procentul de ocupare
- `/orarul-meu` — cu cont: orarul grupei tale, cu semigrupa și opționalele ținute în cont
- `/admin` — panoul de administrare: sesizări, editarea orarului, încărcarea unui orar, roluri
- `/admin/review` — celulele pe care extragerea nu le-a putut confirma, cu decupajul alături

Contul e opțional: fără el, ce alegi să vezi se ține în cookie, în browserul tău.

Datele se extrag din orarul public al facultății, **fără apeluri către servicii plătite**:
captură din Google Drive, segmentare geometrică, apoi OCR local (ONNX pe CPU).

## Instalare

Necesită Python ≥ 3.12 (testat pe 3.14).

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

## Pornire

```bash
.venv/bin/alembic upgrade head                            # creează schema
.venv/bin/python -m orar.cli load tests/golden/date.json  # populează baza
.venv/bin/uvicorn orar.web.app:app --reload               # http://127.0.0.1:8000
```

### Ingest din sursă

Necesită `pip install -e ".[ingest]" && playwright install chromium`.

```bash
.venv/bin/python -m orar.cli sincronizeaza --doar-verifica  # ce s-a schimbat pe fmi.unibuc.ro
.venv/bin/python -m orar.cli sincronizeaza                  # + reia ingestul dacă e cazul
.venv/bin/python -m orar.cli surse                          # ce orare cunoaștem, dacă-s la zi
.venv/bin/python -m orar.cli crosscheck --completeaza        # compară cu orarul profesorilor
.venv/bin/python -m orar.cli planuri --descarca              # credite și formă de evaluare
.venv/bin/python -m orar.cli capacitati                      # nr. de locuri, din pagina 2
```

`sincronizeaza` face tot lanțul: citește pagina FMI, salvează ancorele de săptămână, compară
data publicată cu cea de la ultimul ingest reușit și — **doar dacă s-a schimbat** — capturează,
segmentează, citește și încarcă. La final rulează și verificarea încrucișată cu orarul
profesorilor, care e **obligatorie**: fără orarul profesorilor (lipsește de pe pagină sau nu
se poate captura) orarul grupelor nu se înlocuiește. Ce se citește de acolo se păstrează
(`ORAR_PROFESOR`, `PROFESOR.DIN_ORAR`): lista din care se alege un profesor la editare și
potrivirile propuse în coada de verificare. Verificarea paginii e o cerere HTTP; ingestul
complet durează ~20 de minute.

Înainte să înlocuiască orele, `sincronizeaza` păstrează orarul de până atunci ca **versiune
anterioară** (`VERSIUNE_ORAR`, `db/versiuni.py`). Panoul „Versiuni” din dreapta jos — pe pagina
principală, pe `/grupa/{id}` și pe `/sala/{id}` — o deschide cu `?versiune=N`, iar linkurile
dintre pagini o păstrează, deci navighezi în orarul de atunci. O
versiune e o *publicare FMI*: reluarea aceleiași publicări (`--forteaza`) nu adaugă una.
`orar load` nu arhivează — nu știe ce dată de publicare au datele încărcate.

Baza poate ține **mai multe orare deodată** — câte unul pe (an universitar, semestru); un
orar încărcat îl înlocuiește doar pe cel al aceluiași semestru. Paginile arată unul singur:
cel **implicit** sau cel ales din panoul „Orare” (`?perioada=ID`), vizibil pentru toată
lumea. Implicitul îl alege un admin din `/admin` („Orare încărcate”); până atunci e cel
încărcat cel mai de curând (`db/orare.py`).

Pașii se pot rula și separat:

```bash
.venv/bin/python -m orar.cli captureaza https://bit.ly/… -o data/screenshots/sem2-grupe
.venv/bin/python -m orar.cli load data/screenshots/sem2-grupe   # segmentare + OCR + bază
```

`load` acceptă fie un JSON, fie un director de capturi; în al doilea caz rulează tot lanțul.
Vocabularul pentru corecția OCR vine din baza existentă, iar la prima instalare din
`--referinta` (implicit `tests/golden/date.json`).

### Verificare periodică

Orarul se schimbă de câteva ori pe semestru, deci o verificare pe zi ajunge. Două variante:

```bash
# systemd timer / cron — recomandat, un singur proces indiferent câți workeri are web-ul
15 4 * * *  cd /opt/orar && .venv/bin/python -m orar.cli sincronizeaza >> /var/log/orar.log 2>&1
```

```bash
# sau în procesul web, dacă rulezi un singur worker
ORAR_SCHEDULER=1 .venv/bin/uvicorn orar.web.app:app
```

Planificatorul in-process **nu pornește implicit**: cu mai mulți workeri `uvicorn`, fiecare
și-ar porni propriul job și ar captura în paralel în același director.

### Conturi și administrare

Orarul e public. Un **cont** (`/signup`, `/login`) ține *orarul tău*: îți alegi grupa
(`/account`), iar `/orarul-meu` te duce la orarul ei, unde semigrupa și opționalele / facultativele
alese se țin în cont — le găsești la fel de pe orice dispozitiv. Pe celelalte pagini, și fără
cont, aceleași alegeri stau în cookie.

**Evenimente.** Pe lângă orarul săptămânal, un admin poate adăuga activități cu dată
(`EVENIMENT`, `/admin/evenimente`): o *activitate ASMI* sau o *altă activitate*. Spune data și
orele, situl îi arată sălile libere atunci, de la cea mai încăpătoare (`db/evenimente.py`
ține cont de orar și de celelalte evenimente; sâmbăta și duminica nu sunt cursuri). Un
eveniment apare pe toate orarele, doar pe ale unor specializări sau doar pe pagina sălii,
în săptămâna lui; grila primește atunci rânduri de weekend și coloane de seară. Evenimentele
ASMI, inclusiv perioadele fără sală (recrutări, Balul Bobocilor), sunt publice în
`/calendar-asmi`.

**Orarul în cifre** (`/admin/statistici`, `db/statistici.py`): câți oameni au ore în fiecare
interval, câți sunt liberi și câte săli sunt ocupate sau pline, cu filtre pe public
(specializări, ani, serii, grupe), pe activități și pe săli. Mărimile sunt asumate (semigrupă
15, grupă 30; un curs are câte grupe are seria). Filtrele stau în adresă și se pot salva cu
nume (`FILTRU_SALVAT`).

Conturile au un **rol**: `student` (implicit), `voluntar`, `profesor` sau `admin`. Rolurile
se dau din panoul `/admin`, de către un admin; din linia de comandă:

```bash
.venv/bin/python -m orar.cli rol ana@example.com voluntar
```

Adminii nu au „orarul meu”: după autentificare ajung în panou. **Adminul principal** nu stă
în baza de date, ci în mediu — intră pe `/login` cu numele lui în câmpul de email:

```bash
cp .env.example .env      # .env nu intră în git
# ORAR_SECRET=…           obligatoriu în producție: openssl rand -hex 32
# ORAR_ADMIN_USER=…       numele adminului principal
# ORAR_ADMIN_PAROLA=…     sau ORAR_ADMIN_PAROLA_HASH, ca să nu stea parola în clar
# ORAR_HTTPS=1            cookie `Secure`, în spatele TLS
```

Din panou, un admin poate **încărca un orar dintr-un link** — PDF-ul aSc de pe Google Drive
(sau bit.ly-ul către el), opțional și orarul profesorilor. Rulează în fundal exact lanțul lui
`sincronizeaza` (`worker/incarcare.py`), 10–20 de minute, iar panoul arată etapa; orarul de
până atunci rămâne ca versiune anterioară. E o singură tranzacție: dacă pică, baza rămâne
cum era. Se acceptă doar linkuri `https` către Drive — serverul chiar deschide adresa, deci
nu e lăsat să fie trimis oriunde. Ca planificatorul, merge cu un singur worker.

**Sesizări.** Sub orarul oricărei grupe sau săli, „Raportează o problemă” — pentru oricine,
cu sau fără cont: alegi activitatea și scrii ce e greșit. Adminii le văd în `/admin/sesizari`,
în ordinea în care au venit, și le marchează rezolvate sau respinse; „Corectează activitatea”
deschide direct formularul ei.

**Editarea orarului.** Pe pagina unei grupe sau a unei săli, un admin apasă „✎ Editează
orarul”: un click pe o activitate îi deschide formularul, același ca la „+ Adaugă
activitate” — se poate schimba orice: materia, tipul, ziua și orele, profesorul, sala,
pentru cine se ține. Schimbările nu se fac direct în `ORA` —
ingestul șterge și reîncarcă orele semestrului, deci ar dispărea la următorul orar publicat —
ci se țin și ca rânduri în `CORECTIE` (`db/corectii.py`) și se pun la loc după fiecare ingest.
Cele care nu-și mai găsesc activitatea în orarul nou rămân marcate „neaplicate” în
`/admin/corectii`, de unde orice schimbare se poate și anula.

`.env` se încarcă la pornire; variabilele deja setate în mediu au întâietate. Fără
`ORAR_ADMIN_USER` și parolă, adminul principal nu există. Fără `ORAR_SECRET` se generează o
cheie la pornire: sesiunile se pierd la repornire și nu sunt valabile între procese.

Parolele conturilor se stochează cu `hashlib.scrypt` (N=2¹⁵, ~70 ms), cu parametrii scriși în
hash ca să poată fi crescuți fără să invalideze conturile existente. Nu există limitare a
încercărilor de autentificare — dacă expui aplicația public, adaug-o în față (nginx,
fail2ban): contul de admin e exact ținta unui atac cu dicționar.

Alte comenzi:

```bash
.venv/bin/python -m orar.cli stats                          # câte rânduri sunt în bază
.venv/bin/python -m orar.cli reset                          # șterge baza
.venv/bin/python -m orar.cli -v load …                      # + avertismente și calitatea datelor
.venv/bin/python -m orar.cli segmenteaza data/screenshots/sem2-grupe/pag_016.png
.venv/bin/python -m orar.cli citeste   data/screenshots/sem2-grupe/pag_016.png
.venv/bin/python -m orar.cli evalueaza                       # acuratețe pe câmpuri
.venv/bin/python -m pytest -m "not slow"                    # 250 de teste, ~30 s
.venv/bin/python -m pytest                                  # + citirea completă, ~2.5 min
```

Baza e un fișier SQLite în `data/orar.db`; se poate muta cu `ORAR_DB=/alt/loc.db`.

## Cum e organizat

```
src/orar/
├── domain/      logică pură, fără I/O
│   ├── hierarchy.py   „INFO Grupa 144" → an 1, seria 14, grupa 144
│   ├── names.py       „Cheval H" ↔ „Cheval Andrei-Horatiu"
│   ├── abbrev.py      „StructDate" ↔ „Structuri de date"
│   ├── rooms.py       normalizarea sălilor + clasificare fizică/externă/virtuală
│   ├── weeks.py       calendarul academic: numărul și paritatea săptămânii
│   └── grid.py        așează activitățile în grila de 5 zile × 12 ore
├── db/          modele SQLAlchemy, interogările ierarhice, migrări Alembic
├── ingest/      watcher.py (ce publică FMI), capture.py (Drive → PNG),
│               segment.py (PNG → celule), ocr.py (celule → text),
│               lexicon.py (vocabulare închise), evaluate.py (acuratețe),
│               crosscheck.py (a doua sursă), plans.py (planuri de învățământ),
│               tables.py (tabele cu linii), capacities.py (nr. de locuri),
│               load.py (→ bază), consolidate.py (vezi mai jos)
├── worker/      sync.py (lanțul complet), scheduler.py (verificarea zilnică)
└── web/         FastAPI + Jinja2 + HTMX, CSS scris de mână
```

### Opt lucruri care nu sunt evidente

**1. Fiecare pagină din orarul FMI e autonomă.** Pagina grupei 244 conține și cursurile
ținute cu toată seria 24 — deci același curs apare identic pe paginile 241, 242, 243, 244.
Încărcat naiv, un curs de serie devine 4 rânduri `ORA`, sala pare rezervată de 4 ori
simultan, iar ierarhia rămâne goală. `ingest/consolidate.py` detectează activitățile
identice și le urcă la cel mai apropiat strămoș comun: **1146 → 784 de rânduri**, din care
59 devin ore de serie/an. Abia după asta `/sala/{id}` arată ocuparea reală. Rândul păstrat
preia legăturile `ORA_GRUPA` ale **tuturor** duplicatelor: altfel un opțional listat și în
pachetul `INFO (Curs)`, și în `MATE-INFO (Informatica)` rămânea legat doar de unul, iar
seriile celuilalt nu-l mai vedeau — 325 de perechi (grupă, activitate) pierdute. Un test
verifică, pentru fiecare grupă, că orarul e identic înainte și după consolidare.

**2. Segmentarea nu se poate lua după culoare.** aSc umple unele celule cu două tonuri
tăiate în diagonală — aceeași activitate, două culori — iar chenarele dintre celule au 1 px
și se amestecă cu umplerile la randare (între două verzuri, chenarul iese `(87,142,87)`, nu
negru). `ingest/segment.py` taie deci numai unde găsește un minim local de luminanță
*continuu* pe toată lățimea: continuitatea deosebește un chenar de un rând de text.

**3. Detectorul de text al OCR-ului încurcă, nu ajută.** Pe celulele astea rapidocr „din
cutie" rupe `Prunescu M` în `runescu` și taie `ESLA (curs) [sapt 3-4]` în patru bucăți: e
antrenat pe fotografii, nu pe dreptunghiuri de text vectorial pe fundal pastel. Dar decupajul
îl putem face noi, geometric — textul e negru curat, rândurile sunt despărțite de goluri albe.
Măsurat pe toate paginile, 34276 de goluri: raportate la înălțimea rândului, spațiile dintre
cuvinte stau sub 0.8 și separatorii de câmp peste 1.2, iar **între ele nu cade nimic**.
`ingest/ocr.py` taie la 1.0 și folosește din rapidocr doar recunoașterea — ieșirea devine
întreagă, iar pasul e de ~25× mai rapid.

**4. Setul de referință greșește, și se vede unde.** `tests/golden/date.json` (ieșirea
prototipului cu Gemini) taie benzile suprapuse: unde pagina are trei celule de `18-20` una
sub alta, el citește șase celule alăturate de câte o oră. Din 147 de dezacorduri pe `ore`, în
**134** intervalul nostru îl conține strict pe cel din referință — semnătura exactă a acestei
erori. Verificat pe pixeli, plus două grafii greșite care stricau vocabularul, șase celule
pierdute și un `frecventa` inventat: [`docs/formatul-orarului.md` §9](docs/formatul-orarului.md).

**5. Vocabularul nu are voie să se hrănească din propria ieșire.** Corecția OCR folosește
termenii din bază, iar baza e umplută tot de ingest. Fără grijă, o citire greșită intră ca
termen valid și de la a doua rulare devine „cuvânt cunoscut": aceeași celulă se potrivește
perfect cu propria ei greșeală și nu mai ajunge în coada de verificare. De aceea
`Lexicon.din_baza` numără doar termenii care apar în cel puțin un rând unde câmpul **nu** e
marcat nesigur. Mai e o capcană: după verificarea încrucișată, baza ține numele **întregi**
(`Cheval Andrei-Horatiu`), iar celulele le scriu tot prescurtat (`Cheval H`). Comparate ca
șiruri nu seamănă destul, deci un ingest nou ar scoate fiecare profesor „necunoscut" (5 → 65
de activități nesigure). Lexiconul aplică întâi regula de prescurtare din `domain/names.py`
și acceptă numele întreg doar când prescurtarea se potrivește cu **un singur** profesor.

**6. `ORA_GRUPA.ID_GRUPA` nu e pachetul, e ținta.** În jonctiune, `ID_GRUPA` e formațiunea
*căreia i se oferă* opționalul (Seria 33), iar pachetul însuși e `ORA.ID_GRUPA`. Un filtru
scris pe coloana greșită trece oricum, fiindcă ținta e deja în lanțul grupei — arată că merge
și nu filtrează nimic. De aceea dropdown-ul de opționale (`web/afisare.py`) recunoaște o
activitate de pachet după **proprietarul** ei, iar consolidarea păstrează ca proprietar
pachetul, nu grupa, când aceeași oră apare în amândouă.

**7. Același orar e publicat de două ori, și a doua oară e util.** PDF-ul profesorilor (191
de pagini) are aceeași grilă, dar pivotată: profesorul e în titlu, formațiunile în mijlocul
celulei. Din el ies două lucruri pe care o singură sursă nu le poate da — numele **întregi**
(citite dintr-un titlu mare, nu dintr-o bandă de 20 px) și o confirmare independentă pe
fiecare activitate. Rezultat: **668 de activități găsite în ambele surse, 0 divergențe de
profesor**, și 181 de prescurtări din bază înlocuite cu numele complet. Prescurtarea aSc nu e
cea evidentă: `Cheval H` ← `Cheval Andrei-Horatiu` (inițiala **ultimului** prenume),
`BanuDem. I` ← `Banu Demergian Iulia`. Vezi `domain/names.py`.

**8. Numerotarea săptămânilor sare peste vacanțe.** FMI publică:
*„Săptămâna 06.04.2026 – 09.04.2026 este săptămână impară (sapt 7)"* și
*„Săptămâna 20.04.2026 – 24.04.2026 este săptămână pară (sapt 8)"* — două săptămâni
calendaristice distanță, dar una academică (între ele e vacanța de Paște). De aceea
`domain/weeks.py` ține **toate** ancorele publicate și marchează rezultatul ca aproximativ
când nu cade exact pe una, în loc să împartă naiv la 7.

### Opționale, facultative, limbi străine

Sunt pagini-pachet, nu formațiuni, și diferă prin cui se adresează:

- **opționale** — pe *specializare și an* (`Optionale an III - MATE (1)`), uneori pe serii
  (`INFO Seriile 33,34,35: …`); pachetele numerotate sunt liste din care se alege;
- **facultative** — pe *an*, transversal: titlul enumeră specializările
  (`Facultative an II (Mate, Mate-Info, Mate Apl., Info, CTI)`); sunt peste plan;
- **limbi străine** — tot pe an și transversal (`Limbi straine - an I (Mate Info, CTI)`).

`ingest/load.py` le leagă de grupe **permisiv** (`domain/hierarchy.py`): anul se caută oriunde
în titlu (`an III`, `anul 2`, `Ill` citit de OCR), specializările după prescurtări tolerante
(`Mate-lnfo`, `Mate Apl.`, `Mate Info` fără virgulă — care în anul I, unde nu există
MATE-INFO, înseamnă MATE și INFO), **printre cele care există în anul paginii**. O
specializare nouă își ia codul din titlul grupei (`BIO INFO Grupa 171` → `BIO-INFO`), deci nu
trebuie trecută în vreo listă. Când titlul nu numește nicio specializare cunoscută, pachetul
ajunge la **tot anul**. La reingest, formațiunile și pachetele care nu mai apar în orar se
șterg (dacă nu le mai folosește nimic — nici arhiva, nici vreun cont).

Pe `/grupa/{id}`, dropdown-ul *Opționale și facultative* are întâi „Selectează tot”, apoi
fiecare materie, pe categorii. Alegerea se ține **în cookie** (`web/afisare.py`), separat
pentru fiecare pagină; se memorează ce e *ascuns*, deci o materie apărută într-un orar nou
e vizibilă din prima.

## Sursa datelor

Orarul e generat cu **aSc Orare** și publicat ca PDF vectorial pe Google Drive, linkat de pe
<https://fmi.unibuc.ro/orar/>. PDF-ul **are strat de text, dar Drive blochează descărcarea**,
iar preview-ul servește doar pagini rasterizate — deci extragerea cere segmentare + OCR.
Semantica paginii și geometria măsurată sunt documentate în
[`docs/formatul-orarului.md`](docs/formatul-orarului.md).

Drive plafonează randarea unei pagini la **3200×2262 px**, atins exact cu
`device_scale_factor=4`; peste atât imaginea e doar mărită. La plafon tabelul are 2882 px, iar
în cea mai densă bandă textul are 19–24 px — citibil. Sub `LATIME_MINIMA_TABEL` captura
eșuează explicit, în loc să producă imagini din care OCR-ul ar ghici.

`tests/golden/date.json` rămâne setul de referință pentru regresie (vezi §9 din documentație
pentru unde greșește el).

### Acuratețea extragerii

`orar evalueaza`, pe toate cele 98 de pagini (1140 de activități împerecheate):

| câmp | acuratețe | cerința din plan |
| :--- | ---: | ---: |
| `sala` | 100.00% | ≥ 99% |
| `frecventa` | 99.91% | ≥ 99% |
| `saptamani` | 99.82% | — |
| `semigrupa` | 99.82% | ≥ 99% |
| `tip` | 99.74% | ≥ 99% |
| `materie` | 99.56% | ≥ 97% |
| `profesor` | 99.56% | ≥ 95% |
| `ore` | (vezi mai jos) | — |

`ore` iese din aritmetică pe caroiaj, deci nu poate fi aproximativ; cele 147 de dezacorduri
sunt erori ale referinței, nu ale extragerii — 134 dintre ele au exact semnătura descrisă la
punctul 4 de mai sus.

Ce rămâne neconfirmat ajunge în `/admin/review`, nu tăcut în bază: **5 activități din 784**,
toate verificate manual. Patru sunt celule în care aSc a scris textul suprapus, literă peste
literă (`pag_028` și `pag_048`); a cincea e o notă în text liber. Nicio extragere nu le poate
citi, iar afișarea decupajului lângă valorile propuse e singurul mod onest de a le rezolva.

### Stadiu

| | |
| :--- | :--- |
| ✅ Schemă, ierarhie, consolidare, import | funcțional |
| ✅ `/grupa/{id}`, `/sala/{id}`, căutare | funcțional |
| ✅ Captură la rezoluție nativă + segmentare geometrică | funcțional |
| ✅ OCR local + lexicon + coadă de verificare | funcțional |
| ✅ Watcher + sincronizare automată | funcțional |
| ✅ Conturi, „orarul meu”, preferințe în cont (fără cont: în cookie) | funcțional |
| ✅ Verificare încrucișată cu orarul profesorilor | funcțional |
| ✅ Capacitatea sălilor (pagina 2 a orarului) | funcțional |
| ✅ Versiunile anterioare ale orarului, pe grupă | funcțional |
| ✅ Opționale / facultative / limbi pe toți anii, alese din dropdown | funcțional |
| ✅ Sesizări de la vizitatori; editarea orarului de către admin | funcțional |
| 🟡 Panou de administrare (`/admin`) | roluri, încărcarea unui orar dintr-un link; drepturile pe roluri, de adăugat |
| 🟡 Planuri de învățământ (credite, formă de evaluare) | 4 fișiere din 9 |

Creditele și forma de evaluare vin din „Planurile de învățământ", ingestate separat cu
`orar planuri`: **39 de materii completate** din 161. Restul aparțin programelor MATE,
MATE-INFO, MATE APL, ASM și PSFS, ale căror PDF-uri au cifrele desenate ca contururi
vectoriale, nu ca text — vezi [`docs/planuri-de-invatamant.md`](docs/planuri-de-invatamant.md).
**Sălile** se citesc din tabelul de pe pagina 2 a orarului — lista oficială, **30 de săli din
30** cu număr de locuri — cu un segmentator de tabele cu linii trasate (`ingest/tables.py`).
Tabelul se citește **înaintea** orarului și e vocabularul cu care se recunosc sălile din
celule: pe o bază goală, fără el nu se recunoaște nicio sală, cu el toate cele 31. O sală din
tabel fără nicio oră se adaugă oricum în bază (e liberă, nu inexistentă). Din aceeași listă
alege adminul sala, dintr-un dropdown, când editează o activitate.

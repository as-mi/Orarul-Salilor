# Formatul orarului FMI — specificație

Documentul descrie **exact** ce conține o pagină de orar FMI și cum se citește ea.
E specificația de la care pornesc `ingest/segment.py` și `ingest/ocr.py`.

Semantica de mai jos provine din promptul folosit cu Gemini în prototip (validat pe 98 de
pagini / 1143 de activități), iar geometria a fost măsurată direct pe imaginile capturate.

---

## 1. Sursa

- Orarul e generat cu **aSc Orare** (footer: `Orar generat:DD.MM.YYYY` stânga, `aSc Orare` dreapta).
- Publicat ca **PDF vectorial** (`Producer: Acrobat Distiller`, `Title: orar.roz`) pe Google Drive,
  linkat de pe <https://fmi.unibuc.ro/orar/> prin `bit.ly`.
- PDF-ul are strat de text, **dar Drive blochează descărcarea** → accesibil doar rasterizat.
  De aici necesitatea segmentării + OCR. Vezi `docs/` și §2 din plan.
- O pagină = o **formațiune de studiu** (grupă, serie, an, sau pagină de opționale).

## 2. Geometria paginii

Măsurată pe toate cele 98 de pagini capturate (fiecare exact 1610×1120 px).
Valorile de mai jos sunt **proporții**, nu pixeli hardcodați — parserul le derivă din
dreptunghiul exterior detectat, ca să fie independent de rezoluția de captură.

```
┌──────────────────────────────────────────────────────────┐
│                    INFO Grupa 144                        │  ← titlu = numele formațiunii
│  Universitatea din Bucuresti, Facultatea de Matematica…   │  ← subtitlu constant
│  ┌────┬────┬────┬────┬────┬────┬────┬────┬────┬────┐     │
│  │    │ 8  │ 9  │ 10 │ …  │ …  │ …  │ …  │ 18 │ 19 │     │  ← header: 12 coloane orare
│  │    │8:00│9:00│…   │    │    │    │    │    │    │     │     (8:00–8:50 … 19:00–19:50)
│  ├────┼────┴────┴────┴────┴────┴────┴────┴────┴────┤     │
│  │ Lu │                                            │     │  ← 5 rânduri de zi
│  │ Mon│         celule colorate = activități       │     │
│  ├────┼────────────────────────────────────────────┤     │
│  │ Ma │                                            │     │
│  │ …  │                                            │     │
│  └────┴────────────────────────────────────────────┘     │
│  Orar generat:26.04.2026                     aSc Orare   │  ← footer
└──────────────────────────────────────────────────────────┘
```

| Element | Valoare măsurată (la 1610×1120) | Regulă generală |
| :--- | :--- | :--- |
| Chenar exterior tabel | `x ∈ [28, 1582]`, `y ∈ [109, 1068]` | detectat ca cel mai mare dreptunghi negru |
| Separator header | `y = 177` | prima linie orizontală sub header |
| Limita coloană-zi / grilă | `x = 142` | prima linie verticală după eticheta de zi |
| Pas coloană orară | **exact 120 px** | `(X1 − Xgrid) / 12` |
| Înălțime rând de zi | `(1068 − 177)/5 = 178.2 px` | `(Y1 − Yheader) / 5` |

**Maparea orei:** coloana `h ∈ [8..19]` ocupă `x ∈ [Xgrid + (h−8)·pas, Xgrid + (h−7)·pas]`.

**Benzi (lanes):** un rând de zi se subîmparte pe verticală în 1..N benzi, când mai multe
activități coexistă în același interval (semigrupe diferite sau săptămâni alternante).
**Măsurat: până la 5 benzi** într-o singură zi (`tests/fixtures/pag_57.png`, vineri).
Benzile *nu* au înălțime fixă — se deduc din intervalele `y` distincte ale dreptunghiurilor găsite.

**Culoarea NU identifică materia.** Verificat pe 40 de pagini: 124 de culori distincte, iar
aceeași materie primește până la 21 de culori (`POO`). aSc colorează *per lecție*.

**Culoarea nu delimitează nici măcar celula.** Două lucruri strică regula „o celulă = o
suprafață de o culoare”, ambele verificate pe pixeli:

- **Fundal în diagonală.** aSc umple unele celule cu două tonuri, tăiate oblic — de exemplu
  portocaliu-albastru pe `pag_73`, sau colorat-alb pe `pag_51`. Cele două jumătăți sunt
  *aceeași* activitate, cu un singur profesor, o singură materie și o singură sală. O
  segmentare care taie la schimbarea de culoare rupe astfel de celule în două.
- **Chenare estompate.** Liniile dintre celule au 1 px și, la rezoluția de randare, se
  amestecă cu umplerile din jur: între două verzuri, chenarul iese `(87,142,87)` — departe
  de negru. Nu pot fi găsite cu un prag de „negru”; sunt însă minime locale de luminanță.

De aceea `ingest/segment.py` taie **numai** unde există chenar desenat, detectat ca minim
local de luminanță *continuu* pe toată lățimea. Continuitatea e ce deosebește un chenar de un
rând de text: și textul e mai întunecat decât vecinii pe medie, dar lasă goluri între litere.

## 3. Conținutul unei celule

Layout intern **fix**, deci extragerea se face pe **poziție**, nu prin parsare de text liber:

```
┌──────────────────────────────────────┐
│ Dumitru B                    Gr_1    │  ← profesor (stânga-sus) | semigrupă (dreapta-sus)
│    LbForm&Autom (Lab, SI)            │  ← materie (tip[, frecvență]) [sapt X-Y]
│                            L-507     │  ← sala (dreapta-jos)
└──────────────────────────────────────┘
```

În benzile dense layout-ul se comprimă la 2 linii, dar alinierea laterală rămâne:
`linia 1` = profesor (stânga) + `Gr_N` (dreapta); `linia 2` = `Materie (tip) [sapt]` (stânga) + sala (dreapta).

### Câmpuri

| Câmp | Descriere | Valori observate (n = 1143) |
| :--- | :--- | :--- |
| `ore` | Intervalul acoperit de celulă, `"start-end"`. Convenție **capăt exclusiv**: `"14-17"` = 3 coloane = 14:00→17:00. | — |
| `profesor` | Stânga-sus. Mai mulți profesori separați prin `" / "`. Poate lipsi (celula are doar număr de grupă). | 33 de celule cu profesori multipli |
| `materie` | Abreviere aSc (`LbForm&Autom`, `Geom&AlgLin`, `POO`). | — |
| `tip` | În paranteză după materie. | `Lab` 465, `curs` 400, `sem` 115, `seminar` 96, `curs+seminar` 25, `sem+Lab` 21, `proiect` 4, gol 17 |
| `frecventa` | `SI` = săptămână impară, `SP` = pară. Al doilea element din paranteză. | gol 847, `SI` 149, `SP` 147 |
| `saptamani` | Textul exact dintre paranteze drepte, ex. `sapt 1-7`. | 16 pagini; `sapt 1-7` ×51, `sapt 8-14` ×52, restul rare |
| `semigrupa` | `Gr 1` / `Gr_1` / `Gr1` → normalizat `Gr_1..Gr_4`. | gol 801, `Gr_1` 139, `Gr_2` 138, `Gr_3` 42, `Gr_4` 22 |
| `sala` | Dreapta-jos. | 38 de valori distincte — vezi §4 |

### Regex pentru partea structurată

```
^(?P<materie>.+?)\s*
 \((?P<tip>curs|seminar|sem|Lab|sem\+Lab|curs\+seminar|proiect)
   (?:,\s*(?P<frecventa>SI|SP))?\)\s*
 (?:\[(?P<sapt>[^\]]+)\])?$
```

### Reguli

1. Celulă **albă/necolorată** → nicio activitate, se ignoră.
2. Două activități suprapuse vizual în același interval → **două intrări separate**.
3. Pagini de titlu/cuprins/goale → `grupa = ""`, toate zilele `[]`.
4. Câmp absent din imagine → `""`. **Nu se inventează date.**

## 4. Sălile — necesită normalizare

Aceeași sală apare scrisă inconsecvent: `L-507` vs `L.506`, `S-214` vs `S.213`.
Separatorul (`-` sau `.`) nu poartă informație → se normalizează la o formă canonică.

**Săli reale:** `Amf.501/503/701/703`, `L-106/202/401/402/404/507/508/509/511`,
`L.410/411/413/506`, `L-414 Robotica`, `S-214/415/512`, `S.102/107/108/109/111/203/209/210/211/213`.

**Non-săli** (marcate `SALA.TIP = virtual`, excluse din calculul gradului de ocupare):
`ONLINE`, `Magurele`, `Fac.Fizica -Magurele`, `IMAR (sala 309/412/414)`, `lab.`, și string-ul gol.

## 5. Titlul paginii → ierarhie

Codificare regulată: în `NNN`, **cifra 1 = anul**, **cifrele 1–2 = seria**, tot numărul = grupa.

| Formă titlu | Exemplu | Interpretare |
| :--- | :--- | :--- |
| `<SPEC> Grupa NNN` | `INFO Grupa 144` | an 1, seria 14, grupa 144 |
| `<SPEC> Master NNN (COD - Denumire)` | `INFO Master 408 (SD - Sisteme distribuite)` | master, an 1 (4xx) / an 2 (5xx) |
| `<SPEC> Seriile A,B,C: …` | `INFO Seriile 33,34,35: Optionale an III` | se leagă la mai multe serii prin `ORA_GRUPA` |
| `Optionale an N - <SPEC> (k)` | `Optionale an III - MATE (1)` | pachet de opționale |
| `Facultative an N (…)` / `Limbi straine - an N (…)` | | transversale pe specializări |
| `(studenti Fizica/Robotica, an N, grupa N01ROB)` | | caz special |
| `Conferinte si Seminarii` | | fără grupă-țintă |

Serii per specializare: MATE `10/20/30` · MATE APL. `22/32` · MATE-INFO `21/31` ·
INFO `13,14,15 / 23,24,25 / 33,34,35` · CTI `16/26/36`.

## 6. Ce NU se găsește în orar

`MATERIE.CREDITE`, `TIP_MATERIE`, `FORMA_EVALUARE`, `TIP_DISCIPLINA`, `NR_ORE_C/S/L/P` **nu apar
nicăieri** în orar. Sursa lor e folderul *Planurile de învățământ*
(<https://drive.google.com/drive/folders/1cg-GOlmDhAYBiqHJAd_iEes8QKm2GE1U>), ingestat separat.

## 7. Paritatea săptămânilor

Pagina FMI publică ancora, în text:
> Săptămâna 06.04.2026 – 09.04.2026 este **săptămână impară** (sapt 7).

Din ea, `domain/weeks.py` calculează pentru orice dată numărul săptămânii și paritatea SI/SP,
folosite la filtrarea activităților cu `frecventa` sau `saptamani`.

## 8. Pagini care nu sunt orar

PDF-ul are 100 de pagini, dintre care **primele două nu conțin tabel** — segmentarea le
respinge cu `EroareSegmentare`, ceea ce e comportamentul dorit. Ambele sunt însă utile:

| Pagina | Conținut | Folosit pentru |
| :--- | :--- | :--- |
| 1 | Anunțuri + **ancorele de paritate** („Săptămâna 23 – 27 februarie este impară (SI)”) și data actualizării | a doua sursă pentru §7 |
| 2 | **Tabelul de capacități**: `Amf. 501 → 122 locuri`, `L.410 → 15`, … pentru 30 de săli | `SALA.NR_LOCURI` (§11) |

Tabelul de la pagina 2 e și dovada că normalizarea sălilor din §4 e necesară: chiar și acolo
apar amândouă separatoarele — `S-214` lângă `S.102`, `L-106` lângă `L.410`.

## 9. Setul de referință — unde greșește

`tests/golden/date.json` e ieșirea prototipului cu Gemini. E util ca reper, dar **nu e
oracol**. Diferențele față de extragerea deterministă au fost verificate una câte una, pe
pixeli. Ce s-a găsit:

**a) `ore` — referința taie benzile suprapuse.** Unde pagina are trei celule suprapuse pe
`18-20`, referința citește șase celule alăturate de câte o oră (`18-19`, `19-20`, …).
Verificat pe `pag_012` luni și pe `pag_016` vineri, unde adevărul e
`10-12, 12-14, 14-16, 14-16, 16-18, 16-18, 18-20`, iar referința mută două activități la
`16-17` și `17-19`. Din 147 de dezacorduri pe `ore`, în **134** intervalul nostru îl conține
strict pe cel din referință — semnătura exactă a acestei erori. `ore` iese la noi din
aritmetică pe caroiaj, deci nu poate „aproxima” o lățime.

**b) Transcrieri greșite, corectate în golden.** Două afectau vocabularul, deci forțau ieșiri
greșite prin lexicon:

| Referința spunea | Sursa spune | Cum s-a verificat |
| :--- | :--- | :--- |
| `ProgrAvObjJava` (×18), `ProgrAvObjava` (×2) | `ProgrAvObJava` | zoom pe glife, `pag_032` luni 18-20 |
| `ComplAnMate Prof` | `ComplAnMateProf` | pe aceeași pagină apare și nerupt, pe un rând (`pag_076` joi 17-19) |

**c) Celule pierdute.** Referinței îi lipsesc 6 activități reale, verificate vizual —
`pag_014` miercuri (două laboratoare POO), `pag_016` vineri, `pag_046`, `pag_051`, `pag_056`.

**d) Câmpuri inventate.** La `pag_003` vineri 18-20, celula scrie
`Programare competitiva [SAMBATA S.I., ora 10-16]` fără nicio paranteză de tip; referința
pune tot textul în `materie` **și** deduce `frecventa = SI` din „S.I.” — care acolo face parte
din notă, nu e marcaj de săptămână impară.

**e) Patru celule pe care nicio extragere nu le poate citi.** Pe `pag_028` și `pag_048`, aSc
suprapune textul: numele celor patru profesori și `Combinatorica (curs)` sunt scrise unul
peste altul, literă peste literă. Acolo referința e corectă (modelul a ghicit bine), iar
extragerea deterministă marchează câmpurile ca neconfirmate și le trimite în `/admin/review` —
comportamentul corect când imaginea chiar nu se poate citi.

## 10. A doua sursă: orarul profesorilor

FMI publică același orar de două ori. Al doilea PDF (191 de pagini, `bit.ly/4cFmnXo` pentru
semestrul II) are **aceeași grilă aSc**, dar celula e pivotată:

```
  orarul grupelor                      orarul profesorilor
┌─────────────────────────────┐      ┌─────────────────────────────┐
│ Alexe B              Gr_3   │      │ AdvMachLearn (sem, SI)      │
│   AdvMachLearn (sem, SI)    │      │        407/411/412          │
│                     S-415   │      │ Gr_3                S-415   │
└─────────────────────────────┘      └─────────────────────────────┘
  titlu = „INFO Master 407"            titlu = „Alexe Bogdan"
```

Ce se schimbă: **profesorul e în titlu**, iar în mijlocul celulei stau **formațiunile**
(`407/411/412`, `251/252`, `407`). Restul câmpurilor sunt pe aceleași poziții, deci
segmentarea și cea mai mare parte a atribuirii merg neschimbate. Două ajustări au fost
necesare, ambele documentate în cod: recunoașterea listei de formațiuni după formă, și
regula că **prefixul dinaintea parantezei de tip e materia oriunde ar cădea** — la grupe
paranteza e pe al doilea rând, la profesori pe primul.

Paginile conțin și activități administrative fără materie și fără sală (`Consiliu FMI`).

### Prescurtarea numelor

Aceeași persoană e scrisă `Alexe B` în celulă și `Alexe Bogdan` în titlu. Regula aSc **nu**
e „primul cuvânt + inițiala celui de-al doilea”; perechile de mai jos sunt reale:

| În celulă | În titlu | Ce ilustrează |
| :--- | :--- | :--- |
| `Cheval H` | `Cheval Andrei-Horatiu` | inițiala e a **ultimului** prenume |
| `Micluta M` | `Micluta-Campeanu Marius` | numele de familie compus, tăiat la primul cuvânt |
| `BanuDem. I` | `Banu Demergian Iulia` | familia lipită și tăiată **în mijlocul** cuvântului |
| `Marin Le` | `Marin (Velcescu) Letitia` | numele de fată în paranteze; două litere din același prenume |
| `Grecu AE` | `Grecu Alina-Elena` | câte o inițială pentru fiecare prenume |

`domain/names.py` acoperă toate cazurile. Trunchierea în mijlocul unui cuvânt se acceptă
**numai** când prescurtarea e mai lungă decât primul cuvânt și continuă în al doilea —
altfel `Ion L` ar prinde `Ionescu Ioan`, care e alt om.

### Ce a ieșit la verificare

Pe datele din 26.04.2026: **668 de activități găsite în ambele surse, cu 0 divergențe de
profesor**. Cele 118 neconfirmate sunt, în cea mai mare parte, activități ținute de cadre din
afara FMI — laboratoarele de la Măgurele (Fizică), limbile străine — care nu au pagină în
orarul profesorilor. Din cele 189 de nume întregi, **181 de prescurtări din bază au fost
înlocuite** cu numele complet; 6 au rămas ambigue (`Popescu A` se potrivește și cu Adrian, și
cu Ana) și se raportează ca atare.


## 11. Tabelul de capacități (pagina 2)

Alt fel de tabel decât grila orarului, deci altă segmentare. Grila n-are linii interioare
vizibile — de aceea `segment.py` deduce caroiajul din riglele curate de pe margini. Aici,
invers: **toate** liniile sunt trasate, dar nu se știe dinainte câte rânduri sau coloane sunt,
iar pe pagină stau **două tabele alăturate**:

```
+----------------+------------+     +------------------+------------+
| Sală PBT       | Nr. locuri |     | Laborator PBT    | Nr. locuri |
| Amf. 501       |    122     |     | L.410            |     15     |
| S-214          |     70     |     | L-509 (iOS)      |     30     |
```

O proiecție pe toată lățimea le-ar uni, fiindcă liniile unui tabel nu ajung la celălalt.
`ingest/tables.py` caută deci **segmente** — rulaje continue de pixeli întunecați — și
grupează orizontalele după întinderea lor pe `x`. Fiecare grup e un tabel; verticalele care
îl traversează îi dau coloanele. Nimic nu e hardcodat: pe pagina reală ies două tabele de
31×2, detectate singure.

Citirea celulelor folosește exact același motor ca orarul: decupare geometrică pe rânduri de
text, apoi recunoaștere pe bucăți de o singură linie.

**Rezultat: 30 de săli din 30.** Singura fără număr e `L.414 Robotica`, unde sursa însăși
scrie `?` — rămâne `NULL`, nu se inventează.

Două lucruri de reținut:

- Tabelul e și **dovada că normalizarea sălilor era necesară**: chiar în lista oficială,
  `S-214` stă lângă `S.102` și `L-106` lângă `L.410`. Legarea se face pe slug.
- Lămurirea din paranteză descrie dotarea, nu altă sală: tabelul scrie `L-509 (iOS)`, orarul
  doar `L-509`. La legare se încearcă întâi slug-ul întreg, apoi cel fără paranteză.

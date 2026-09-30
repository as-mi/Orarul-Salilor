# Orarul Sălilor

Orarul Facultății de Matematică și Informatică (Universitatea din București), pus într-o
formă în care chiar îl poți folosi: îl cauți pe grupe, pe săli sau pe profesori, vezi doar
ce te privește și afli repede când e liberă o sală.

Proiectul e făcut pentru ASMI. Datele vin din orarul public de pe
<https://fmi.unibuc.ro/orar/> și sunt citite automat, deci pot conține greșeli. Orarul
oficial rămâne cel al facultății.

## Ce poți face cu el

**Ca vizitator, fără cont**

- `/` e pagina principală: alegi nivelul, domeniul, anul și grupa, sau direct o sală. Tot
  aici sunt toate grupele (licență și master) și sălile, pe etaje.
- `/grupa/244` arată orarul unei grupe, cu tot cu cursurile pe care le face împreună cu
  seria și cu anul. Poți alege semigrupa, ce opționale și facultative vezi și data pentru
  care se calculează săptămâna. Alegerile se țin minte în browser.
- `/sala/amf-701` arată cât de ocupată e o sală: când, cu ce materie, cu ce profesor și cu
  ce grupă. `/sala` le listează pe toate, cu procentul de ocupare.
- `/profesor/popescu-stefan` arată orarul unui profesor.
- `/calendar-asmi` e calendarul public al asociației: evenimente, termene, înscrieri.
- Sub orice orar găsești butonul „Raportează o problemă”, dacă vezi ceva greșit.
- În colțul din dreapta jos e panoul „Orare”: de acolo treci la alt semestru sau la o
  versiune mai veche a orarului.

**Cu cont** (`/signup`, `/login`)

- Îți alegi grupa, iar „Orarul meu” te duce direct la orarul ei. Semigrupa și opționalele
  alese se salvează în cont, deci le găsești la fel de pe orice dispozitiv.
- Dacă ești profesor, ceri rolul din „Contul și grupa mea”. După ce un admin aprobă,
  „Orarul meu” devine orarul tău de profesor.

**Ca admin** (`/admin`)

- Tratezi sesizările primite de la vizitatori.
- Editezi orarul: adaugi o activitate sau schimbi orice la una existentă (materie, zi, ore,
  profesori, sală, pentru cine se ține). Ce schimbi rămâne și după ce se încarcă un orar nou.
- Rezolvi coada de verificare: activitățile pe care citirea automată nu le-a putut
  confirma apar lângă decupajul din PDF, iar tu corectezi și confirmi pe loc.
- Încarci un orar nou din linkurile publicate de facultate și alegi care orar e cel
  implicit.
- Adaugi evenimente cu dată (activități ASMI sau alte activități): spui ziua și orele, iar
  situl îți arată sălile libere atunci, de la cea mai încăpătoare. Merge și sâmbăta sau
  duminica.
- Vezi „Orarul în cifre”: câți oameni au ore în fiecare interval, câți sunt liberi și cât
  de pline sunt sălile, cu multe filtre pe care le poți salva cu nume.
- Dai roluri utilizatorilor: student, voluntar, profesor sau admin.

## Instalare și pornire

Ai nevoie de Python 3.12 sau mai nou.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"

.venv/bin/alembic upgrade head                            # creează baza de date
.venv/bin/python -m orar.cli load tests/golden/date.json  # o umple cu un orar de probă
.venv/bin/uvicorn orar.web.app:app --reload               # http://127.0.0.1:8000
```

Baza de date e un fișier SQLite, `data/orar.db`. O poți muta în altă parte cu
`ORAR_DB=/alt/loc.db`.

După ce actualizezi codul, rulează din nou `alembic upgrade head`, ca să se aplice
schimbările de schemă.

## Configurare

Setările stau în fișierul `.env`, care nu intră în git. Pornește de la exemplu:

```bash
cp .env.example .env
```

| Variabilă | La ce folosește |
| :--- | :--- |
| `ORAR_SECRET` | Cheia cu care se semnează sesiunile. Obligatorie în producție (`openssl rand -hex 32`). Fără ea se generează una la fiecare pornire și toată lumea e delogată la repornire. |
| `ORAR_ADMIN_USER` | Numele adminului principal. Intră pe `/login` cu el în câmpul de email. |
| `ORAR_ADMIN_PAROLA` | Parola lui. Poți pune în loc `ORAR_ADMIN_PAROLA_HASH`, ca parola să nu stea în clar. |
| `ORAR_HTTPS=1` | Pune-o când situl rulează în spatele HTTPS, ca sesiunea să meargă doar pe conexiuni sigure. |
| `ORAR_SCHEDULER=1` | Pornește verificarea zilnică a orarului în procesul web (vezi mai jos). |
| `ORAR_ORA_VERIFICARE` | Ora la care rulează verificarea zilnică. Implicit 4 dimineața. |
| `ORAR_DB` | Alt loc pentru baza de date. |

Adminul principal nu e un cont din baza de date: există doar cât timp numele și parola lui
sunt în mediu. Ceilalți admini sunt conturi obișnuite, ridicate la rolul de admin din panou
sau din linia de comandă:

```bash
.venv/bin/python -m orar.cli rol ana@example.com admin
```

Parolele conturilor se păstrează ca hash `scrypt`. Situl nu limitează numărul de încercări
de autentificare, așa că, dacă îl pui pe internet, adaugă o limitare în fața lui (nginx,
fail2ban).

## Cum ajunge orarul în bază

Pentru partea asta mai trebuie instalate uneltele de captură:

```bash
.venv/bin/python -m pip install -e ".[ingest]"
.venv/bin/playwright install chromium
```

Facultatea publică orarul ca PDF pe Google Drive. Drive nu lasă PDF-ul să fie descărcat,
așa că programul deschide previzualizarea într-un browser, face capturi ale paginilor, le
împarte în celule și citește textul cu OCR local. Nu se folosește niciun serviciu plătit.

Sunt două feluri de a încărca un orar.

**Din panoul de admin.** La „Încarcă un orar” pui linkul orarului grupelor și pe cel al
orarului profesorilor, alegi semestrul și anul. Încărcarea rulează în fundal, durează
10-20 de minute, iar panoul îți arată la ce pas a ajuns.

**Automat, din linia de comandă.**

```bash
.venv/bin/python -m orar.cli sincronizeaza --doar-verifica  # doar spune ce s-a schimbat
.venv/bin/python -m orar.cli sincronizeaza                  # și reîncarcă, dacă e cazul
.venv/bin/python -m orar.cli sincronizeaza --forteaza       # reîncarcă oricum
```

`sincronizeaza` citește pagina facultății și reîncarcă orarul doar dacă data publicată
acolo e mai nouă decât ultima încărcare. Verificarea în sine e o singură cerere și durează
o secundă.

Câteva lucruri de știut, valabile pentru ambele feluri:

- **Orarul profesorilor e obligatoriu.** El confirmă activitățile și dă numele întregi ale
  profesorilor. Dacă lipsește sau nu poate fi citit, nici orarul grupelor nu se înlocuiește
  și rămâne cel de dinainte.
- **Se înlocuiește doar orarul aceluiași semestru și an.** Celelalte orare rămân neatinse.
  Baza poate ține mai multe orare deodată, iar adminul alege care e cel implicit.
- **Orarul vechi nu se pierde.** Rămâne ca versiune anterioară, de văzut din panoul „Orare”.
- **Corecțiile făcute de mână se pun la loc** peste orarul nou. Cele care nu își mai găsesc
  activitatea apar ca „neaplicate” în `/admin/corectii`.
- **Totul sau nimic.** Dacă încărcarea pică pe drum, baza rămâne exact cum era.

### Verificarea zilnică

Orarul se schimbă de câteva ori pe semestru, deci o verificare pe zi e de ajuns. Nu
pornește singură. Ai două variante:

```bash
# din cron: recomandat, fiindcă e un singur proces, oricâți workeri are situl
15 4 * * *  cd /opt/orar && .venv/bin/python -m orar.cli sincronizeaza >> /var/log/orar.log 2>&1
```

```bash
# sau în procesul web, dar numai dacă rulezi un singur worker
ORAR_SCHEDULER=1 .venv/bin/uvicorn orar.web.app:app
```

Cu mai mulți workeri, fiecare și-ar porni propria verificare și ar captura în același
director. Din același motiv, și încărcarea din panoul de admin merge cu un singur worker.

### Alte comenzi

```bash
.venv/bin/python -m orar.cli surse          # ce orare cunoaștem și dacă sunt la zi
.venv/bin/python -m orar.cli stats          # câte rânduri sunt în bază
.venv/bin/python -m orar.cli reset          # șterge baza
.venv/bin/python -m orar.cli evalueaza      # acuratețea citirii, pe câmpuri
.venv/bin/python -m orar.cli planuri --descarca   # credite și formă de evaluare
.venv/bin/python -m orar.cli capacitati     # numărul de locuri al sălilor

# pașii încărcării, unul câte unul
.venv/bin/python -m orar.cli captureaza https://bit.ly/... -o data/screenshots/sem2-grupe
.venv/bin/python -m orar.cli load data/screenshots/sem2-grupe
.venv/bin/python -m orar.cli crosscheck data/screenshots/sem2-profesori --completeaza
.venv/bin/python -m orar.cli crosscheck data/screenshots/sem2-profesori --pastreaza 2025-2026 2
```

`crosscheck --pastreaza` salvează în bază ce s-a citit din orarul profesorilor: lista de
profesori din care se alege la editare și activitățile fiecăruia.

## Teste

```bash
.venv/bin/python -m pytest        # cam 470 de teste, în jur de două minute
.venv/bin/ruff check src tests
```

Testele nu ating internetul și nici baza reală: lucrează pe baze de date ținute în memorie.

## Cum funcționează, pe scurt

Câteva lucruri care nu sunt evidente și care explică de ce codul arată cum arată.

**Fiecare pagină din orarul facultății e de sine stătătoare.** Pagina grupei 244 conține și
cursurile ținute cu toată seria, deci același curs apare pe paginile a patru grupe. Încărcat
ca atare, un curs ar fi patru activități și sala ar părea ocupată de patru ori. După
încărcare, `ingest/consolidate.py` găsește activitățile identice și le urcă la seria sau la
anul care le are în comun: 1146 de rânduri devin 784. Un test verifică, pentru fiecare
grupă, că orarul ei e același înainte și după.

**Celulele nu se pot despărți după culoare.** Programul de orare umple unele celule cu două
tonuri tăiate în diagonală, iar chenarele de un pixel se amestecă la randare cu umplerile.
`ingest/segment.py` taie doar acolo unde găsește o linie întunecată continuă pe toată
lățimea: continuitatea deosebește un chenar de un rând de text.

**Din OCR folosim doar recunoașterea.** Detectorul de text al bibliotecii e antrenat pe
fotografii și rupe cuvintele din celulele astea. Textul e însă negru curat pe fundal
pastel, așa că rândurile și câmpurile se pot decupa geometric. Rezultatul e mai corect și
de vreo 25 de ori mai rapid.

**Vocabularul nu are voie să se hrănească din propriile greșeli.** Corecția OCR folosește
termenii din bază, iar baza e umplută tot de încărcare. Ca o citire greșită să nu devină
„cuvânt cunoscut”, `Lexicon.din_baza` ia doar termenii care apar măcar o dată într-un câmp
care nu e marcat nesigur.

**Orarul profesorilor e a doua sursă.** E același orar, dar cu profesorul în titlul paginii.
De acolo vin numele întregi (în orarul grupelor sunt prescurtate, de exemplu „Cheval H”
pentru „Cheval Andrei-Horatiu”) și o confirmare independentă a fiecărei activități. Regulile
de prescurtare sunt în `domain/names.py` și nu sunt cele la care te-ai aștepta. Când un curs
e împărțit pe săptămâni între doi profesori, fiecare parte își primește profesorul ei.

**Opționalele sunt pachete, nu grupe.** O pagină de opționale, de facultative sau de limbi
străine se adresează unei specializări și unui an, nu unei grupe anume. Legătura cu grupele
se face îngăduitor, după anul și specializările găsite în titlu, ca să meargă și pentru
specializări sau serii care apar de la un an la altul.

**Săptămânile sar peste vacanțe.** Facultatea publică din când în când ce săptămână e și
dacă e pară sau impară. `domain/weeks.py` ține toate aceste repere și spune când un răspuns
e doar aproximativ, în loc să numere simplu din șapte în șapte zile.

**Editările nu se fac direct în orar.** Încărcarea șterge și reîncarcă orele unui semestru,
deci o schimbare făcută direct ar dispărea la următorul orar publicat. De aceea fiecare
modificare se ține și separat (`db/corectii.py`) și se reaplică după fiecare încărcare. La
fel se păstrează și confirmările din coada de verificare.

**Evenimentele sunt altceva decât orele.** O oră se repetă săptămânal și se reîncarcă odată
cu orarul. Un eveniment are o zi anume, poate fi în weekend sau seara și rămâne până îl
șterge un admin. Pe orare apare doar în săptămâna lui, iar grila primește atunci rânduri de
sâmbătă și duminică.

**Statisticile lucrează cu mărimi presupuse.** Orarul nu spune câți studenți are o grupă,
deci „Orarul în cifre” pornește de la 15 oameni pe semigrupă și 30 pe grupă, valori pe care
le poți schimba. Un curs are atâția oameni câte grupe are seria lui.

Formatul orarului și geometria paginilor sunt descrise pe larg în
[`docs/formatul-orarului.md`](docs/formatul-orarului.md).

## Cât de bine citește

Măsurat cu `orar evalueaza` pe cele 98 de pagini ale orarului de referință (1140 de
activități):

| Câmp | Acuratețe |
| :--- | ---: |
| sală | 100,00% |
| frecvență | 99,91% |
| săptămâni | 99,82% |
| semigrupă | 99,82% |
| tip | 99,74% |
| materie | 99,56% |
| profesor | 99,56% |

Ce nu poate fi confirmat nu intră pe tăcute în orar: ajunge în coada de verificare, lângă
decupajul din PDF. Câte activități ajung acolo depinde de orar. Pe orarul de referință au
fost 5 din 784, dar un orar nou, cu materii și profesori pe care baza nu îi cunoaște încă,
poate avea câteva sute până sunt confirmate.

## Unde e fiecare lucru

| Director | Ce conține |
| :--- | :--- |
| `src/orar/domain/` | Logica pură, fără bază de date: ierarhia grupelor, numele profesorilor, sălile, săptămânile, așezarea în grilă. |
| `src/orar/db/` | Modelele, interogările și migrările. Tot aici: versiunile orarului, corecțiile, evenimentele, statisticile. |
| `src/orar/ingest/` | Drumul de la PDF la bază: captură, segmentare, OCR, vocabular, verificarea cu orarul profesorilor, consolidare. |
| `src/orar/worker/` | Sincronizarea cu pagina facultății, verificarea zilnică și încărcarea pornită din panou. |
| `src/orar/web/` | Situl: FastAPI, șabloane Jinja2, HTMX, CSS scris de mână. |
| `tests/` | Testele și orarul de referință (`tests/golden/date.json`). |
| `docs/` | Descrierea formatului orarului și a planurilor de învățământ. |

## Ce mai e de făcut

- Drepturile pe roluri: voluntarul și profesorul nu pot face deocamdată mai mult decât un
  student, în afară de orarul propriu al profesorului.
- Creditele și forma de evaluare vin din planurile de învățământ, iar acum sunt completate
  doar pentru o parte din materii. Restul planurilor au cifrele desenate, nu scrise ca text
  (vezi [`docs/planuri-de-invatamant.md`](docs/planuri-de-invatamant.md)).
- Profesorii nu apar încă în căutare.
- Nimeni nu e anunțat când apare o sesizare sau o cerere de rol; adminul le vede când intră
  în panou.

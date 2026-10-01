# Orarul Salilor, intr-un singur container: situl, plus tot ce trebuie ca un admin sa poata
# incarca un orar nou din panou (browserul pentru captura si OCR-ul local).
FROM python:3.12-slim

WORKDIR /app

# Dependintele se instaleaza inaintea codului, pe un pachet gol: asa o schimbare de cod nu
# le mai reinstaleaza (browserul si OCR-ul dureaza cateva minute). `-e`: sabloanele si
# fisierele statice se servesc direct din src/, iar baza de date sta in /app/data, langa cod
# -- exact ca la rularea fara Docker.
COPY pyproject.toml ./
RUN mkdir -p src/orar && touch src/orar/__init__.py README.md \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && pip install --no-cache-dir -e ".[ingest]" \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY src ./src
COPY alembic.ini ./
# orarul de proba, pentru prima pornire (vezi README)
COPY tests/golden ./tests/golden

# Un port ales dinadins neobisnuit, ca sa nu se calce cu alte servicii de pe masina.
EXPOSE 8347

# Aduce schema la zi, apoi porneste situl. Un singur worker: incarcarea unui orar din panou
# si verificarea zilnica isi tin starea in memoria procesului.
# `--proxy-headers`: in spatele unui proxy (nginx, Caddy, Cloudflare), schema si adresa
# clientului se iau din antetele X-Forwarded-*.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn orar.web.app:app --host 0.0.0.0 --port 8347 --proxy-headers --forwarded-allow-ips='*'"]

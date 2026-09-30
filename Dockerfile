# Orarul Salilor, intr-un singur container: situl, plus tot ce trebuie ca un admin sa poata
# incarca un orar nou din panou (browserul pentru captura si OCR-ul local).
FROM python:3.12-slim

WORKDIR /app

# Dependintele se instaleaza inaintea codului, ca o schimbare de cod sa nu le reinstaleze.
# `-e`: sabloanele si fisierele statice se servesc direct din src/, iar baza de date sta in
# /app/data, langa cod -- exact ca la rularea fara Docker.
COPY pyproject.toml README.md ./
COPY src ./src
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && pip install --no-cache-dir -e ".[ingest]" \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY alembic.ini ./
# orarul de proba, pentru prima pornire (vezi README)
COPY tests/golden ./tests/golden

# Un port ales dinadins neobisnuit, ca sa nu se calce cu alte servicii de pe masina.
EXPOSE 8347

# Aduce schema la zi, apoi porneste situl. Un singur worker: incarcarea unui orar din panou
# si verificarea zilnica isi tin starea in memoria procesului.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn orar.web.app:app --host 0.0.0.0 --port 8347"]

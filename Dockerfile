# Usa un'immagine base di Python che è compatibile con le tue librerie
FROM python:3.12-slim-bookworm

# Imposta la directory di lavoro nel container
WORKDIR /app

# Installa le dipendenze di sistema necessarie per compilare alcuni pacchetti Python
# 'build-essential' include gcc, g++ e make
# 'python3-dev' include gli header e le librerie di sviluppo di Python
# Rimuoviamo le liste dei pacchetti apt per mantenere l'immagine piccola
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Copia il file dei requisiti e installa le dipendenze
# Aggiorna pip e setuptools per evitare problemi di compilazione
RUN pip install --no-cache-dir --upgrade pip setuptools

# Copia il file requirements.txt e installa le dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia tutti gli altri file del tuo progetto nella directory di lavoro del container.
COPY . .

# Espone la porta su cui il tuo bot ascolterà le richieste.
ENV PORT 8080

# Comando per avviare il tuo bot
CMD ["python", "quiz_bot.py"]

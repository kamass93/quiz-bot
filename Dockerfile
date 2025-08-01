# Usa un'immagine base Python
FROM python:3.9-slim-buster

# Imposta la directory di lavoro nel container
WORKDIR /app

# Copia il file requirements.txt e installa le dipendenze
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copia il tuo codice bot
COPY . .

# Comando per eseguire l'applicazione Flask
# Cloud Run si aspetta che tu avvii un server web che ascolti sulla PORT
# Se il tuo file Python si chiama `main.py`, allora:
CMD ["python", "main.py"]

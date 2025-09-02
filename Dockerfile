FROM python:3.10-slim

# Sistem bağımlılıkları (psycopg2 için)
RUN apt-get update && apt-get install -y gcc libpq-dev

WORKDIR /app

# Gereksinimler dosyasını kopyala ve yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Proje dosyalarını kopyala
COPY . .

# Uygulama başlatma komutu
CMD ["python", "uygulama.py"]

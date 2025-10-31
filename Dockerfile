FROM python:3.9-slim-bullseye

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    libsqlite3-0 \
    libsqlite3-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY . .

# Запускаем бота
CMD ["python", "main.py"]

FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

CMD ["python", "-u", "bot.py"]

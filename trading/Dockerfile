FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY trading_bot.py .

# Cles/API a fournir au lancement, ex:
# docker run -e APCA_API_KEY_ID=... -e APCA_API_SECRET_KEY=... \
#            -e TELEGRAM_BOT_TOKEN=... -e TELEGRAM_CHAT_ID=... trading-bot
# ENV APCA_API_KEY_ID=""
# ENV APCA_API_SECRET_KEY=""
# ENV TELEGRAM_BOT_TOKEN=""
# ENV TELEGRAM_CHAT_ID=""

CMD ["python", "trading_bot.py"]

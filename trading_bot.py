"""
Bot de trading algorithmique pour actions US via Alpaca (paper trading par defaut).

Strategie : croisement de moyennes mobiles (SMA courte / SMA longue).
- Achat quand la SMA courte croise au-dessus de la SMA longue.
- Vente quand la SMA courte croise en dessous.

AVERTISSEMENT : ceci est un point de depart educatif, pas un conseil financier.
Le trading algorithmique comporte un risque reel de perte en capital, meme
avec une strategie qui a l'air solide en backtest. Teste toujours en mode
paper trading (argent fictif) pendant plusieurs semaines avant d'envisager
de passer en reel, et n'engage jamais plus que ce que tu peux perdre.

Prerequis :
  1. pip install alpaca-py pandas requests --break-system-packages
  2. Cree un compte gratuit sur https://alpaca.markets
  3. Dans le dashboard, active le mode "Paper Trading" et genere une cle API
  4. Mets ta cle et ton secret dans les variables d'environnement :
       export APCA_API_KEY_ID="ta_cle"
       export APCA_API_SECRET_KEY="ton_secret"
  5. (Optionnel) Pour recevoir une notification Telegram a chaque ordre reussi :
       - Cree un bot via @BotFather sur Telegram, recupere le token
       - Recupere ton chat_id (parle au bot puis va sur
         https://api.telegram.org/bot<TOKEN>/getUpdates)
       - export TELEGRAM_BOT_TOKEN="ton_token"
       - export TELEGRAM_CHAT_ID="ton_chat_id"
"""

import os
import time
from datetime import datetime, timedelta

import requests
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

# --- Configuration ---------------------------------------------------------

API_KEY = os.getenv("APCA_API_KEY_ID", "TA_CLE_API")
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "TON_SECRET_API")
PAPER = True  # laisse True tant que la strategie n'est pas validee sur plusieurs semaines

SYMBOLS = ["AAPL", "MSFT", "NVDA"]  # actions a surveiller
SHORT_WINDOW = 20   # SMA courte (jours)
LONG_WINDOW = 50    # SMA longue (jours)
QTY_PER_ORDER = 1   # nombre d'actions par ordre
CHECK_INTERVAL_SECONDS = 60 * 60  # verifie une fois par heure

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Clients ---------------------------------------------------------------

trading_client = TradingClient(API_KEY, API_SECRET, paper=PAPER)
data_client = StockHistoricalDataClient(API_KEY, API_SECRET)


def send_telegram_message(text: str):
    """Envoie un message Telegram si le bot est configure. Ne bloque jamais le bot."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print(f"[{datetime.now()}] Echec envoi Telegram: {e}")


def get_sma_signal(symbol: str) -> str:
    """Retourne 'buy', 'sell' ou 'hold' selon le croisement de moyennes mobiles."""
    end = datetime.now()
    start = end - timedelta(days=LONG_WINDOW * 3)  # marge pour jours feries/week-ends

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
    )
    bars = data_client.get_stock_bars(request).df

    if bars.empty or len(bars) < LONG_WINDOW:
        return "hold"

    closes = bars["close"]
    sma_short = closes.rolling(SHORT_WINDOW).mean()
    sma_long = closes.rolling(LONG_WINDOW).mean()

    prev_diff = sma_short.iloc[-2] - sma_long.iloc[-2]
    curr_diff = sma_short.iloc[-1] - sma_long.iloc[-1]

    if prev_diff <= 0 and curr_diff > 0:
        return "buy"
    if prev_diff >= 0 and curr_diff < 0:
        return "sell"
    return "hold"


def has_open_position(symbol: str) -> bool:
    try:
        trading_client.get_open_position(symbol)
        return True
    except Exception:
        return False


def place_order(symbol: str, side: OrderSide):
    order = MarketOrderRequest(
        symbol=symbol,
        qty=QTY_PER_ORDER,
        side=side,
        time_in_force=TimeInForce.DAY,
    )
    try:
        trading_client.submit_order(order)
    except Exception as e:
        print(f"[{datetime.now()}] Echec ordre {side.value} pour {symbol}: {e}")
        return

    message = f"Ordre {side.value.upper()} execute pour {QTY_PER_ORDER} {symbol}"
    print(f"[{datetime.now()}] {message}")
    send_telegram_message(f"OK - {message}")


def run_once():
    clock = trading_client.get_clock()
    if not clock.is_open:
        print(f"[{datetime.now()}] Marche ferme, on attend.")
        return

    for symbol in SYMBOLS:
        signal = get_sma_signal(symbol)
        owns_it = has_open_position(symbol)

        if signal == "buy" and not owns_it:
            place_order(symbol, OrderSide.BUY)
        elif signal == "sell" and owns_it:
            place_order(symbol, OrderSide.SELL)
        else:
            print(f"[{datetime.now()}] {symbol}: signal={signal}, position={owns_it}, rien a faire")


def run_forever():
    send_telegram_message(f"Bot demarre - symboles surveilles: {', '.join(SYMBOLS)}")
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"Erreur: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()

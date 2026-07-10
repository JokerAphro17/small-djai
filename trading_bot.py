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
  5. (Optionnel) Pour recevoir les notifications Telegram et utiliser /solde, /historique :
       - Cree un bot via @BotFather sur Telegram, recupere le token
       - Recupere ton chat_id (parle au bot puis va sur
         https://api.telegram.org/bot<TOKEN>/getUpdates)
       - export TELEGRAM_BOT_TOKEN="ton_token"
       - export TELEGRAM_CHAT_ID="ton_chat_id"

Notifications Telegram envoyees :
  - Au demarrage du bot
  - A chaque ordre execute (achat/vente)
  - A chaque ouverture/fermeture du marche
  - Bilan quotidien a 00h00 UTC (P&L du jour + P&L total depuis le debut)

Commandes Telegram disponibles (repondre au bot) :
  - /solde       -> equite, cash, buying power actuels
  - /historique  -> 10 derniers ordres executes
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce

# --- Configuration ---------------------------------------------------------

API_KEY = os.getenv("APCA_API_KEY_ID", "TA_CLE_API")
API_SECRET = os.getenv("APCA_API_SECRET_KEY", "TON_SECRET_API")
PAPER = True  # laisse True tant que la strategie n'est pas validee sur plusieurs semaines

SYMBOLS = ["AAPL", "MSFT", "NVDA"]  # actions a surveiller
SHORT_WINDOW = 20   # SMA courte (jours)
LONG_WINDOW = 50    # SMA longue (jours)
QTY_PER_ORDER = 1   # nombre d'actions par ordre
CHECK_INTERVAL_SECONDS = 60 * 60  # verifie une fois par heure (strategie SMA)
MARKET_CHECK_INTERVAL_SECONDS = 60  # verifie l'etat du marche (ouvert/ferme) chaque minute

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STATE_FILE = Path(os.getenv("STATE_FILE", "data/state.json"))

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


# --- Etat persistant --------------------------------------------------------
# Retient l'equite initiale (pour le bilan total), l'etat du marche (pour
# detecter les transitions ouverture/fermeture) et l'offset Telegram (pour ne
# pas retraiter les vieux messages) a travers les redemarrages du conteneur.

_state_lock = threading.Lock()


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"initial_equity": None, "market_open": None, "last_summary_date": None, "telegram_offset": 0}


def save_state(state: dict):
    with _state_lock:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state))


def get_account():
    return trading_client.get_account()


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


def market_watcher(state: dict):
    """Envoie une notif Telegram a chaque ouverture/fermeture du marche."""
    while True:
        try:
            is_open = trading_client.get_clock().is_open
            if state["market_open"] is None:
                # premier passage: on memorise l'etat sans notifier
                state["market_open"] = is_open
                save_state(state)
            elif is_open != state["market_open"]:
                state["market_open"] = is_open
                save_state(state)
                if is_open:
                    send_telegram_message("🟢 Marche ouvert")
                else:
                    send_telegram_message("🔴 Marche ferme")
        except Exception as e:
            print(f"[{datetime.now()}] Erreur market_watcher: {e}")
        time.sleep(MARKET_CHECK_INTERVAL_SECONDS)


def send_daily_summary(state: dict):
    account = get_account()
    equity = float(account.equity)
    last_equity = float(account.last_equity)
    daily_pl = equity - last_equity

    if state["initial_equity"] is None:
        state["initial_equity"] = equity
        save_state(state)
    total_pl = equity - state["initial_equity"]

    message = (
        "📊 Bilan quotidien\n"
        f"Equite actuelle : {equity:.2f} $\n"
        f"P&L du jour : {daily_pl:+.2f} $\n"
        f"P&L depuis le debut : {total_pl:+.2f} $"
    )
    send_telegram_message(message)


def daily_summary_watcher(state: dict):
    """Envoie chaque jour a 00h00 UTC le bilan journalier + le bilan total."""
    while True:
        now = datetime.now(timezone.utc)
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        time.sleep((next_midnight - now).total_seconds())
        try:
            send_daily_summary(state)
            state["last_summary_date"] = next_midnight.date().isoformat()
            save_state(state)
        except Exception as e:
            print(f"[{datetime.now()}] Erreur daily_summary_watcher: {e}")


def format_solde_message() -> str:
    account = get_account()
    return (
        "💰 Solde\n"
        f"Equite : {float(account.equity):.2f} $\n"
        f"Cash : {float(account.cash):.2f} $\n"
        f"Buying power : {float(account.buying_power):.2f} $"
    )


def format_historique_message() -> str:
    req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=10, nested=False)
    orders = trading_client.get_orders(req)
    if not orders:
        return "📜 Historique : aucun ordre pour l'instant."

    lines = ["📜 10 derniers ordres :"]
    for o in orders:
        filled_price = f"{float(o.filled_avg_price):.2f} $" if o.filled_avg_price else "n/a"
        filled_at = o.filled_at.strftime("%Y-%m-%d %H:%M") if o.filled_at else "non rempli"
        lines.append(f"{o.side.value.upper()} {o.qty} {o.symbol} @ {filled_price} ({filled_at})")
    return "\n".join(lines)


def telegram_command_listener(state: dict):
    """Long-polling Telegram : repond a /solde et /historique. Ignore tout
    message qui ne vient pas du TELEGRAM_CHAT_ID autorise."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    while True:
        try:
            resp = requests.get(
                url,
                params={"offset": state["telegram_offset"], "timeout": 30},
                timeout=35,
            )
            updates = resp.json().get("result", [])
            for update in updates:
                state["telegram_offset"] = update["update_id"] + 1
                message = update.get("message") or {}
                chat_id = str(message.get("chat", {}).get("id", ""))
                text = (message.get("text") or "").strip().lower()

                if chat_id != str(TELEGRAM_CHAT_ID):
                    continue

                if text == "/solde":
                    send_telegram_message(format_solde_message())
                elif text == "/historique":
                    send_telegram_message(format_historique_message())
                elif text == "/help" or text == "/start":
                    send_telegram_message("Commandes disponibles : /solde, /historique")
            save_state(state)
        except Exception as e:
            print(f"[{datetime.now()}] Erreur telegram_command_listener: {e}")
            time.sleep(5)


def run_forever():
    state = load_state()
    if state["initial_equity"] is None:
        try:
            state["initial_equity"] = float(get_account().equity)
        except Exception as e:
            print(f"[{datetime.now()}] Impossible de recuperer l'equite initiale: {e}")
    save_state(state)

    send_telegram_message(f"Bot demarre - symboles surveilles: {', '.join(SYMBOLS)}")

    threading.Thread(target=market_watcher, args=(state,), daemon=True).start()
    threading.Thread(target=daily_summary_watcher, args=(state,), daemon=True).start()
    threading.Thread(target=telegram_command_listener, args=(state,), daemon=True).start()

    while True:
        try:
            run_once()
        except Exception as e:
            print(f"Erreur: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()

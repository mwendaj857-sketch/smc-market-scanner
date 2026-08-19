import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
LSE_API_KEY = os.environ.get("LSE_API_KEY", "")
NEWS_RSS_URLS = os.environ.get("NEWS_RSS_URLS", "")
SCAN_RUNTIME_MINUTES = os.environ.get("SCAN_RUNTIME_MINUTES", "8")
TOP_N = os.environ.get("TOP_N", "5")
MAX_INSTRUMENTS = os.environ.get("MAX_INSTRUMENTS", "0")
LSE_CATEGORIES = os.environ.get("LSE_CATEGORIES", "forex,commodity,crypto")
AUTO_SCAN_MINUTES = max(0, int(os.environ.get("AUTO_SCAN_MINUTES", "15")))

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
BASE_DIR = Path(__file__).resolve().parent
SCANNER = BASE_DIR / "scanner_engine_telegram.py"
LOCK = threading.Lock()
LAST_RESULT = None


def telegram(method, data=None, files=None, timeout=60):
    response = requests.post(
        f"{API}/{method}",
        json=data or {} if files is None else None,
        files=files,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error: {payload}")
    return payload


def send_message(chat_id, text):
    # Telegram text messages are limited to 4096 characters.
    for start in range(0, len(text), 3900):
        telegram("sendMessage", {"chat_id": chat_id, "text": text[start:start + 3900]})


def send_photo(chat_id, image_path, caption=""):
    with open(image_path, "rb") as image:
        telegram(
            "sendPhoto",
            data={"chat_id": str(chat_id), "caption": caption[:1024]},
            files={"photo": image},
            timeout=60,
        )


def run_scanner():
    global LAST_RESULT
    if not LOCK.acquire(blocking=False):
        return None, "A scan is already running."

    try:
        with tempfile.TemporaryDirectory(prefix="lse_scanner_") as tmp:
            result_path = Path(tmp) / "scan_results.json"
            env = os.environ.copy()
            env.update(
                {
                    "LSE_API_KEY": LSE_API_KEY,
                    "NEWS_RSS_URLS": NEWS_RSS_URLS,
                    "SCAN_RUNTIME_MINUTES": SCAN_RUNTIME_MINUTES,
                    "TOP_N": TOP_N,
                    "MAX_INSTRUMENTS": MAX_INSTRUMENTS,
                    "LSE_CATEGORIES": LSE_CATEGORIES,
                    "SCANNER_JSON": str(result_path),
                    "PYTHONUNBUFFERED": "1",
                }
            )

            completed = subprocess.run(
                [sys.executable, str(SCANNER)],
                cwd=str(BASE_DIR),
                env=env,
                capture_output=True,
                text=True,
                timeout=max(180, int(float(SCAN_RUNTIME_MINUTES) * 60) + 120),
            )

            if completed.returncode != 0:
                tail = (completed.stdout + "\n" + completed.stderr)[-3500:]
                return None, f"Scanner failed:\n{tail}"

            if not result_path.exists():
                return None, "Scanner finished but produced no JSON handoff."

            data = json.loads(result_path.read_text(encoding="utf-8"))
            LAST_RESULT = data
            return data, None
    except subprocess.TimeoutExpired:
        return None, "Scanner timed out before producing a complete result."
    except Exception as exc:
        return None, f"Scanner error: {exc}"
    finally:
        LOCK.release()


def fmt_entry(r):
    levels = r.get("levels") or []
    rr = r.get("rr")
    rr_text = f"{float(rr):.2f}R" if rr is not None else "N/A"

    level_text = "Levels unavailable"
    if len(levels) >= 3:
        try:
            level_text = (
                f"Entry {float(levels[0]):.6g} | "
                f"SL {float(levels[1]):.6g} | "
                f"TP {float(levels[2]):.6g}"
            )
        except (TypeError, ValueError):
            pass

    consensus = r.get("strategy_consensus", {})
    aligned = ", ".join(consensus.get("aligned", [])) or "None"
    opposed = ", ".join(consensus.get("opposed", [])) or "None"

    return (
        f"📌 {r.get('symbol', '?')} — {r.get('side', 'NEUTRAL')}\n"
        f"Type: {r.get('trade_type', 'WAIT')}\n"
        f"Score: {float(r.get('score', 0)):.1f}/100 | "
        f"Risk score: {float(r.get('risk_score', 0)):.1f}/100 | R:R: {rr_text}\n"
        f"{level_text}\n"
        f"Regime: {r.get('regime', 'NEUTRAL')}\n"
        f"9-strategy agreement: {aligned}\n"
        f"Opposed: {opposed}\n"
        f"Timeframes: {', '.join(consensus.get('timeframes', [])) or 'None'}\n"
        f"Event/news risk: {r.get('event_level', 'UNKNOWN')}\n"
        f"Status: {r.get('status', 'WAIT')}"
    )


def make_chart(entry):
    # Charts are generated from the scanner's 30M LSE market data.
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except Exception:
        return None

    rows = entry.get("chart_30m") or []
    if len(rows) < 5:
        return None

    rows = rows[-80:]
    opens, highs, lows, closes = [], [], [], []
    labels = []

    for row in rows:
        try:
            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
        labels.append(str(row.get("timestamp", ""))[:16])

    if len(closes) < 5:
        return None

    fig, ax = plt.subplots(figsize=(10, 5.5))
    width = 0.62

    for i, (o, h, l, c) in enumerate(zip(opens, highs, lows, closes)):
        ax.vlines(i, l, h, linewidth=1)
        lower = min(o, c)
        height = max(abs(c - o), 1e-12)
        ax.add_patch(
            Rectangle(
                (i - width / 2, lower),
                width,
                height,
                fill=False,
                linewidth=1,
            )
        )

    ax.plot(range(len(closes)), closes, linewidth=1, label="Close")

    levels = entry.get("levels") or []
    names = ("Entry", "SL", "TP")
    for idx, name in enumerate(names):
        if idx < len(levels):
            try:
                value = float(levels[idx])
                ax.axhline(value, linestyle="--", linewidth=1, label=name)
            except (TypeError, ValueError):
                pass

    ax.set_title(
        f"{entry.get('symbol', '?')} | 30M LSE price analysis | "
        f"{entry.get('side', 'NEUTRAL')} {entry.get('trade_type', 'WAIT')}"
    )
    ax.set_xlabel("30M candles")
    ax.set_ylabel("Price")
    ax.grid(alpha=0.2)
    ax.legend(loc="best")
    fig.tight_layout()

    path = Path(tempfile.mkstemp(prefix="lse_chart_", suffix=".png")[1])
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def send_scan(chat_id, data):
    entries = data.get("entries", [])
    waiting = data.get("setup_waiting", [])
    potential = data.get("potential", [])

    header = (
        "🟢 LSE SMART SCANNER\n\n"
        "9-strategy independent consensus\n"
        "SMC is ONE strategy — NOT a required dependency.\n"
        "Qualification: ≥2 distinct strategies OR ≥3 independent "
        "price-movement methods + real ≥2R + no HIGH event risk.\n\n"
        f"Analyzed: {data.get('complete', 0)} | "
        f"Incomplete: {data.get('incomplete', 0)} | "
        f"Errors: {data.get('errors', 0)}\n"
        f"Confirmed entries: {len(data.get('entries', []))}\n"
    )
    send_message(chat_id, header)

    if entries:
        for entry in entries[: int(TOP_N)]:
            send_message(chat_id, fmt_entry(entry))
            chart = make_chart(entry)
            if chart:
                try:
                    send_photo(
                        chat_id,
                        chart,
                        f"📊 {entry.get('symbol')} 30M LSE analysis — "
                        f"{entry.get('side')} / {entry.get('trade_type')}",
                    )
                finally:
                    try:
                        chart.unlink(missing_ok=True)
                    except OSError:
                        pass
    else:
        send_message(
            chat_id,
            "⛔ NO CONFIRMED ENTRY\n\n"
            "The scanner found no setup that passed all consensus, "
            "risk/reward and event-risk gates."
        )

    if waiting:
        text = "🟡 VALID SETUPS — WAIT\n\n"
        text += "\n\n".join(fmt_entry(x) for x in waiting[: int(TOP_N)])
        send_message(chat_id, text)

    if potential:
        text = "👀 POTENTIAL — NOT CONFIRMED\n\n"
        text += "\n\n".join(fmt_entry(x) for x in potential[: int(TOP_N)])
        send_message(chat_id, text)


def process_command(chat_id, text):
    if text == "/start":
        send_message(
            chat_id,
            "🟢 LSE Smart Scanner Telegram Bot is online.\n\n"
            "Commands:\n"
            "/scan — run a fresh LSE scan\n"
            "/status — show the latest scan status\n"
            "/help — show commands\n\n"
            "Automatic trading is DISABLED.",
        )
        return

    if text == "/help":
        send_message(
            chat_id,
            "/scan — fresh scan\n"
            "/status — latest scan information\n"
            "/help — commands\n\n"
            "The bot sends 30M LSE analysis charts for confirmed entries "
            "when chart data is available.",
        )
        return

    if text == "/status":
        if not LAST_RESULT:
            send_message(chat_id, "No scan has completed yet. Use /scan.")
            return
        send_message(
            chat_id,
            "📡 LAST SCAN\n"
            f"Analyzed: {LAST_RESULT.get('complete', 0)}\n"
            f"Incomplete: {LAST_RESULT.get('incomplete', 0)}\n"
            f"Errors: {LAST_RESULT.get('errors', 0)}\n"
            f"Confirmed: {len(LAST_RESULT.get('entries', []))}\n"
            f"Runtime: {float(LAST_RESULT.get('runtime_seconds', 0)):.1f}s",
        )
        return

    if text == "/scan":
        send_message(chat_id, "🔄 Starting fresh LSE market scan...")
        data, error = run_scanner()
        if error:
            send_message(chat_id, f"❌ {error}")
        else:
            send_scan(chat_id, data)
        return

    send_message(chat_id, "Unknown command. Use /help.")


def poll():
    offset = None
    print("LSE Telegram scanner bot starting...")

    while True:
        try:
            params = {"timeout": 20}
            if offset is not None:
                params["offset"] = offset

            result = telegram("getUpdates", params)
            for update in result.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue

                chat_id = message["chat"]["id"]
                text = message.get("text", "").strip()
                print(f"Message received: {text}")

                if text.startswith("/"):
                    process_command(chat_id, text.split()[0])

        except Exception as exc:
            print(f"Polling error: {exc}", flush=True)
            time.sleep(10)


def automatic_scan_loop():
    if AUTO_SCAN_MINUTES <= 0:
        return

    while True:
        time.sleep(AUTO_SCAN_MINUTES * 60)
        data, error = run_scanner()
        if error:
            print(error, flush=True)
            continue

        # Automatic scans only notify chats that have interacted with the bot.
        # Chat IDs are persisted in memory for the current process.
        for chat_id in list(CHAT_IDS):
            try:
                send_scan(chat_id, data)
            except Exception as exc:
                print(f"Automatic Telegram delivery error: {exc}", flush=True)


CHAT_IDS = set()


def main():
    threading.Thread(target=automatic_scan_loop, daemon=True).start()

    # Wrap command handling so automatic scans know active chats.
    original = process_command

    def tracked_command(chat_id, text):
        CHAT_IDS.add(chat_id)
        original(chat_id, text)

    globals()["process_command"] = tracked_command
    poll()


if __name__ == "__main__":
    main()

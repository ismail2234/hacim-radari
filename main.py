import os
import time
import sqlite3
import logging
from dataclasses import dataclass
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask


# ============================================================
# 🐋 BALİNA RADARI V7 — ULTRA CONFLUENCE ENGINE (LONG & SHORT)
# ============================================================

@dataclass(frozen=True)
class Config:
    min_volume: float = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
    scan_interval: int = int(os.getenv("SCAN_INTERVAL", "300"))
    workers: int = int(os.getenv("MAX_WORKERS", "8"))
    ultra_score_threshold: int = int(os.getenv("ULTRA_SCORE_THRESHOLD", "85"))
    max_signals: int = int(os.getenv("MAX_SIGNALS_PER_SCAN", "3"))
    cooldown: int = int(os.getenv("SIGNAL_COOLDOWN", "7200"))
    timeout: int = int(os.getenv("REQUEST_TIMEOUT", "10"))
    db: str = os.getenv("STATE_DB_PATH", "balina_v7.db")


CFG = Config()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

SPOT = "https://api.binance.com"
FUT = "https://fapi.binance.com"

EXCLUDED = {"BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT"}


# ============================================================
# LOGGING & SESSION
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("balina-v7")


def session() -> requests.Session:
    s = requests.Session()
    retry_kwargs = dict(
        total=3, connect=3, read=3, backoff_factor=0.7,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    try:
        r = Retry(allowed_methods=["GET", "POST"], **retry_kwargs)
    except TypeError:
        r = Retry(method_whitelist=["GET", "POST"], **retry_kwargs)
    a = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=r)
    s.mount("https://", a)
    s.mount("http://", a)
    s.headers.update({"User-Agent": "BalinaRadari-V7-Ultra/1.0"})
    return s


S = session()


# ============================================================
# FLASK SERVER
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V7 Ultra Aktif!"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V7 Ultra",
        "threshold": CFG.ultra_score_threshold,
    }


# ============================================================
# API HELPERS
# ============================================================

def api(base: str, path: str, params: Optional[dict] = None) -> Any:
    resp = S.get(base + path, params=params, timeout=CFG.timeout)
    resp.raise_for_status()
    return resp.json()


def telegram(text: str) -> bool:
    if not TOKEN or not CHAT:
        return False
    try:
        resp = S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT, "text": text},
            timeout=CFG.timeout,
        )
        resp.raise_for_status()
        return bool(resp.json().get("ok"))
    except Exception as e:
        log.error("Telegram hatası: %s", e)
        return False


def tickers(base: str) -> List[dict]:
    try:
        return api(base, "/api/v3/ticker/24hr" if base == SPOT else "/fapi/v1/ticker/24hr")
    except Exception as e:
        log.error("Ticker çekme hatası: %s", e)
        return []


def klines(base: str, symbol: str, interval: str, limit: int = 220) -> List[list]:
    try:
        return api(base, "/api/v3/klines" if base == SPOT else "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    except Exception as e:
        log.debug("%s %s kline hatası: %s", symbol, interval, e)
        return []


def open_interest(symbol: str) -> Optional[float]:
    try:
        return float(api(FUT, "/fapi/v1/openInterest", {"symbol": symbol})["openInterest"])
    except:
        return None


def funding(symbol: str) -> float:
    try:
        return float(api(FUT, "/fapi/v1/premiumIndex", {"symbol": symbol})["lastFundingRate"])
    except:
        return 0.0


# ============================================================
# MATHEMATICAL & TECHNICAL ANALYSIS MODULES
# ============================================================

def calculate_ema(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    ema_list = [sum(values[:period]) / period]
    for val in values[period:]:
        ema_list.append((val - ema_list[-1]) * multiplier + ema_list[-1])
    return ema_list


def calculate_macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(closes) < slow + signal + 5:
        return None, None, None
    ema_fast = calculate_ema(closes, fast)
    ema_slow = calculate_ema(closes, slow)
    
    min_len = min(len(ema_fast), len(ema_slow))
    macd_line = [f - s for f, s in zip(ema_fast[-min_len:], ema_slow[-min_len:])]
    signal_line = calculate_ema(macd_line, signal)
    
    if not signal_line:
        return None, None, None
        
    histogram = macd_line[-1] - signal_line[-1]
    return macd_line[-1], signal_line[-1], histogram


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = ((period - 1) * avg_gain + gains[i]) / period
        avg_loss = ((period - 1) * avg_loss + losses[i]) / period
    return 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))


def check_structure_breakout(closes: List[float], highs: List[float], lows: List[float], price: float) -> Tuple[str, float, float]:
    """Son 50 mumun Destek / Direnç seviyelerini ve Kırılımlarını bulur."""
    lookback = 50
    if len(closes) < lookback:
        return "RANGE", 0.0, 0.0

    recent_highs = highs[-lookback:-2]
    recent_lows = lows[-lookback:-2]

    resistance = max(recent_highs)
    support = min(recent_lows)

    if price > resistance:
        return "BULLISH_BREAKOUT", support, resistance
    elif price < support:
        return "BEARISH_BREAKDOWN", support, resistance
    return "RANGE", support, resistance


def pct(a: float, b: float) -> float:
    return ((b - a) / a * 100) if a > 0 else 0.0


# ============================================================
# DATABASE
# ============================================================

class DB:
    def __init__(self, p: str):
        self.p = p
        self.lock = Lock()
        with self.lock, sqlite3.connect(p) as c:
            c.execute("CREATE TABLE IF NOT EXISTS state(symbol TEXT PRIMARY KEY, sent REAL, score REAL)")
            c.execute("CREATE TABLE IF NOT EXISTS oi(symbol TEXT PRIMARY KEY, value REAL, ts REAL)")

    def getoi(self, s: str) -> Optional[float]:
        with self.lock, sqlite3.connect(self.p) as c:
            row = c.execute("SELECT value, ts FROM oi WHERE symbol=?", (s,)).fetchone()
        if not row:
            return None
        val, ts = row
        if (time.time() - ts) > (CFG.scan_interval * 3):
            return None
        return float(val)

    def putoi(self, s: str, v: Optional[float]):
        if v is None:
            return
        with self.lock, sqlite3.connect(self.p) as c:
            c.execute(
                "INSERT INTO oi VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET value=excluded.value, ts=excluded.ts",
                (s, v, time.time()),
            )

    def cooldown(self, s: str) -> bool:
        with self.lock, sqlite3.connect(self.p) as c:
            x = c.execute("SELECT sent FROM state WHERE symbol=?", (s,)).fetchone()
        return bool(x and time.time() - x[0] < CFG.cooldown)

    def sent(self, s: str, score: float):
        with self.lock, sqlite3.connect(self.p) as c:
            c.execute(
                "INSERT INTO state VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET sent=excluded.sent, score=excluded.score",
                (s, time.time(), score),
            )


DBS = DB(CFG.db)


# ============================================================
# CANDIDATES
# ============================================================

def candidates(st: List[dict], ft: List[dict]) -> List[dict]:
    fm = {x.get("symbol"): x for x in ft}
    out = []
    for x in st:
        s = x.get("symbol", "")
        if not s.endswith("USDT") or s in EXCLUDED:
            continue
        if any(s.endswith(z) for z in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
            continue
        f = fm.get(s)
        if not f:
            continue
        try:
            if float(x.get("quoteVolume", 0)) < CFG.min_volume:
                continue
            if float(f.get("quoteVolume", 0)) < CFG.min_volume:
                continue
        except:
            continue
        out.append(x)
    return out


# ============================================================
# V7 ULTRA CONFLUENCE ANALYSIS ENGINE
# ============================================================

def analyze_v7(spot_ticker: dict) -> Dict[str, Any]:
    symbol = spot_ticker.get("symbol", "")
    try:
        sp15 = klines(SPOT, symbol, "15m", 220)
        fu15 = klines(FUT, symbol, "15m", 220)

        if len(sp15) < 200 or len(fu15) < 200:
            return {"status": "insufficient"}

        closed_sp = sp15[:-1]
        closes = [float(x[4]) for x in closed_sp]
        highs = [float(x[2]) for x in closed_sp]
        lows = [float(x[3]) for x in closed_sp]
        vols = [float(x[7]) for x in closed_sp]
        buys = [float(x[10]) for x in closed_sp]
        
        fut_vols = [float(x[7]) for x in fu15[:-1]]

        price = float(sp15[-1][4])

        # 1. EMA 50 / 200 ve Trend Analizi
        ema50_list = calculate_ema(closes, 50)
        ema200_list = calculate_ema(closes, 200)
        if not ema50_list or not ema200_list:
            return {"status": "insufficient"}

        ema50 = ema50_list[-1]
        ema200 = ema200_list[-1]

        # 2. MACD Hesaplaması
        macd_val, macd_sig, macd_hist = calculate_macd(closes)
        if macd_val is None or macd_sig is None:
            return {"status": "insufficient"}

        # 3. RSI Hesaplaması
        rsi_val = calculate_rsi(closes)
        if rsi_val is None:
            return {"status": "insufficient"}

        # 4. Yapısal Destek / Direnç ve Kırılım
        structure, support, resistance = check_structure_breakout(closes, highs, lows, price)

        # 5. Hacim ve Alıcı/Satıcı Baskısı
        avg_vol = sum(vols[-25:-1]) / 24
        avg_fut_vol = sum(fut_vols[-25:-1]) / 24

        if avg_vol <= 0 or avg_fut_vol <= 0:
            return {"status": "insufficient"}

        vr = vols[-1] / avg_vol
        fvr = fut_vols[-1] / avg_fut_vol
        taker_buy_pct = (buys[-1] / vols[-1]) * 100 if vols[-1] > 0 else 50.0

        # 6. Türev Verileri (OI ve Funding)
        now_oi = open_interest(symbol)
        old_oi = DBS.getoi(symbol)
        oi_change = pct(old_oi, now_oi) if old_oi and now_oi else 0.0
        fund_rate = funding(symbol)
        DBS.putoi(symbol, now_oi)

        # ====================================================
        # 🟢 BULLISH (LONG) SOMUT DELİL PUANLAMASI
        # ====================================================
        bull_score = 0
        bull_evidence = []

        if structure == "BULLISH_BREAKOUT":
            bull_score += 25
            bull_evidence.append(f"💥 Direnç Kırıldı (${resistance:.4g})")

        if price > ema200 and ema50 > ema200:
            bull_score += 20
            bull_evidence.append("🌟 Golden Cross & EMA200 Üzerinde Trend")
        elif price > ema50:
            bull_score += 10
            bull_evidence.append("📈 EMA50 Üzerinde Kısa Vadeli Yükseliş")

        if vr >= 3.0 and taker_buy_pct >= 62:
            bull_score += 20
            bull_evidence.append(f"🐋 Güçlü Spot Hacmi ({vr:.1f}x) & Alıcı Baskısı (%{taker_buy_pct:.1f})")
        elif vr >= 2.0 and taker_buy_pct >= 56:
            bull_score += 10
            bull_evidence.append(f"📈 Yükselen Hacim ({vr:.1f}x) & Pozitif Baskı")

        if macd_hist > 0 and macd_val > macd_sig:
            bull_score += 15
            bull_evidence.append("📊 MACD Pozitif Kesişim (Yukarı İvme)")

        if 50 <= rsi_val <= 68:
            bull_score += 10
            bull_evidence.append(f"📈 RSI İvme Bölgesinde ({rsi_val:.1f})")

        if oi_change >= 4.0:
            bull_score += 10
            bull_evidence.append(f"📈 Open Interest Güçlü Artıyor (+%{oi_change:.1f})")

        if fund_rate < -0.0005:
            bull_score += 5
            bull_evidence.append("⚡ Negatif Funding (Short Squeeze Potansiyeli)")

        # ====================================================
        # 🔴 BEARISH (SHORT) SOMUT DELİL PUANLAMASI
        # ====================================================
        bear_score = 0
        bear_evidence = []

        if structure == "BEARISH_BREAKDOWN":
            bear_score += 25
            bear_evidence.append(f"📉 Desteğe Çöküş Kırılımı (${support:.4g})")

        if price < ema200 and ema50 < ema200:
            bear_score += 20
            bear_evidence.append("💀 Death Cross & EMA200 Altında Çöküş Trendi")
        elif price < ema50:
            bear_score += 10
            bear_evidence.append("📉 EMA50 Altında Düşüş Baskısı")

        if vr >= 3.0 and taker_buy_pct <= 38:
            bear_score += 20
            bear_evidence.append(f"🔻 Devasa Satış Hacmi ({vr:.1f}x) & Satıcı Baskısı (%{100-taker_buy_pct:.1f})")
        elif vr >= 2.0 and taker_buy_pct <= 44:
            bear_score += 10
            bear_evidence.append(f"📉 Yükselen Satış Hacmi ({vr:.1f}x)")

        if macd_hist < 0 and macd_val < macd_sig:
            bear_score += 15
            bear_evidence.append("📊 MACD Negatif Kesişim (Aşağı İvme)")

        if 32 <= rsi_val <= 50:
            bear_score += 10
            bear_evidence.append(f"📉 RSI Çöküş Bölgesinde ({rsi_val:.1f})")

        if oi_change >= 4.0:
            bear_score += 10
            bear_evidence.append(f"📈 OI Artıyor / Satıcı Pozisyon Yığılması (+%{oi_change:.1f})")

        if fund_rate > 0.001:
            bear_score += 5
            bear_evidence.append("⚠️ Aşırı Pozitif Funding (Long Squeeze Potansiyeli)")

        # ====================================================
        # 🚨 ULTRA SIKI FİLTRE — MİNİMUM 85 PUAN
        # ====================================================
        if bull_score >= CFG.ultra_score_threshold:
            return {
                "status": "signal",
                "symbol": symbol,
                "direction": "🟢 BULLISH (LONG)",
                "score": min(100, bull_score),
                "price": price,
                "support": support,
                "resistance": resistance,
                "vr": vr,
                "fvr": fvr,
                "buy_pct": taker_buy_pct,
                "rsi": rsi_val,
                "oi": oi_change,
                "fund": fund_rate,
                "evidence": bull_evidence,
            }
        elif bear_score >= CFG.ultra_score_threshold:
            return {
                "status": "signal",
                "symbol": symbol,
                "direction": "🔴 BEARISH (SHORT)",
                "score": min(100, bear_score),
                "price": price,
                "support": support,
                "resistance": resistance,
                "vr": vr,
                "fvr": fvr,
                "buy_pct": taker_buy_pct,
                "rsi": rsi_val,
                "oi": oi_change,
                "fund": fund_rate,
                "evidence": bear_evidence,
            }

        return {"status": "below_threshold", "score": max(bull_score, bear_score)}

    except Exception as e:
        log.debug("%s V7 analiz hatası: %s", symbol, e)
        return {"status": "error"}


# ============================================================
# TELEGRAM MSG FORMATTER
# ============================================================

def msg(r: Dict[str, Any]) -> str:
    evidence_text = "\n".join("• " + x for x in r["evidence"])
    return (
        f"🐋 BALİNA RADARI V7 — ULTRA CONFLUENCE\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 YÖN: {r['direction']}\n"
        f"🪙 COIN: #{r['symbol']}\n"
        f"💰 FİYAT: {r['price']:.8g}\n"
        f"🏆 GÜVEN SKORU: {r['score']} / 100\n\n"
        f"📐 TEKNİK SEVİYELER:\n"
        f"• Destek (Support): ${r['support']:.4g}\n"
        f"• Direnç (Resistance): ${r['resistance']:.4g}\n\n"
        f"📊 HACİM VE TÜREV METRİKLERİ:\n"
        f"• Spot / Fut Hacim: {r['vr']:.2f}x / {r['fvr']:.2f}x\n"
        f"• Alıcı Baskısı: %{r['buy_pct']:.1f}\n"
        f"• RSI: {r['rsi']:.1f} | OI: %{r['oi']:.2f}\n"
        f"• Funding Rate: %{r['fund']*100:.4f}\n\n"
        f"🔎 SOMUT DELİLLER ({len(r['evidence'])}/5 TEYİT):\n"
        f"{evidence_text}\n\n"
        f"⚠️ Yüksek güçlü teknik teyit kombinasyonudur. İşlem yaparken stop seviyelerine sadık kalınız."
    )


# ============================================================
# SCAN & LOOP
# ============================================================

def scan():
    st = tickers(SPOT)
    ft = tickers(FUT)
    if not st or not ft:
        return
    cs = candidates(st, ft)
    log.info("📋 %d aday V7 Ultra süzgecinden geçiriliyor...", len(cs))
    stats = {}
    signals = []
    with ThreadPoolExecutor(max_workers=CFG.workers) as ex:
        fs = [ex.submit(analyze_v7, t) for t in cs]
        for f in as_completed(fs):
            r = f.result()
            k = r.get("status", "error")
            stats[k] = stats.get(k, 0) + 1
            if k == "signal":
                signals.append(r)
    
    signals.sort(key=lambda x: x["score"], reverse=True)
    sent = 0
    for r in signals[:CFG.max_signals]:
        if DBS.cooldown(r["symbol"]):
            continue
        if telegram(msg(r)):
            DBS.sent(r["symbol"], r["score"])
            sent += 1
        time.sleep(0.4)

    log.info(
        "📊 V7 SONUÇ | Aday:%d | ULTRA Sinyal:%d | Eşik Altı:%d | Yetersiz Veri:%d | Hata:%d",
        len(cs), sent, stats.get("below_threshold", 0), stats.get("insufficient", 0), stats.get("error", 0)
    )
    if signals:
        log.info("🏆 VIP SİNYALLER: %s", ", ".join(f"{r['symbol']}={r['direction']}({r['score']})" for r in signals))


def loop():
    log.info("🐋 BALİNA RADARI V7 ULTRA başlatılıyor...")
    telegram(
        "🐋 BALİNA RADARI V7 ULTRA AKTİF\n\n"
        "🎯 Çoklu Teyit (Ultra-Confluence)\n"
        "🟢 Long & 🔴 Short Çift Yönlü Motor\n"
        "💥 Destek / Direnç Kırılımları\n"
        "🌟 Golden & Death Cross Tespiti\n"
        "📊 MACD & RSI İvme Doğrulaması\n"
        "🛡️ Minimum 85/100 Puan Eşiği"
    )
    while True:
        t = time.time()
        try:
            scan()
        except Exception as e:
            log.exception("Tarama döngüsü hatası: %s", e)
        time.sleep(max(1, CFG.scan_interval - (time.time() - t)))


Thread(target=loop, daemon=True, name="balina-v7").start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), use_reloader=False)
  

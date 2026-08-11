import os,time,sqlite3,logging,sys
from threading import Thread,Lock
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask

# ============================================================
# 🐋 BALİNA RADARI V14 — DIP → BİRİKİM → DÖNÜŞ
# ============================================================
# V13'ÜN ÇALIŞAN RAILWAY İSKELETİ KORUNDU.
#
# V14 hedefi:
#   "En çok yükseleni" değil,
#   "yerel dip/birikim bölgesinde olup para girişi alan
#    ve henüz kaçmamış" tokenleri bulmak.
#
# Telegram yalnızca öncelikli CANDIDATE / STRONG sonuçlarını
# gönderir. Dahili PASS sonuçları mesaj yağmuruna dönüşmez.
#
# Not: "dip" kesin piyasa dibi değildir; ölçülen kısa vadeli
# yerel dip/birikim bölgesidir.
# ============================================================

MIN_VOLUME=float(os.getenv("MIN_VOLUME_USDT","1000000"))
SCAN_INTERVAL=int(os.getenv("SCAN_INTERVAL","60"))
WORKERS=int(os.getenv("MAX_WORKERS","6"))

STRONG_THRESHOLD=int(os.getenv("STRONG_THRESHOLD","84"))
CANDIDATE_THRESHOLD=int(os.getenv("CANDIDATE_THRESHOLD","74"))

MAX_SIGNALS=int(os.getenv("MAX_SIGNALS_PER_SCAN","2"))
COOLDOWN=int(os.getenv("SIGNAL_COOLDOWN","7200"))
TIMEOUT=int(os.getenv("REQUEST_TIMEOUT","8"))
DB_PATH=os.getenv("STATE_DB_PATH","balina_v14.db")

TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
CHAT=os.getenv("TELEGRAM_CHAT_ID","")

SPOT="https://api.binance.com"
FUT="https://fapi.binance.com"

EXCLUDED={
    "BTCUSDT","ETHUSDT","USDCUSDT","FDUSDUSDT",
    "TUSDUSDT","USDPUSDT","DAIUSDT","BUSDUSDT"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout
)
log=logging.getLogger("balina-v14")


# ============================================================
# V13 ÇALIŞAN NETWORK İSKELETİ — DEĞİŞTİRİLMEDİ
# ============================================================

def session():
    kw=dict(
        total=2,
        connect=2,
        read=2,
        backoff_factor=.5,
        status_forcelist=[429,500,502,503,504],
        raise_on_status=False
    )
    try:
        r=Retry(allowed_methods=["GET","POST"],**kw)
    except TypeError:
        r=Retry(method_whitelist=["GET","POST"],**kw)

    s=requests.Session()
    s.mount(
        "https://",
        HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=r
        )
    )
    s.headers.update({
        "User-Agent":"BalinaRadari-V14/1.0"
    })
    return s

S=session()


def api(base,path,params=None):
    r=S.get(
        base+path,
        params=params,
        timeout=TIMEOUT
    )
    r.raise_for_status()
    return r.json()


def telegram(text):
    if not TOKEN or not CHAT:
        log.warning("Telegram credential eksik.")
        return False

    try:
        r=S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id":CHAT,
                "text":text
            },
            timeout=TIMEOUT
        )
        r.raise_for_status()
        return bool(r.json().get("ok"))
    except Exception as e:
        log.error("Telegram: %s",e)
        return False


def tickers(base):
    try:
        return api(
            base,
            "/api/v3/ticker/24hr"
            if base==SPOT
            else "/fapi/v1/ticker/24hr"
        )
    except Exception as e:
        log.error("Ticker: %s",e)
        return []


def klines(base,symbol,interval,limit):
    try:
        return api(
            base,
            "/api/v3/klines"
            if base==SPOT
            else "/fapi/v1/klines",
            {
                "symbol":symbol,
                "interval":interval,
                "limit":limit
            }
        )
    except Exception as e:
        log.debug(
            "%s %s: %s",
            symbol,
            interval,
            e
        )
        return []


def oi(symbol):
    try:
        return float(
            api(
                FUT,
                "/fapi/v1/openInterest",
                {"symbol":symbol}
            )["openInterest"]
        )
    except Exception:
        return None


# ============================================================
# HELPERS
# ============================================================

def pct(a,b):
    return (b-a)/a*100 if a and a>0 and b is not None else 0.0


def clamp(x):
    return max(0,min(100,int(round(x))))


def average(values):
    return sum(values)/len(values) if values else 0.0


def rsi(closes,period=14):
    if len(closes)<period+1:
        return 50.0

    gains=[]
    losses=[]

    for i in range(1,len(closes)):
        d=closes[i]-closes[i-1]
        gains.append(max(d,0.0))
        losses.append(max(-d,0.0))

    gains=gains[-period:]
    losses=losses[-period:]

    ag=average(gains)
    al=average(losses)

    if al<=0:
        return 100.0

    rs=ag/al
    return 100-(100/(1+rs))


# ============================================================
# DATABASE — V13 İLE AYNI MANTIK
# ============================================================

class DB:
    def __init__(self,path):
        self.path=path
        self.lock=Lock()

        with self.lock,sqlite3.connect(path) as d:
            d.execute(
                "CREATE TABLE IF NOT EXISTS state("
                "symbol TEXT PRIMARY KEY,sent REAL,score REAL)"
            )
            d.execute(
                "CREATE TABLE IF NOT EXISTS oi("
                "symbol TEXT PRIMARY KEY,value REAL,ts REAL)"
            )

    def getoi(self,s):
        with self.lock,sqlite3.connect(self.path) as d:
            r=d.execute(
                "SELECT value,ts FROM oi WHERE symbol=?",
                (s,)
            ).fetchone()

        return (
            None
            if not r or time.time()-r[1]>SCAN_INTERVAL*5
            else float(r[0])
        )

    def putoi(self,s,v):
        if v is None:
            return

        with self.lock,sqlite3.connect(self.path) as d:
            d.execute(
                "INSERT INTO oi VALUES(?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET "
                "value=excluded.value,ts=excluded.ts",
                (s,v,time.time())
            )

    def cooldown(self,s):
        with self.lock,sqlite3.connect(self.path) as d:
            r=d.execute(
                "SELECT sent FROM state WHERE symbol=?",
                (s,)
            ).fetchone()

        return bool(
            r and time.time()-r[0]<COOLDOWN
        )

    def sent(self,s,score):
        with self.lock,sqlite3.connect(self.path) as d:
            d.execute(
                "INSERT INTO state VALUES(?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET "
                "sent=excluded.sent,score=excluded.score",
                (s,time.time(),score)
            )


DBS=DB(DB_PATH)


# ============================================================
# ADAY HAVUZU
# ============================================================

def candidates(st,ft):
    fm={x.get("symbol"):x for x in ft}
    out=[]

    for x in st:
        s=x.get("symbol","")

        if (
            not s.endswith("USDT")
            or s in EXCLUDED
            or any(
                s.endswith(z)
                for z in (
                    "UPUSDT",
                    "DOWNUSDT",
                    "BULLUSDT",
                    "BEARUSDT"
                )
            )
        ):
            continue

        f=fm.get(s)
        if not f:
            continue

        try:
            spot_volume=float(
                x.get("quoteVolume",0)
            )
            futures_volume=float(
                f.get("quoteVolume",0)
            )
            daily_change=float(
                x.get("priceChangePercent",0)
            )

            if spot_volume<MIN_VOLUME:
                continue

            if futures_volume<MIN_VOLUME:
                continue

            # Günlük hareketi aşırı olanları daha baştan ele.
            if daily_change>16:
                continue

            out.append(s)

        except (TypeError,ValueError):
            continue

    return out


# ============================================================
# V14 ANALİZ MOTORU
# ============================================================

def analyze(s):
    try:
        sp=klines(SPOT,s,"1m",48)
        fu=klines(FUT,s,"1m",36)
        sp5=klines(SPOT,s,"5m",18)

        if len(sp)<35 or len(fu)<30 or len(sp5)<10:
            return {"status":"insufficient"}

        live=sp[-1]
        price=float(live[4])
        live_open=float(live[1])
        live_low=float(live[3])

        lc=pct(live_open,price)

        c5=[float(x[4]) for x in sp5]
        m5=pct(c5[-2],price)
        m15=pct(c5[-4],price)
        m30=pct(c5[-7],price)

        # ----------------------------------------------------
        # 1) GEÇ KALMA / DÜŞÜŞ KORUMASI
        # ----------------------------------------------------
        if lc>1.20 or m5>2.50 or m15>4.50 or m30>7.0:
            return {"status":"late"}

        if lc<-2.0 or m5<-3.5:
            return {"status":"weak"}

        # ----------------------------------------------------
        # 2) YEREL FİYAT KONUMU
        # ----------------------------------------------------
        closed=sp[:-1]

        lows=[float(x[3]) for x in closed[-30:]]
        highs=[float(x[2]) for x in closed[-30:]]

        lo=min(lows)
        hi=max(highs)

        location=(
            (price-lo)/(hi-lo)*100
            if hi>lo else 50.0
        )

        very_low=location<=25
        near_low=location<=40

        # 5m bağlamı
        closed5=sp5[:-1]
        lows5=[float(x[3]) for x in closed5[-10:]]
        highs5=[float(x[2]) for x in closed5[-10:]]

        lo5=min(lows5)
        hi5=max(highs5)

        location5=(
            (price-lo5)/(hi5-lo5)*100
            if hi5>lo5 else 50.0
        )

        near_5m_low=location5<=45

        # ----------------------------------------------------
        # 3) PRICE ACTION
        # ----------------------------------------------------
        a,b,c=sp[-2],sp[-3],sp[-4]

        a_low=float(a[3])
        b_low=float(b[3])
        c_low=float(c[3])

        a_high=float(a[2])
        b_high=float(b[2])

        a_open=float(a[1])
        a_close=float(a[4])
        b_close=float(b[4])

        higher_low=(
            a_low>b_low
            and a_low>=c_low
            and live_low>=a_low
        )

        break_high=price>a_high
        break_zone=price>max(a_high,b_high)

        reclaim=(
            a_close>=b_close
            and lc>=-0.05
        )

        reversal_points=sum([
            higher_low,
            break_high,
            reclaim
        ])

        reversal=(
            reversal_points>=2
            or break_zone
        )

        # Alt fitil: satış karşılanıyor olabilir.
        body=abs(a_close-a_open)
        lower_wick=min(a_open,a_close)-a_low

        wick_rejection=(
            lower_wick>0
            and lower_wick>=body*0.8
        )

        # ----------------------------------------------------
        # 4) ALICI BASKISI
        # ----------------------------------------------------
        buy3=sum(float(x[10]) for x in sp[-3:])
        vol3=sum(float(x[7]) for x in sp[-3:])

        buy5=sum(float(x[10]) for x in sp[-5:])
        vol5=sum(float(x[7]) for x in sp[-5:])

        bp3=buy3/vol3*100 if vol3>0 else 50
        bp5=buy5/vol5*100 if vol5>0 else 50

        bp=bp3*.65+bp5*.35

        # ----------------------------------------------------
        # 5) HACİM / TRADE FLOW
        # ----------------------------------------------------
        sc=sp[:-1]
        fc=fu[:-1]

        sv=[float(x[7]) for x in sp]
        fv=[float(x[7]) for x in fu]
        tr=[float(x[8]) for x in sp]

        avs=sum(float(x[7]) for x in sc[-18:])/18
        avf=sum(float(x[7]) for x in fc[-18:])/18
        avt=sum(float(x[8]) for x in sc[-18:])/18

        if min(avs,avf,avt)<=0:
            return {"status":"insufficient"}

        sr=sum(sv[-3:])/3/avs
        fr=sum(fv[-3:])/3/avf
        trr=sum(tr[-3:])/3/avt

        previous_spot=sum(sv[-6:-3])/3
        acc=(
            sum(sv[-3:])/3/previous_spot
            if previous_spot>0 else 1
        )

        spot_leads=(
            sr>=2
            and sr>=fr*1.15
        )

        # ----------------------------------------------------
        # 6) RSI
        # ----------------------------------------------------
        r1=rsi([float(x[4]) for x in sp],14)
        r5=rsi(c5,14)

        # ----------------------------------------------------
        # 7) STRATEJİ PUANI
        # ----------------------------------------------------
        # 100 puanın ağırlığı:
        # Dip konumu      25
        # Price Action    25
        # Spot akışı      20
        # Alıcı baskısı   15
        # Teyit           10
        # Erkenlik         5
        # ----------------------------------------------------

        score=0
        reasons=[]

        # DIP / KONUM — 25
        if very_low:
            score+=20
            reasons.append("🟦 Yerel dip bölgesi ÇOK güçlü")
        elif near_low:
            score+=14
            reasons.append("🟦 Yerel dip/birikim bölgesi")
        elif location<=55:
            score+=6
            reasons.append("🟦 Fiyat aralığın alt yarısında")

        if near_5m_low:
            score+=5
            reasons.append("📍 5m fiyat konumu erken")

        # PRICE ACTION — 25
        if higher_low:
            score+=9
            reasons.append("📐 Higher-Low oluştu")

        if break_high:
            score+=9
            reasons.append("💥 Önceki 1m tepe kırıldı")

        if break_zone:
            score+=4
            reasons.append("💥 Kısa vadeli tepe bölgesi aşıldı")

        if wick_rejection:
            score+=3
            reasons.append("🛡️ Satış fitili karşılandı")

        # SPOT FLOW — 20
        if sr>=4:
            score+=12
            reasons.append(
                f"🐋 Spot para girişi ÇOK güçlü ({sr:.2f}x)"
            )
        elif sr>=3:
            score+=10
            reasons.append(
                f"🐋 Spot para girişi güçlü ({sr:.2f}x)"
            )
        elif sr>=2.5:
            score+=8
            reasons.append(
                f"📈 Spot akışı artıyor ({sr:.2f}x)"
            )
        elif sr>=2:
            score+=6
            reasons.append(
                f"📊 Spot hacmi destekliyor ({sr:.2f}x)"
            )

        if spot_leads:
            score+=5
            reasons.append("🐋 Spot akışı futures'tan önce")

        if acc>=2:
            score+=3
            reasons.append(
                f"🚀 Hacim ivmesi artıyor ({acc:.2f}x)"
            )

        # BUYER — 15
        if bp>=85:
            score+=15
            reasons.append(
                f"🐋 Çok güçlü alıcı baskısı (%{bp:.1f})"
            )
        elif bp>=78:
            score+=12
            reasons.append(
                f"🟢 Güçlü alıcı baskısı (%{bp:.1f})"
            )
        elif bp>=70:
            score+=9
            reasons.append(
                f"🟢 Pozitif alıcı baskısı (%{bp:.1f})"
            )
        elif bp>=64:
            score+=5
            reasons.append(
                f"🟢 Alıcı baskısı (%{bp:.1f})"
            )

        # CONFIRMATION — 10
        if trr>=2:
            score+=3
            reasons.append(
                f"📈 İşlem sayısı güçlü ({trr:.2f}x)"
            )
        elif trr>=1.25:
            score+=2
            reasons.append(
                f"📊 İşlem sayısı destekliyor ({trr:.2f}x)"
            )

        if fr>=2:
            score+=3
            reasons.append(
                f"⚡ Futures destekliyor ({fr:.2f}x)"
            )
        elif fr>=1.2:
            score+=1
            reasons.append(
                f"⚡ Futures normalin üzerinde ({fr:.2f}x)"
            )

        if 38<=r1<=62:
            score+=2
            reasons.append(
                f"📊 RSI dengeli ({r1:.1f})"
            )

        if r1<42 and reversal:
            score+=2
            reasons.append(
                "🎯 Düşük RSI'dan dönüş işareti"
            )

        # EARLY — 5
        if .00<=lc<=.50:
            score+=5
            reasons.append(
                f"🎯 Fiyat hâlâ çok erken (+%{lc:.2f})"
            )
        elif .50<lc<=.90:
            score+=2
            reasons.append(
                f"📈 Hareket başladı (+%{lc:.2f})"
            )

        # ----------------------------------------------------
        # 8) CEZALAR / TEHLİKE KONTROLLERİ
        # ----------------------------------------------------
        # Aşırı düşüş + dönüş yoksa "dip" diye işaretleme.
        falling=(
            m5<-0.8
            and m15<-1.0
            and not reversal
        )

        if falling:
            score-=10
            reasons.append(
                "⚠️ Düşüş devam ediyor; dönüş teyidi yok"
            )

        # Çok yükselmişse erkenlik puanını geri al.
        if m5>1.5:
            score-=8
            reasons.append(
                "⚠️ 5m hareket fazla hızlandı"
            )

        if m15>3.0:
            score-=8
            reasons.append(
                "⚠️ 15m hareket fazla yükseldi"
            )

        # Alıcı baskısı düşükken büyük spot hacmi tek başına
        # LONG sebebi olmasın.
        distribution=(
            bp<60
            and sr>=3
            and m5<-.5
        )

        if distribution:
            score-=12
            reasons.append(
                "⚠️ Hacim var fakat satış riski yüksek"
            )

        score=clamp(score)

        # ----------------------------------------------------
        # 9) OI — SADECE ADAYLARDA
        # ----------------------------------------------------
        oi_change=None

        if score>=CANDIDATE_THRESHOLD-5:
            now=oi(s)
            old=DBS.getoi(s)

            if old is not None and now is not None:
                oi_change=pct(old,now)

                if oi_change>=.8:
                    score=clamp(score+3)
                    reasons.append(
                        f"📈 OI destekli (+%{oi_change:.2f})"
                    )
                elif oi_change<=-1.5:
                    score=clamp(score-3)
                    reasons.append(
                        f"⚠️ OI geriliyor (%{oi_change:.2f})"
                    )

            DBS.putoi(s,now)

        # ----------------------------------------------------
        # 10) STRATEJİ EVRESİ
        # ----------------------------------------------------
        accumulation=(
            (very_low or near_low)
            and sr>=2
            and bp>=64
            and not falling
            and not distribution
        )

        turning=(
            accumulation
            and reversal
        )

        early=(
            turning
            and lc<=.90
            and m5<=1.50
            and m15<=3.00
        )

        # Güçlü sinyal için yalnızca yüksek puan değil,
        # yapısal koşullar da zorunlu.
        strong=(
            score>=STRONG_THRESHOLD
            and accumulation
            and turning
            and early
        )

        candidate=(
            score>=CANDIDATE_THRESHOLD
            and accumulation
            and (
                turning
                or (
                    bp>=75
                    and sr>=2.5
                )
            )
        )

        if strong:
            status="STRONG"
            signal_type="🟢 BUNU DEĞERLENDİR"
        elif candidate:
            status="CANDIDATE"
            signal_type="🟡 STRATEJİ ADAYI"
        else:
            status="PASS"
            signal_type="⚪ PASS"

        return {
            "status":status,
            "type":signal_type,
            "symbol":s,
            "score":score,
            "price":price,

            "sr":sr,
            "fr":fr,
            "tr":trr,
            "bp":bp,
            "lc":lc,
            "m5":m5,
            "m15":m15,
            "m30":m30,
            "acc":acc,

            "location":location,
            "location5":location5,

            "r1":r1,
            "r5":r5,

            "oi":oi_change,

            "higher_low":higher_low,
            "break_high":break_high,
            "reversal":reversal,
            "accumulation":accumulation,
            "turning":turning,
            "early":early,

            "reasons":reasons
        }

    except Exception as e:
        log.debug(
            "%s analiz hatası: %s",
            s,
            e
        )
        return {"status":"error"}


# ============================================================
# TELEGRAM — SADE / KARAR ODAKLI
# ============================================================

def message(r):
    if r["status"]=="STRONG":
        title="🟢 BUNU DEĞERLENDİR"
        stage="DİP/BİRİKİM → DÖNÜŞ"
        strength="ÇOK GÜÇLÜ"
        footer=(
            "🎯 STRATEJİNE UYGUN ERKEN GİRİŞ ADAYI"
        )
    else:
        title="🟡 STRATEJİ ADAYI"
        stage=(
            "BİRİKİM → DÖNÜŞ"
            if r["turning"]
            else "BİRİKİM → DÖNÜŞ BEKLENİYOR"
        )
        strength="GÜÇLÜ"
        footer=(
            "👁️ Stratejiye uygun; dönüş teyidini takip et."
        )

    oi_txt=(
        "veri yok"
        if r["oi"] is None
        else f"{r['oi']:+.2f}%"
    )

    dip=(
        "ÇOK GÜÇLÜ"
        if r["location"]<=25
        else "GÜÇLÜ"
        if r["location"]<=40
        else "ORTA"
    )

    reversal=(
        "ONAYLI"
        if r["reversal"]
        else "BEKLENİYOR"
    )

    late=(
        "HAYIR"
        if r["lc"]<=.90
        and r["m5"]<=1.50
        and r["m15"]<=3.00
        else "EVET"
    )

    return (
        f"🐋 BALİNA RADARI V14\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{title}\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 Fiyat: {r['price']:.8g}\n"
        f"🏆 FIRSAT: {r['score']}/100\n\n"

        f"🎯 SENİN STRATEJİN\n"
        f"• Aşama: {stage}\n"
        f"• Dip/Birikim: {dip}\n"
        f"• Para girişi: "
        f"{'ÇOK GÜÇLÜ' if r['sr']>=4 else 'GÜÇLÜ' if r['sr']>=2.5 else 'POZİTİF'}\n"
        f"• Alıcı baskısı: "
        f"{'ÇOK GÜÇLÜ' if r['bp']>=85 else 'GÜÇLÜ' if r['bp']>=75 else 'POZİTİF'} "
        f"(%{r['bp']:.1f})\n"
        f"• Fiyat dönüşü: {reversal}\n"
        f"• Geç kalmış mı?: {late}\n\n"

        f"📊 KISA VERİ\n"
        f"• Spot hacim: {r['sr']:.2f}x\n"
        f"• Futures hacim: {r['fr']:.2f}x\n"
        f"• İşlem sayısı: {r['tr']:.2f}x\n"
        f"• 1m: {r['lc']:+.2f}%\n"
        f"• 5m: {r['m5']:+.2f}%\n"
        f"• 15m: {r['m15']:+.2f}%\n"
        f"• Hacim ivmesi: {r['acc']:.2f}x\n"
        f"• RSI: {r['r1']:.1f}\n"
        f"• OI: {oi_txt}\n\n"

        f"🔎 TEYİTLER\n"
        + "\n".join(
            "• "+x
            for x in r["reasons"]
        )
        + "\n\n"
        f"🔥 GÜÇ SEVİYESİ: {strength}\n"
        f"{footer}\n\n"
        f"⚠️ Teknik filtre/sınıflandırmadır; kesin dip veya kâr garantisi değildir."
    )


# ============================================================
# SCAN — V13 İLE AYNI ÇALIŞMA MODELİ
# ============================================================

def scan():
    start=time.time()

    st=tickers(SPOT)
    ft=tickers(FUT)

    if not st or not ft:
        return True

    syms=candidates(st,ft)
    res=[]
    stats={}

    with ThreadPoolExecutor(
        max_workers=WORKERS
    ) as ex:
        jobs=[
            ex.submit(analyze,s)
            for s in syms
        ]

        for j in as_completed(jobs):
            r=j.result()
            k=r.get("status","error")
            stats[k]=stats.get(k,0)+1

            if k in ("STRONG","CANDIDATE"):
                res.append(r)

    # Önce gerçekten dipten dönüşe en yakın olanlar.
    pri={
        "STRONG":2,
        "CANDIDATE":1
    }

    res.sort(
        key=lambda x:(
            pri.get(x["status"],0),
            x["score"],
            x["location"]*-1,
            x["bp"],
            x["sr"]
        ),
        reverse=True
    )

    sent=0

    for r in res[:MAX_SIGNALS]:
        if DBS.cooldown(r["symbol"]):
            continue

        if telegram(message(r)):
            DBS.sent(
                r["symbol"],
                r["score"]
            )
            sent+=1

        time.sleep(.5)

    elapsed=time.time()-start
    err=stats.get("error",0)
    total=max(1,len(syms))

    log.info(
        "🐋 V14 | Aday:%d | STRONG:%d | CANDIDATE:%d | "
        "Geç:%d | Zayıf:%d | Hata:%d | Gönder:%d | %.1fs",
        len(syms),
        stats.get("STRONG",0),
        stats.get("CANDIDATE",0),
        stats.get("late",0),
        stats.get("weak",0),
        err,
        sent,
        elapsed
    )

    return (
        err/total>.30
        or elapsed>SCAN_INTERVAL*1.25
    )


# ============================================================
# FLASK — V13 İLE AYNI
# ============================================================

app=Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V14 Dip → Birikim → Dönüş Aktif!"


@app.route("/health")
def health():
    return {
        "status":"ok",
        "bot":"Balina Radarı V14",
        "strong_threshold":STRONG_THRESHOLD,
        "candidate_threshold":CANDIDATE_THRESHOLD,
        "mode":"dip-accumulation-reversal"
    }


# ============================================================
# LOOP — V13 İLE AYNI BAŞLANGIÇ MODELİ
# ============================================================

def loop():
    log.info(
        "🐋 BALİNA RADARI V14 başlatılıyor..."
    )

    if TOKEN and CHAT:
        telegram(
            "🐋 BALİNA RADARI V14 AKTİF\n\n"
            "🎯 Dip → Birikim → Dönüş\n"
            "🟢 Güçlü erken adaylar\n"
            "🟡 Strateji adayları\n"
            "🐋 Spot para akışı öncelikli\n"
            "📐 Higher-Low + tepe kırılımı\n"
            "📊 Alıcı baskısı + hacim\n"
            "🚫 Geç kalmış hareket filtresi\n"
            "🛡️ Rate-limit koruması"
        )

    while True:
        t=time.time()

        try:
            backoff=scan()
        except Exception:
            log.exception(
                "Tarama döngüsü hatası"
            )
            backoff=True

        elapsed=time.time()-t

        if backoff:
            time.sleep(
                max(
                    180,
                    SCAN_INTERVAL*3
                )
            )
        else:
            time.sleep(
                max(
                    1,
                    SCAN_INTERVAL-elapsed
                )
            )


Thread(
    target=loop,
    daemon=True,
    name="balina-v14"
).start()


if __name__=="__main__":
    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv("PORT","8080")
        ),
        use_reloader=False
    )

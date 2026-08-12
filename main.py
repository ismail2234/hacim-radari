
import os,time,sqlite3,logging,sys
from threading import Thread,Lock
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask

VERSION="V20"

TR_BASE=os.getenv("BINANCE_TR_BASE","https://api.binance.me")
FUT_BASE="https://fapi.binance.com"

MIN_TR_VOLUME=float(os.getenv("MIN_TR_VOLUME","5000000"))
MIN_USDT_VOLUME=float(os.getenv("MIN_USDT_VOLUME","500000"))
SCAN_INTERVAL=int(os.getenv("SCAN_INTERVAL","45"))
WORKERS=int(os.getenv("MAX_WORKERS","6"))
MAX_ALERTS=int(os.getenv("MAX_ALERTS_PER_SCAN","2"))
COOLDOWN=int(os.getenv("SIGNAL_COOLDOWN","1800"))
TIMEOUT=int(os.getenv("REQUEST_TIMEOUT","8"))
DB_PATH=os.getenv("STATE_DB_PATH","balina_v20.db")

TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
CHAT=os.getenv("TELEGRAM_CHAT_ID","")

EXCLUDED={
    "BTC","ETH","USDC","FDUSD","TUSD","USDP","DAI","BUSD"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout
)
log=logging.getLogger("balina-v20")


def build_session():
    kw=dict(
        total=2,connect=2,read=2,
        backoff_factor=.4,
        status_forcelist=[429,500,502,503,504],
        raise_on_status=False
    )
    try:
        retry=Retry(
            allowed_methods=["GET","POST"],
            **kw
        )
    except TypeError:
        retry=Retry(
            method_whitelist=["GET","POST"],
            **kw
        )

    s=requests.Session()
    a=HTTPAdapter(
        pool_connections=30,
        pool_maxsize=30,
        max_retries=retry
    )
    s.mount("https://",a)
    s.headers.update({
        "User-Agent":"BalinaRadari-V20/2.0"
    })
    return s


S=build_session()


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
        return False
    try:
        r=S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id":CHAT,"text":text},
            timeout=TIMEOUT
        )
        r.raise_for_status()
        return bool(r.json().get("ok"))
    except Exception as e:
        log.error("Telegram: %s",e)
        return False


def tr_exchange_info():
    try:
        return api(
            TR_BASE,
            "/api/v3/exchangeInfo"
        )
    except Exception as e:
        log.error("TR exchangeInfo: %s",e)
        return {}


def tr_tickers():
    try:
        return api(
            TR_BASE,
            "/api/v3/ticker/24hr"
        )
    except Exception as e:
        log.error("TR ticker: %s",e)
        return []


def tr_klines(symbol,interval,limit):
    try:
        return api(
            TR_BASE,
            "/api/v3/klines",
            {
                "symbol":symbol,
                "interval":interval,
                "limit":limit
            }
        )
    except Exception as e:
        log.debug(
            "%s %s kline: %s",
            symbol,interval,e
        )
        return []


def futures_oi(symbol):
    try:
        return float(
            api(
                FUT_BASE,
                "/fapi/v1/openInterest",
                {"symbol":symbol}
            )["openInterest"]
        )
    except Exception:
        return None


def pct(a,b):
    return (
        (b-a)/a*100
        if a and a>0 and b is not None
        else 0.0
    )


def avg(v):
    return sum(v)/len(v) if v else 0.0


def clamp(x):
    return max(0,min(100,int(round(x))))


def ema(v,n):
    if len(v)<n:
        return avg(v)
    k=2/(n+1)
    e=avg(v[:n])
    for x in v[n:]:
        e=x*k+e*(1-k)
    return e


def rsi(v,n=14):
    if len(v)<n+1:
        return 50.0

    gains=[]
    losses=[]

    for i in range(1,len(v)):
        d=v[i]-v[i-1]
        gains.append(max(d,0))
        losses.append(max(-d,0))

    ag=avg(gains[-n:])
    al=avg(losses[-n:])

    if al<=0:
        return 100.0

    return 100-100/(1+ag/al)


def macd(v):
    if len(v)<40:
        return 0.0,0.0,0.0

    line=[]

    for i in range(26,len(v)+1):
        line.append(
            ema(v[:i],12)-ema(v[:i],26)
        )

    m=line[-1]
    sig=ema(line,9)

    return m,sig,m-sig


def bb(v,n=20,k=2):
    if len(v)<n:
        return 0.0,0.0,0.0

    x=v[-n:]
    m=avg(x)
    sd=avg([(z-m)**2 for z in x])**.5

    return m-k*sd,m,m+k*sd


def adx(h,l,c,n=14):
    if len(c)<n*2+1:
        return 0.0,0.0,0.0

    tr=[]
    plus=[]
    minus=[]

    for i in range(1,len(c)):
        tr.append(max(
            h[i]-l[i],
            abs(h[i]-c[i-1]),
            abs(l[i]-c[i-1])
        ))

        up=h[i]-h[i-1]
        dn=l[i-1]-l[i]

        plus.append(
            up if up>dn and up>0 else 0
        )
        minus.append(
            dn if dn>up and dn>0 else 0
        )

    atr=avg(tr[-n:])
    p=avg(plus[-n:])
    m=avg(minus[-n:])

    if atr<=0:
        return 0.0,0.0,0.0

    pdi=100*p/atr
    mdi=100*m/atr

    dx=(
        100*abs(pdi-mdi)/(pdi+mdi)
        if pdi+mdi else 0
    )

    return dx,pdi,mdi
class DB:
    def __init__(self,path):
        self.path=path
        self.lock=Lock()

        with sqlite3.connect(path) as d:
            d.execute("""
                CREATE TABLE IF NOT EXISTS state(
                    symbol TEXT PRIMARY KEY,
                    stage INTEGER,
                    score REAL,
                    updated REAL
                )
            """)

            d.execute("""
                CREATE TABLE IF NOT EXISTS alerts(
                    symbol TEXT PRIMARY KEY,
                    level TEXT,
                    score REAL,
                    sent REAL
                )
            """)

            d.execute("""
                CREATE TABLE IF NOT EXISTS oi(
                    symbol TEXT PRIMARY KEY,
                    value REAL,
                    ts REAL
                )
            """)


    def state(self,s):
        with self.lock,sqlite3.connect(self.path) as d:
            return d.execute(
                "SELECT stage,score,updated FROM state WHERE symbol=?",
                (s,)
            ).fetchone()


    def save_state(self,s,stage,score):
        with self.lock,sqlite3.connect(self.path) as d:
            d.execute("""
                INSERT INTO state VALUES(?,?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    stage=excluded.stage,
                    score=excluded.score,
                    updated=excluded.updated
            """,(
                s,
                stage,
                score,
                time.time()
            ))


    def alert(self,s):
        with self.lock,sqlite3.connect(self.path) as d:
            return d.execute(
                "SELECT level,score,sent FROM alerts WHERE symbol=?",
                (s,)
            ).fetchone()


    def can_send(self,s,level):
        old=self.alert(s)

        if not old:
            return True

        old_level,_,sent=old

        rank={
            "AL":1,
            "VERY":2
        }

        if rank.get(level,0)>rank.get(old_level,0):
            return True

        return (
            time.time()-sent>=COOLDOWN
        )


    def mark_alert(self,s,level,score):
        with self.lock,sqlite3.connect(self.path) as d:
            d.execute("""
                INSERT INTO alerts
                VALUES(?,?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    level=excluded.level,
                    score=excluded.score,
                    sent=excluded.sent
            """,(
                s,
                level,
                score,
                time.time()
            ))


    def get_oi(self,s):
        with self.lock,sqlite3.connect(self.path) as d:
            r=d.execute(
                "SELECT value,ts FROM oi WHERE symbol=?",
                (s,)
            ).fetchone()

        if not r:
            return None

        if time.time()-r[1]>SCAN_INTERVAL*5:
            return None

        return float(r[0])


    def put_oi(self,s,v):
        if v is None:
            return

        with self.lock,sqlite3.connect(self.path) as d:
            d.execute("""
                INSERT INTO oi VALUES(?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    value=excluded.value,
                    ts=excluded.ts
            """,(
                s,
                v,
                time.time()
            ))


DBS=DB(DB_PATH)


SYMBOLS=set()


def refresh_symbols(info):
    global SYMBOLS

    symbols=set()

    for x in info.get("symbols",[]):
        try:
            if x.get("status")!="TRADING":
                continue

            s=x.get("symbol","")
            base=x.get("baseAsset","")
            quote=x.get("quoteAsset","")

            if quote not in ("TRY","USDT"):
                continue

            if base in EXCLUDED:
                continue

            if any(
                base.endswith(z)
                for z in ("UP","DOWN","BULL","BEAR")
            ):
                continue

            symbols.add(s)

        except Exception:
            continue

    SYMBOLS=symbols

    return symbols


def candidate_symbols(tickers):
    out=[]

    for x in tickers:
        s=x.get("symbol","")

        if s not in SYMBOLS:
            continue

        try:
            qv=float(x.get("quoteVolume",0))

            quote="TRY" if s.endswith("TRY") else "USDT"

            minimum=(
                MIN_TR_VOLUME
                if quote=="TRY"
                else MIN_USDT_VOLUME
            )

            if qv<minimum:
                continue

            out.append(s)

        except (TypeError,ValueError):
            continue

    return out


def symbol_quote(s):
    if s.endswith("TRY"):
        return "TRY"
    if s.endswith("USDT"):
        return "USDT"
    return ""


def futures_symbol(s):
    base=s[:-3] if s.endswith("TRY") else s[:-4]
    return base+"USDT"
def analyze(s):
    try:
        sp=tr_klines(s,"1m",180)
        sp5=tr_klines(s,"5m",60)

        if len(sp)<100 or len(sp5)<25:
            return {"status":"PASS"}

        live=sp[-1]
        price=float(live[4])

        c=[float(x[4]) for x in sp[:-1]]
        h=[float(x[2]) for x in sp[:-1]]
        l=[float(x[3]) for x in sp[:-1]]
        o=[float(x[1]) for x in sp[:-1]]

        c5=[float(x[4]) for x in sp5[:-1]]

        m1=pct(c[-2],price)
        m3=pct(c[-4],price)
        m5=pct(c5[-2],price)
        m15=pct(c5[-4],price)

        lo30=min(l[-30:])
        hi30=max(h[-30:])
        loc30=(
            (price-lo30)/(hi30-lo30)*100
            if hi30>lo30 else 50
        )

        lo60=min(l[-60:])
        hi60=max(h[-60:])
        loc60=(
            (price-lo60)/(hi60-lo60)*100
            if hi60>lo60 else 50
        )

        sv=[float(x[7]) for x in sp[:-1]]
        tv=[float(x[8]) for x in sp[:-1]]

        av=avg(sv[-36:])
        at=avg(tv[-36:])

        if av<=0 or at<=0:
            return {"status":"PASS"}

        vol_ratio=avg(sv[-3:])/av
        trade_ratio=avg(tv[-3:])/at

        old_vol=avg(sv[-9:-3])
        impulse=(
            avg(sv[-3:])/old_vol
            if old_vol>0 else 1
        )

        # Binance kline:
        # [10] taker buy base volume
        # [11] taker buy quote volume
        buy=sum(float(x[11]) for x in sp[-4:])
        total=sum(float(x[7]) for x in sp[-4:])

        buyer=(
            buy/total*100
            if total>0 else 50
        )

        e9=ema(c,9)
        e21=ema(c,21)
        e50=ema(c,50)

        e9_old=ema(c[:-3],9)
        e21_old=ema(c[:-3],21)

        ema_bull=e9>e21
        ema_rising=e9>e9_old
        ema_cross=(
            e9>e21 and
            e9_old<=e21_old
        )

        rv=rsi(c)
        rv_old=rsi(c[:-3])

        rsi_up=rv>rv_old

        mm,ms,mh=macd(c)
        pm,ps,ph=macd(c[:-3])

        macd_up=mh>ph
        macd_bull=mm>ms

        ad,di,mdi=adx(h,l,c)

        trend=(
            ad>=18 and
            di>mdi
        )

        bl,bm,bu=bb(c)

        width=(
            (bu-bl)/bm*100
            if bm else 0
        )

        obl,obm,obu=bb(c[:-5])

        old_width=(
            (obu-obl)/obm*100
            if obm else width
        )

        squeeze=(
            width<=1.8
            or width<old_width*.82
        )

        expanding=(
            width>old_width*1.05
            if old_width else False
        )

        resistance=max(h[-20:])

        distance=max(
            0,
            (resistance-price)/price*100
        )

        breakout_now=(
            price>=resistance*.998
        )

        higher_low=(
            l[-1]>l[-3]
            and l[-3]>=l[-6]
        )

        candle_range=h[-1]-l[-1]

        close_strength=(
            (c[-1]-l[-1])/candle_range
            if candle_range>0 else .5
        )

        strong_close=close_strength>=.75

        # ---------------------------------------------
        # PARA AKIŞI
        # ---------------------------------------------

        money=0

        if vol_ratio>=3:
            money+=12
        elif vol_ratio>=2:
            money+=9
        elif vol_ratio>=1.5:
            money+=6
        elif vol_ratio>=1.2:
            money+=3

        if trade_ratio>=2:
            money+=5
        elif trade_ratio>=1.5:
            money+=3

        if buyer>=75:
            money+=7
        elif buyer>=68:
            money+=5
        elif buyer>=60:
            money+=3

        # ---------------------------------------------
        # MOMENTUM
        # ---------------------------------------------

        momentum=0

        if ema_bull:
            momentum+=4

        if ema_rising:
            momentum+=3

        if ema_cross:
            momentum+=4

        if rsi_up and 40<=rv<=68:
            momentum+=5
        elif rsi_up and 35<=rv<=72:
            momentum+=3

        if macd_up:
            momentum+=4

        if macd_bull:
            momentum+=3

        if trend:
            momentum+=4

        if price>=e50:
            momentum+=2

        # ---------------------------------------------
        # BREAKOUT
        # ---------------------------------------------

        breakout=0

        if distance<=.10:
            breakout+=10
        elif distance<=.25:
            breakout+=8
        elif distance<=.50:
            breakout+=5
        elif distance<=.80:
            breakout+=2

        if breakout_now:
            breakout+=6

        if squeeze:
            breakout+=4

        if expanding and impulse>=1.25:
            breakout+=4

        if higher_low:
            breakout+=4

        if strong_close:
            breakout+=3

        # ---------------------------------------------
        # DİP / YAPI
        # ---------------------------------------------

        structure=0

        if loc30<=25:
            structure+=8
        elif loc30<=40:
            structure+=5

        if loc60<=30:
            structure+=4

        if squeeze:
            structure+=3

        # ---------------------------------------------
        # RİSK
        # ---------------------------------------------

        risk=0

        if m15<-1.5 and not higher_low:
            risk-=12

        if rv>82:
            risk-=10
        elif rv>76:
            risk-=5

        if m5>6:
            risk-=10
        elif m5>3.5:
            risk-=5

        if buyer<55 and vol_ratio>=2:
            risk-=10

        # ---------------------------------------------
        # YENİ HAREKET / GEÇ HAREKET
        # ---------------------------------------------

        fresh_move=(
            impulse>=1.35
            and vol_ratio>=1.4
            and (
                breakout_now
                or distance<=.35
            )
            and buyer>=58
        )

        late_move=(
            (
                m5>8
                and impulse<1.15
            )
            or (
                rv>84
                and m3>4
                and distance>1
            )
        )

        # ---------------------------------------------
        # TOPLAM
        # ---------------------------------------------

        score=clamp(
            structure+
            money+
            momentum+
            breakout+
            risk
        )

        # ---------------------------------------------
        # OI YARDIMCI TEYİT
        # ---------------------------------------------

        oi_change=None

        if score>=68:

            fs=futures_symbol(s)
            now_oi=futures_oi(fs)
            old_oi=DBS.get_oi(s)

            if (
                now_oi is not None
                and old_oi is not None
            ):
                oi_change=pct(
                    old_oi,
                    now_oi
                )

                if oi_change>=.7:
                    score=clamp(score+3)

                elif oi_change<=-1.5:
                    score=clamp(score-3)

            DBS.put_oi(
                s,
                now_oi
            )

        # ---------------------------------------------
        # DURUM
        # ---------------------------------------------

        old=DBS.state(s)

        old_score=(
            float(old[1])
            if old else 0
        )

        old_stage=(
            int(old[0])
            if old else 0
        )

        # Coin zaman içinde güçleniyorsa
        # küçük devamlılık bonusu.
        if score>=old_score+5:
            score=clamp(score+3)

        preparation=(
            structure>=6
            and (
                squeeze
                or vol_ratio>=1.2
            )
        )

        strengthening=(
            money>=8
            and momentum>=10
            and (
                vol_ratio>=1.2
                or rsi_up
                or macd_up
            )
        )

        confirmed=(
            score>=78
            and money>=13
            and momentum>=12
            and breakout>=10
            and vol_ratio>=1.25
            and buyer>=58
            and not late_move
        )

        very=(
            score>=90
            and money>=17
            and momentum>=16
            and breakout>=14
            and vol_ratio>=1.5
            and impulse>=1.15
            and buyer>=62
            and not late_move
        )

        if very:
            level="VERY"
            stage=4
        elif confirmed:
            level="AL"
            stage=3
        elif strengthening:
            level="INTERNAL"
            stage=max(2,old_stage)
        elif preparation:
            level="INTERNAL"
            stage=max(1,old_stage)
        else:
            level="PASS"
            stage=0

        DBS.save_state(
            s,
            stage,
            score
        )

        if level not in ("AL","VERY"):
            return {
                "status":"PASS",
                "score":score,
                "stage":stage
            }

        reasons=[]

        if vol_ratio>=1.3:
            reasons.append(
                f"Hacim {vol_ratio:.2f}x"
            )

        if impulse>=1.25:
            reasons.append(
                f"İvme {impulse:.2f}x"
            )

        if buyer>=65:
            reasons.append(
                f"Alıcı %{buyer:.0f}"
            )

        if ema_bull:
            reasons.append("EMA9>21")

        if ema_rising:
            reasons.append("EMA yükseliyor")

        if rsi_up:
            reasons.append(
                f"RSI {rv:.0f}↑"
            )

        if macd_up:
            reasons.append(
                "MACD güçleniyor"
            )

        if trend:
            reasons.append(
                f"ADX {ad:.0f}"
            )

        if squeeze:
            reasons.append(
                "BB sıkışma"
            )

        if breakout_now:
            reasons.append(
                "Kırılım"
            )

        if higher_low:
            reasons.append(
                "Higher-Low"
            )

        if strong_close:
            reasons.append(
                "Güçlü kapanış"
            )

        if fresh_move:
            reasons.append(
                "Yeni hareket"
            )

        if oi_change is not None and oi_change>=.7:
            reasons.append(
                f"OI +{oi_change:.2f}%"
            )

        return {
            "status":level,
            "stage":stage,
            "symbol":s,
            "price":price,
            "score":score,
            "loc":loc30,
            "buyer":buyer,
            "volume":vol_ratio,
            "impulse":impulse,
            "rsi":rv,
            "ema":ema_bull,
            "macd":macd_up,
            "adx":ad,
            "squeeze":squeeze,
            "distance":distance,
            "higher_low":higher_low,
            "oi":oi_change,
            "fresh":fresh_move,
            "reasons":reasons
        }

    except Exception as e:
        log.debug(
            "%s analyze: %s",
            s,e
        )
        return {"status":"error"}
def message(r):
    very=r["status"]=="VERY"

    title=(
        "🔥 ÇOK GÜÇLÜ AL"
        if very
        else
        "🟢 AL"
    )

    conclusion=(
        "🚀 Çoklu teyit tamamlandı."
        if very
        else
        "🎯 Teknik yapı teyit aldı."
    )

    oi=(
        "—"
        if r["oi"] is None
        else f"{r['oi']:+.2f}%"
    )

    return (
        f"🐋 BALİNA RADARI V20\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"{title}\n\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 {r['price']:.8g}\n"
        f"💪 GÜÇ: {r['score']}/100\n\n"

        f"📈 EMA9/21: "
        f"{'🟢' if r['ema'] else '🔴'}\n"

        f"📊 RSI: {r['rsi']:.0f}\n"

        f"〽️ MACD: "
        f"{'🟢' if r['macd'] else '🔴'}\n"

        f"⚡ ADX: {r['adx']:.0f}\n\n"

        f"💥 Hacim: {r['volume']:.2f}x\n"
        f"🚀 Hacim ivmesi: {r['impulse']:.2f}x\n"
        f"🟢 Alıcı: %{r['buyer']:.0f}\n"
        f"🎯 Direnç: %{r['distance']:.2f}\n"

        f"📦 BB sıkışma: "
        f"{'✅' if r['squeeze'] else '—'}\n"

        f"📈 Higher-Low: "
        f"{'✅' if r['higher_low'] else '—'}\n"

        f"💰 OI: {oi}\n\n"

        f"🔎 {' • '.join(r['reasons'][:8])}\n\n"

        f"{conclusion}\n"
        f"⚠️ Teknik filtredir; risk yönetimi sana aittir."
    )


def scan():
    start=time.time()

    info=tr_exchange_info()

    if not info:
        log.warning(
            "Binance TR exchangeInfo alınamadı."
        )
        return True

    refresh_symbols(info)

    ticks=tr_tickers()

    if not ticks:
        log.warning(
            "Binance TR ticker alınamadı."
        )
        return True

    symbols=candidate_symbols(ticks)

    if not symbols:
        log.warning(
            "Uygun Binance TR paritesi bulunamadı."
        )
        return False

    signals=[]
    stats={}

    with ThreadPoolExecutor(
        max_workers=WORKERS
    ) as ex:

        jobs=[
            ex.submit(analyze,s)
            for s in symbols
        ]

        for job in as_completed(jobs):

            r=job.result()

            status=r.get(
                "status",
                "error"
            )

            stats[status]=(
                stats.get(status,0)+1
            )

            if status in (
                "AL",
                "VERY"
            ):
                signals.append(r)

    rank={
        "AL":1,
        "VERY":2
    }

    signals.sort(
        key=lambda x:(
            rank[x["status"]],
            x["score"],
            x["volume"],
            x["buyer"]
        ),
        reverse=True
    )

    sent=0

    for r in signals[:MAX_ALERTS]:

        s=r["symbol"]
        level=r["status"]

        if not DBS.can_send(
            s,
            level
        ):
            continue

        if telegram(message(r)):

            DBS.mark_alert(
                s,
                level,
                r["score"]
            )

            sent+=1

        time.sleep(.5)

    elapsed=time.time()-start

    errors=stats.get(
        "error",
        0
    )

    total=max(
        1,
        len(symbols)
    )

    log.info(
        "V20 | TR:%d | AL:%d | VERY:%d | "
        "Hata:%d | Gonder:%d | %.1fs",
        len(symbols),
        stats.get("AL",0),
        stats.get("VERY",0),
        errors,
        sent,
        elapsed
    )

    return (
        errors/total>.30
        or elapsed>SCAN_INTERVAL*1.25
    )
app=Flask(__name__)


@app.route("/")
def home():
    return "🐋 BALİNA RADARI V20 AKTİF"


@app.route("/health")
def health():
    return {
        "status":"ok",
        "bot":"Balina Radarı V20",
        "market":"Binance TR",
        "telegram":["AL","VERY"],
        "scan_interval":SCAN_INTERVAL
    }


def loop():

    log.info(
        "🐋 BALİNA RADARI V20 başlatılıyor..."
    )

    if TOKEN and CHAT:
        telegram(
            "🐋 BALİNA RADARI V20 AKTİF\n\n"
            "🇹🇷 Binance TR ana piyasa\n"
            "🔇 Hazırlık mesajları kapalı\n"
            "🔇 Takip mesajları kapalı\n"
            "🟢 AL\n"
            "🔥 ÇOK GÜÇLÜ AL"
        )

    while True:

        started=time.time()

        try:
            backoff=scan()

        except Exception:
            log.exception(
                "Tarama döngüsü hatası"
            )
            backoff=True

        elapsed=time.time()-started

        if backoff:

            wait=max(
                180,
                SCAN_INTERVAL*3
            )

            log.warning(
                "Koruma beklemesi: %ds",
                wait
            )

            time.sleep(wait)

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
    name="balina-v20"
).start()


if __name__=="__main__":
    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8080"
            )
        ),
        use_reloader=False
    )

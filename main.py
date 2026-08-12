# 1/5
import os,time,sqlite3,logging,sys
from threading import Thread,Lock
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask

MIN_VOLUME=float(os.getenv("MIN_VOLUME_USDT","1000000"))
SCAN_INTERVAL=int(os.getenv("SCAN_INTERVAL","60"))
WORKERS=int(os.getenv("MAX_WORKERS","6"))
MAX_SIGNALS=int(os.getenv("MAX_SIGNALS_PER_SCAN","3"))
COOLDOWN=int(os.getenv("SIGNAL_COOLDOWN","900"))
TIMEOUT=int(os.getenv("REQUEST_TIMEOUT","8"))
DB_PATH=os.getenv("STATE_DB_PATH","balina_v18.db")
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
log=logging.getLogger("balina-v18")

def build_session():
    kw=dict(
        total=2,connect=2,read=2,backoff_factor=.5,
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
            pool_connections=30,
            pool_maxsize=30,
            max_retries=r
        )
    )
    s.headers.update({"User-Agent":"BalinaRadari-V18/2.0"})
    return s

S=build_session()

def api(base,path,params=None):
    r=S.get(base+path,params=params,timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def telegram(text):
    if not TOKEN or not CHAT:
        return False
    try:
        r=S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id":CHAT,
                "text":text,
                "disable_web_page_preview":True
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
        path="/api/v3/ticker/24hr" if base==SPOT else "/fapi/v1/ticker/24hr"
        return api(base,path)
    except Exception as e:
        log.error("Ticker: %s",e)
        return []

def klines(base,symbol,interval,limit):
    try:
        path="/api/v3/klines" if base==SPOT else "/fapi/v1/klines"
        return api(
            base,path,
            {
                "symbol":symbol,
                "interval":interval,
                "limit":limit
            }
        )
    except Exception:
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

def pct(a,b):
    return (b-a)/a*100 if a and a>0 and b is not None else 0.0

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

    if al==0:
        return 100.0

    return 100-100/(1+ag/al)

def macd(v):
    if len(v)<40:
        return 0,0,0

    vals=[]

    for i in range(26,len(v)+1):
        vals.append(
            ema(v[:i],12)-ema(v[:i],26)
        )

    m=vals[-1]
    sig=ema(vals,9)

    return m,sig,m-sig

def bb(v,n=20,k=2):
    if len(v)<n:
        return 0,0,0

    x=v[-n:]
    m=avg(x)
    sd=(avg([(z-m)**2 for z in x]))**0.5

    return m-k*sd,m,m+k*sd

def adx(h,l,c,n=14):
    if len(c)<n*2+1:
        return 0,0,0

    tr=[]
    plus=[]
    minus=[]

    for i in range(1,len(c)):
        tr.append(
            max(
                h[i]-l[i],
                abs(h[i]-c[i-1]),
                abs(l[i]-c[i-1])
            )
        )

        up=h[i]-h[i-1]
        dn=l[i-1]-l[i]

        plus.append(up if up>dn and up>0 else 0)
        minus.append(dn if dn>up and dn>0 else 0)

    atr=avg(tr[-n:])
    p=avg(plus[-n:])
    m=avg(minus[-n:])

    if atr<=0:
        return 0,0,0

    pdi=100*p/atr
    mdi=100*m/atr
    dx=100*abs(pdi-mdi)/(pdi+mdi) if pdi+mdi else 0

    return dx,pdi,mdi
# 2/5
class DB:
    def __init__(self,path):
        self.path=path
        self.lock=Lock()

        with sqlite3.connect(path) as d:
            d.execute(
                "CREATE TABLE IF NOT EXISTS state("
                "symbol TEXT PRIMARY KEY,"
                "sent REAL,"
                "score REAL,"
                "level TEXT)"
            )
            d.execute(
                "CREATE TABLE IF NOT EXISTS oi("
                "symbol TEXT PRIMARY KEY,"
                "value REAL,"
                "ts REAL)"
            )

    def prev(self,s):
        with self.lock,sqlite3.connect(self.path) as d:
            return d.execute(
                "SELECT score,level FROM state WHERE symbol=?",
                (s,)
            ).fetchone()

    def getoi(self,s):
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

    def sent_time(self,s):
        with self.lock,sqlite3.connect(self.path) as d:
            r=d.execute(
                "SELECT sent FROM state WHERE symbol=?",
                (s,)
            ).fetchone()

        return r[0] if r else 0

    def can_send(self,s,level):
        r=self.prev(s)

        if not r:
            return True

        rank={
            "WATCH":1,
            "BUY":2,
            "VERY":3
        }

        old_rank=rank.get(r[1],0)
        new_rank=rank.get(level,0)

        if new_rank>old_rank:
            return True

        return time.time()-self.sent_time(s)>=COOLDOWN

    def sent(self,s,score,level):
        with self.lock,sqlite3.connect(self.path) as d:
            d.execute(
                "INSERT INTO state VALUES(?,?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET "
                "sent=excluded.sent,"
                "score=excluded.score,"
                "level=excluded.level",
                (s,time.time(),score,level)
            )

DBS=DB(DB_PATH)

def candidates(st,ft):
    fm={x.get("symbol"):x for x in ft}
    out=[]

    for x in st:
        s=x.get("symbol","")

        if not s.endswith("USDT"):
            continue

        if s in EXCLUDED:
            continue

        if any(
            s.endswith(z)
            for z in (
                "UPUSDT","DOWNUSDT",
                "BULLUSDT","BEARUSDT"
            )
        ):
            continue

        f=fm.get(s)

        if not f:
            continue

        try:
            sv=float(x.get("quoteVolume",0))
            fv=float(f.get("quoteVolume",0))
            chg=float(x.get("priceChangePercent",0))

            if sv<MIN_VOLUME or fv<MIN_VOLUME:
                continue

            # Çok aşırı yükselmiş coinleri tamamen silme.
            # Çünkü gerçek kırılım bazen 24s içinde zaten güçlüdür.
            if chg>40:
                continue

            out.append(s)

        except (TypeError,ValueError):
            continue

    return out
# 3/5
def analyze(s):
    try:
        sp=klines(SPOT,s,"1m",180)
        sp5=klines(SPOT,s,"5m",80)
        sp15=klines(SPOT,s,"15m",40)
        fu=klines(FUT,s,"1m",80)

        if (
            len(sp)<100 or
            len(sp5)<35 or
            len(sp15)<25 or
            len(fu)<40
        ):
            return {"status":"PASS"}

        # Son mum kapanmadığı için indikatörleri kapalı mumlardan hesapla.
        price=float(sp[-1][4])

        c=[float(x[4]) for x in sp[:-1]]
        h=[float(x[2]) for x in sp[:-1]]
        l=[float(x[3]) for x in sp[:-1]]
        o=[float(x[1]) for x in sp[:-1]]
        v=[float(x[7]) for x in sp[:-1]]
        tb=[float(x[11]) for x in sp[:-1]]

        c5=[float(x[4]) for x in sp5[:-1]]
        v5=[float(x[7]) for x in sp5[:-1]]

        c15=[float(x[4]) for x in sp15[:-1]]

        if len(c)<80 or len(c5)<25:
            return {"status":"PASS"}

        # Kısa momentum
        m1=pct(c[-2],price)
        m5=pct(c5[-2],price)
        m15=pct(c15[-2],price)

        # Aşırı hızlı mum kovalamayı engelle.
        if m1>2.0 or m5>4.0:
            return {"status":"PASS"}

        # EMA trend
        e9=ema(c,9)
        e21=ema(c,21)
        e50=ema(c,50)
        e99=ema(c,99)

        e9p=ema(c[:-3],9)
        e21p=ema(c[:-3],21)

        ema_bull=e9>e21
        ema_stack=e9>e21>e50
        ema_trend=price>e21 and price>e50
        ema_turn=e9>e9p and e9p>=e21p

        # RSI
        rv=rsi(c,14)
        rv6=rsi(c,6)
        rvp=rsi(c[:-3],14)

        rsi_up=rv>rvp
        rsi_ok=48<=rv<=70
        rsi_strong=52<=rv<=68

        # MACD
        mm,ms,mh=macd(c)
        pm,ps,ph=macd(c[:-3])

        macd_up=mh>ph
        macd_cross=mm>ms and pm<=ps
        macd_positive=mm>0

        # ADX / yön
        ad,di,mdi=adx(h,l,c)
        trend_power=ad>=17 and di>mdi

        # Bollinger
        bl,bm,bu=bb(c)
        width=(bu-bl)/bm*100 if bm else 0

        old_l,old_m,old_u=bb(c[:-8])
        old_width=(
            (old_u-old_l)/old_m*100
            if old_m else width
        )

        squeeze=width<=2.4 or (
            old_width>0 and width<old_width*.82
        )

        expanding=(
            old_width>0 and
            width>old_width*1.08
        )

        # Hacim
        avg_v=avg(v[-30:])
        avg_v5=avg(v5[-20:])

        vol1=v[-1]/avg_v if avg_v>0 else 1
        vol5=v5[-1]/avg_v5 if avg_v5>0 else 1

        buy_quote=sum(tb[-3:])
        quote=sum(v[-3:])
        buy_pct=buy_quote/quote*100 if quote>0 else 50

        # Son 20 mumun direnci.
        recent_high=max(h[-20:])
        recent_low=min(l[-20:])

        dist=max(
            0,
            (recent_high-price)/price*100
        )

        # Canlı fiyat son direnci geçti mi?
        breakout=price>recent_high*1.0005

        # Kırılım sonrası hacim teyidi
        volume_break=(
            breakout and
            (vol1>=1.35 or vol5>=1.25) and
            buy_pct>=55
        )

        # Sıkışmadan çıkış
        squeeze_break=(
            squeeze and
            price>=recent_high*.998 and
            (vol1>=1.15 or vol5>=1.15)
        )

        # Yapı
        higher_low=(
            l[-1]>l[-3] and
            l[-3]>=l[-6]
        )

        higher_high=(
            h[-1]>=h[-4] and
            c[-1]>=c[-4]
        )

        candle_body=abs(c[-1]-o[-1])
        candle_range=max(h[-1]-l[-1],price*.0001)

        strong_close=(
            c[-1]>o[-1] and
            candle_body/candle_range>=.45
        )

        # Dip artık ana şart değil.
        # Güçlü trendde dip şartı sinyali öldürmemeli.
        loc=(
            (price-recent_low)/
            (recent_high-recent_low)*100
            if recent_high>recent_low else 50
        )

        near_support=loc<=45

        # 15m trend
        e15_9=ema(c15,9)
        e15_21=ema(c15,21)
        trend15=e15_9>=e15_21

        # 1m hacim ivmesi
        prev_v=avg(v[-7:-3])
        volume_imp=(
            avg(v[-3:])/prev_v
            if prev_v>0 else 1
        )

        # OI
        oi_change=None
        oi_reason=""

        if (
            volume_break or
            squeeze_break or
            ema_stack
        ):
            now=oi(s)
            old=DBS.getoi(s)

            if old is not None and now is not None:
                oi_change=pct(old,now)

            DBS.putoi(s,now)

        # =========================
        # YENİ SKOR
        # =========================
        score=0

        # Trend: 25 puan
        if ema_bull:
            score+=5

        if ema_stack:
            score+=7

        if ema_trend:
            score+=5

        if trend15:
            score+=4

        if ema_turn:
            score+=4

        # Momentum: 20 puan
        if rsi_ok:
            score+=5

        if rsi_strong:
            score+=3

        if rsi_up:
            score+=3

        if macd_up:
            score+=5

        if macd_cross:
            score+=4

        # Hacim / para: 25 puan
        if vol1>=1.25:
            score+=5
        elif vol1>=1.10:
            score+=3

        if vol5>=1.30:
            score+=5
        elif vol5>=1.15:
            score+=3

        if buy_pct>=65:
            score+=6
        elif buy_pct>=58:
            score+=4
        elif buy_pct>=53:
            score+=2

        if volume_imp>=1.5:
            score+=5
        elif volume_imp>=1.25:
            score+=3

        # Kırılım / yapı: 25 puan
        if breakout:
            score+=10
        elif dist<=.25:
            score+=7
        elif dist<=.60:
            score+=4

        if volume_break:
            score+=8
        elif squeeze_break:
            score+=6

        if higher_low:
            score+=3

        if higher_high:
            score+=2

        if strong_close:
            score+=2

        if squeeze:
            score+=3

        if expanding:
            score+=2

        # ADX
        if trend_power:
            score+=4
        elif ad>=15:
            score+=2

        # OI
        if oi_change is not None:
            if oi_change>=.5:
                score+=3
                oi_reason="OI artıyor"
            elif oi_change<=-1.5:
                score-=3
                oi_reason="OI düşüyor"

        # Risk cezaları
        if m5>2.5:
            score-=7

        if rv>75:
            score-=6

        if (
            m5<-.8 and
            m15<-1.0 and
            not higher_low
        ):
            score-=10

        if (
            not ema_bull and
            not breakout and
            not squeeze_break
        ):
            score-=5

        # Aşırı uzak direnç bölgesinde kovalamayı azalt.
        if (
            price>recent_high and
            m1>1.2 and
            vol1<1.1
        ):
            score-=5

        score=clamp(score)

        # Önceki sinyale göre güçlenme
        prev=DBS.prev(s)

        if prev and score>=float(prev[0])+5:
            score=clamp(score+3)

        # =========================
        # SİNYAL SEVİYELERİ
        # =========================

        very=(
            score>=88 and
            ema_bull and
            ema_trend and
            rsi_strong and
            macd_up and
            (breakout or squeeze_break) and
            (volume_break or vol5>=1.30) and
            buy_pct>=58 and
            not (m5>3.0)
        )

        buy=(
            score>=78 and
            ema_trend and
            rsi_ok and
            macd_up and
            (
                volume_break or
                squeeze_break or
                (
                    dist<=.30 and
                    vol5>=1.15
                )
            ) and
            buy_pct>=55 and
            not (m5>3.5)
        )

        watch=(
            score>=68 and
            (
                dist<=.60 or
                squeeze or
                volume_imp>=1.25
            ) and
            (
                ema_bull or
                macd_up or
                rsi_up
            )
        )

        level=(
            "VERY" if very else
            "BUY" if buy else
            "WATCH" if watch else
            "PASS"
        )

        if level=="PASS":
            return {"status":"PASS","score":score}

        reasons=[]

        if ema_stack:
            reasons.append("EMA trend")

        elif ema_bull:
            reasons.append("EMA9>21")

        if rsi_up and rsi_ok:
            reasons.append(f"RSI {rv:.0f}↑")

        if macd_up:
            reasons.append("MACD↑")

        if trend_power:
            reasons.append(f"ADX {ad:.0f}")

        if vol5>=1.15:
            reasons.append(f"5m hacim {vol5:.1f}x")

        if buy_pct>=58:
            reasons.append(f"Alıcı %{buy_pct:.0f}")

        if breakout:
            reasons.append("Direnç kırıldı")

        elif dist<=.30:
            reasons.append(f"Direnç %{dist:.2f}")

        if squeeze:
            reasons.append("BB sıkışma")

        if volume_break:
            reasons.append("Hacimli kırılım")

        if higher_low:
            reasons.append("Higher-Low")

        if oi_reason:
            reasons.append(oi_reason)

        return {
            "status":level,
            "symbol":s,
            "score":score,
            "price":price,
            "loc":loc,
            "bp":buy_pct,
            "rv":rv,
            "rv6":rv6,
            "ema_bull":ema_bull,
            "ema_stack":ema_stack,
            "macd_up":macd_up,
            "macd_positive":macd_positive,
            "squeeze":squeeze,
            "expanding":expanding,
            "dist":dist,
            "breakout":breakout,
            "volume_break":volume_break,
            "vol1":vol1,
            "vol5":vol5,
            "volume_imp":volume_imp,
            "higher_low":higher_low,
            "higher_high":higher_high,
            "adx":ad,
            "oi":oi_change,
            "m1":m1,
            "m5":m5,
            "m15":m15,
            "reasons":reasons
        }

    except Exception as e:
        log.debug("%s: %s",s,e)
        return {"status":"error"}

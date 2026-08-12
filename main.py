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


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

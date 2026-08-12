
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

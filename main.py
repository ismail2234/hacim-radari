
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
MAX_SIGNALS=int(os.getenv("MAX_SIGNALS_PER_SCAN","2"))
COOLDOWN=int(os.getenv("SIGNAL_COOLDOWN","900"))
TIMEOUT=int(os.getenv("REQUEST_TIMEOUT","8"))
DB_PATH=os.getenv("STATE_DB_PATH","balina_v18.db")
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
CHAT=os.getenv("TELEGRAM_CHAT_ID","")
SPOT="https://api.binance.com"
FUT="https://fapi.binance.com"

EXCLUDED={"BTCUSDT","ETHUSDT","USDCUSDT","FDUSDUSDT","TUSDUSDT","USDPUSDT","DAIUSDT","BUSDUSDT"}

logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(message)s",stream=sys.stdout)
log=logging.getLogger("balina-v18")

def build_session():
    kw=dict(total=2,connect=2,read=2,backoff_factor=.5,status_forcelist=[429,500,502,503,504],raise_on_status=False)
    try:r=Retry(allowed_methods=["GET","POST"],**kw)
    except TypeError:r=Retry(method_whitelist=["GET","POST"],**kw)
    s=requests.Session()
    a=HTTPAdapter(pool_connections=20,pool_maxsize=20,max_retries=r)
    s.mount("https://",a)
    s.headers.update({"User-Agent":"BalinaRadari-V18/1.0"})
    return s

S=build_session()

def api(base,path,params=None):
    r=S.get(base+path,params=params,timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def telegram(text):
    if not TOKEN or not CHAT:return False
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

def tickers(base):
    try:
        return api(base,"/api/v3/ticker/24hr" if base==SPOT else "/fapi/v1/ticker/24hr")
    except Exception as e:
        log.error("Ticker: %s",e)
        return []

def klines(base,symbol,interval,limit):
    try:
        return api(
            base,
            "/api/v3/klines" if base==SPOT else "/fapi/v1/klines",
            {"symbol":symbol,"interval":interval,"limit":limit}
        )
    except Exception as e:
        log.debug("%s %s: %s",symbol,interval,e)
        return []

def oi(symbol):
    try:
        return float(api(FUT,"/fapi/v1/openInterest",{"symbol":symbol})["openInterest"])
    except Exception:
        return None

def pct(a,b):
    return (b-a)/a*100 if a and a>0 and b is not None else 0.0

def avg(v):
    return sum(v)/len(v) if v else 0.0

def clamp(x):
    return max(0,min(100,int(round(x))))

def ema(v,n):
    if len(v)<n:return avg(v)
    k=2/(n+1)
    e=avg(v[:n])
    for x in v[n:]:
        e=x*k+e*(1-k)
    return e

def rsi(v,n=14):
    if len(v)<n+1:return 50.0
    g=[];l=[]
    for i in range(1,len(v)):
        d=v[i]-v[i-1]
        g.append(max(d,0))
        l.append(max(-d,0))
    ag=avg(g[-n:])
    al=avg(l[-n:])
    return 100 if al==0 else 100-100/(1+ag/al)

def macd(v):
    if len(v)<35:return 0,0,0
    vals=[]
    for i in range(25,len(v)+1):
        vals.append(ema(v[:i],12)-ema(v[:i],26))
    m=vals[-1]
    sig=ema(vals,9)
    return m,sig,m-sig

def bb(v,n=20,k=2):
    if len(v)<n:return 0,0,0
    x=v[-n:]
    m=avg(x)
    sd=(avg([(z-m)**2 for z in x]))**.5
    return m-k*sd,m,m+k*sd

def adx(h,l,c,n=14):
    if len(c)<n*2+1:return 0,0,0
    tr=[];plus=[];minus=[]
    for i in range(1,len(c)):
        tr.append(max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])))
        up=h[i]-h[i-1]
        dn=l[i-1]-l[i]
        plus.append(up if up>dn and up>0 else 0)
        minus.append(dn if dn>up and dn>0 else 0)
    atr=avg(tr[-n:])
    p=avg(plus[-n:])
    m=avg(minus[-n:])
    if atr<=0:return 0,0,0
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
            d.execute("CREATE TABLE IF NOT EXISTS state(symbol TEXT PRIMARY KEY,sent REAL,score REAL,level TEXT)")
            d.execute("CREATE TABLE IF NOT EXISTS oi(symbol TEXT PRIMARY KEY,value REAL,ts REAL)")

    def prev(self,s):
        with self.lock,sqlite3.connect(self.path) as d:
            return d.execute(
                "SELECT score,level FROM state WHERE symbol=?",(s,)
            ).fetchone()

    def getoi(self,s):
        with self.lock,sqlite3.connect(self.path) as d:
            r=d.execute(
                "SELECT value,ts FROM oi WHERE symbol=?",(s,)
            ).fetchone()
        return None if not r or time.time()-r[1]>SCAN_INTERVAL*5 else float(r[0])

    def putoi(self,s,v):
        if v is None:return
        with self.lock,sqlite3.connect(self.path) as d:
            d.execute(
                "INSERT INTO oi VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET value=excluded.value,ts=excluded.ts",
                (s,v,time.time())
            )

    def can_send(self,s,level):
        r=self.prev(s)
        if not r:return True
        rank={"WATCH":1,"BUY":2,"VERY":3}
        return time.time()-self.sent_time(s)>=COOLDOWN or rank.get(level,0)>rank.get(r[1],0)

    def sent_time(self,s):
        with self.lock,sqlite3.connect(self.path) as d:
            r=d.execute("SELECT sent FROM state WHERE symbol=?",(s,)).fetchone()
        return r[0] if r else 0

    def sent(self,s,score,level):
        with self.lock,sqlite3.connect(self.path) as d:
            d.execute(
                "INSERT INTO state VALUES(?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET sent=excluded.sent,score=excluded.score,level=excluded.level",
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

        if any(s.endswith(z) for z in ("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")):
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

            if chg>18:
                continue

            out.append(s)

        except (TypeError,ValueError):
            continue

    return out
# 3/5
def analyze(s):
    try:
        sp=klines(SPOT,s,"1m",120)
        fu=klines(FUT,s,"1m",60)
        sp5=klines(SPOT,s,"5m",30)

        if len(sp)<70 or len(fu)<40 or len(sp5)<20:
            return {"status":"PASS"}

        live=sp[-1]
        price=float(live[4])

        c=[float(x[4]) for x in sp[:-1]]
        h=[float(x[2]) for x in sp[:-1]]
        l=[float(x[3]) for x in sp[:-1]]
        o=[float(x[1]) for x in sp[:-1]]

        c5=[float(x[4]) for x in sp5[:-1]]

        m1=pct(c[-2],price)
        m5=pct(c5[-2],price)
        m15=pct(c5[-4],price)

        if m1>1.5 or m5>3 or m15>5:
            return {"status":"PASS"}

        lo=min(l[-60:])
        hi=max(h[-60:])
        loc=(price-lo)/(hi-lo)*100 if hi>lo else 50

        dip=20 if loc<=25 else 14 if loc<=40 else 0

        base_range=(max(h[-12:])-min(l[-12:]))/price*100
        base=base_range<=1.6

        if base:
            dip+=5

        sv=[float(x[7]) for x in sp[:-1]]
        fv=[float(x[7]) for x in fu[:-1]]
        tr=[float(x[8]) for x in sp[:-1]]

        avs=avg(sv[-36:])
        avf=avg(fv[-36:])
        avt=avg(tr[-36:])

        if min(avs,avf,avt)<=0:
            return {"status":"PASS"}

        sr=avg(sv[-3:])/avs
        fr=avg(fv[-3:])/avf
        trr=avg(tr[-3:])/avt

        prevv=avg(sv[-6:-3])
        imp=avg(sv[-3:])/prevv if prevv>0 else 1

        buy=sum(float(x[11]) for x in sp[-3:])
        vol=sum(float(x[7]) for x in sp[-3:])
        bp=buy/vol*100 if vol>0 else 50

        spot_lead=sr>=1.5 and sr>=fr*1.15

        money=0

        if sr>=2.8:money+=12
        elif sr>=2.0:money+=9
        elif sr>=1.5:money+=6
        elif sr>=1.2:money+=3

        if fr>=2.2:money+=6
        elif fr>=1.5:money+=4
        elif fr>=1.2:money+=2

        if trr>=2.0:money+=5
        elif trr>=1.5:money+=3

        if bp>=72 and sr>=1.2:money+=5
        elif bp>=62 and sr>=1.2:money+=3

        if spot_lead:
            money+=4

        e9=ema(c,9)
        e21=ema(c,21)
        e50=ema(c,50)

        e9p=ema(c[:-3],9)
        e21p=ema(c[:-3],21)

        ema_up=e9>e21
        ema_turn=e9>e9p and e9p<=e21p

        rv=rsi(c)
        rvp=rsi(c[:-3])

        mm,ms,mh=macd(c)
        pm,ps,ph=macd(c[:-3])

        ad,di,mdi=adx(h,l,c)

        momentum=0

        if ema_up:
            momentum+=4

        if ema_turn or e9>e9p:
            momentum+=3

        if 38<=rv<=58 and rv>rvp:
            momentum+=5
        elif 45<rv<=64 and rv>rvp:
            momentum+=3

        if mh>ph:
            momentum+=4

        if ad>=18 and di>mdi:
            momentum+=4

        if price>=e50:
            momentum+=2

        bl,bm,bu=bb(c)
        width=(bu-bl)/bm*100 if bm else 0

        old_l,old_m,old_u=bb(c[:-5])
        pwidth=(old_u-old_l)/old_m*100 if old_m else width

        squeeze=width<=1.8 or width<pwidth*.8
        expanding=width>pwidth*1.05 if pwidth else False

        recent_high=max(h[-20:])
        dist=max(0,(recent_high-price)/price*100)

        breakout=0

        if dist<=.15:breakout+=9
        elif dist<=.35:breakout+=7
        elif dist<=.7:breakout+=4

        if squeeze:
            breakout+=4

        if expanding and imp>=1.3:
            breakout+=3

        higher_low=l[-1]>l[-3] and l[-3]>=l[-6]

        rejection=(min(o[-1],c[-1])-l[-1])>abs(c[-1]-o[-1])*.8

        if higher_low:
            breakout+=3

        if rejection:
            breakout+=2

        risk=0

        falling=m5<-.8 and m15<-1.2 and not higher_low and not rejection

        if falling:
            risk-=15

        if bp<55 and sr>=2:
            risk-=10

        if sr<1.0 and bp>=75:
            risk-=6

        if rv>72:
            risk-=8

        if m5>2:
            risk-=8

        score=clamp(dip+money+momentum+breakout+risk)

        prev=DBS.prev(s)
        progress=0

        if prev and score>=float(prev[0])+5:
            progress=4
            score=clamp(score+4)

        oi_change=None
        reasons_oi=""

        if score>=70:
            now=oi(s)
            old=DBS.getoi(s)

            if old is not None and now is not None:
                oi_change=pct(old,now)

                if oi_change>=.7:
                    score=clamp(score+3)
                    reasons_oi="OI artıyor"

                elif oi_change<=-1.5:
                    score=clamp(score-3)
                    reasons_oi="OI düşüyor"

            DBS.putoi(s,now)

        if score<68:
            return {"status":"PASS","score":score}

        very=(
            score>=92 and
            dip>=19 and
            money>=23 and
            momentum>=13 and
            breakout>=10 and
            sr>=1.8 and
            bp>=60 and
            not falling
        )

        buy=(
            score>=84 and
            dip>=14 and
            money>=17 and
            momentum>=9 and
            breakout>=7 and
            sr>=1.5 and
            bp>=60 and
            not falling
        )

        watch=(
            score>=74 and
            dip>=14 and
            money>=11 and
            breakout>=5 and
            (sr>=1.2 or bp>=65) and
            not falling
        )

        level="VERY" if very else "BUY" if buy else "WATCH" if watch else "PASS"

        reasons=[]

        if reasons_oi:
            reasons.append(reasons_oi)

        if dip>=19:
            reasons.append("Dip")

        elif dip>=14:
            reasons.append("Dip bölgesi")

        if sr>=1.5:
            reasons.append(f"Spot {sr:.2f}x")

        if spot_lead:
            reasons.append("Spot öncü")

        if bp>=65:
            reasons.append(f"Alıcı %{bp:.0f}")

        if ema_up:
            reasons.append("EMA9>21")

        if rv>rvp and 35<rv<65:
            reasons.append("RSI dönüyor")

        if mh>ph:
            reasons.append("MACD güçleniyor")

        if squeeze:
            reasons.append("BB sıkışması")

        if dist<=.35:
            reasons.append(f"Kırılıma %{dist:.2f}")

        if higher_low:
            reasons.append("Higher-Low")

        if imp>=1.4:
            reasons.append(f"Hacim ivmesi {imp:.2f}x")

        if progress:
            reasons.append("Güçleniyor")

        return {
            "status":level,
            "symbol":s,
            "score":score,
            "price":price,
            "loc":loc,
            "bp":bp,
            "sr":sr,
            "fr":fr,
            "trr":trr,
            "rv":rv,
            "ema_up":ema_up,
            "macd_up":mh>ph,
            "squeeze":squeeze,
            "dist":dist,
            "imp":imp,
            "higher_low":higher_low,
            "spot_lead":spot_lead,
            "adx":ad,
            "oi":oi_change,
            "reasons":reasons
        }

    except Exception as e:
        log.debug("%s: %s",s,e)
        return {"status":"error"}

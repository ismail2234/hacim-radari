import os,time,sqlite3,logging
from dataclasses import dataclass
from threading import Thread,Lock
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask

# ============================================================
# 🐋 BALİNA RADARI V5 — SPOT + FUTURES COMPOSITE ENGINE
# ============================================================

@dataclass(frozen=True)
class Config:
    min_volume: float=float(os.getenv("MIN_VOLUME_USDT","1000000"))
    scan_interval: int=int(os.getenv("SCAN_INTERVAL","300"))
    workers: int=int(os.getenv("MAX_WORKERS","8"))
    early: int=int(os.getenv("EARLY_SCORE","68"))
    strong: int=int(os.getenv("STRONG_SCORE","80"))
    whale: int=int(os.getenv("WHALE_SCORE","90"))
    max_signals: int=int(os.getenv("MAX_SIGNALS_PER_SCAN","5"))
    cooldown: int=int(os.getenv("SIGNAL_COOLDOWN","7200"))
    timeout: int=int(os.getenv("REQUEST_TIMEOUT","8"))
    db: str=os.getenv("STATE_DB_PATH","balina_v5.db")

CFG=Config()
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
CHAT=os.getenv("TELEGRAM_CHAT_ID","")
SPOT="https://api.binance.com"
FUT="https://fapi.binance.com"
EXCLUDED={"BTCUSDT","ETHUSDT","USDCUSDT","FDUSDUSDT","TUSDUSDT","USDPUSDT","DAIUSDT"}

logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(message)s")
log=logging.getLogger("balina-v5")

def session():
    s=requests.Session()
    r=Retry(total=3,connect=3,read=3,backoff_factor=.7,
            status_forcelist=[429,500,502,503,504],
            allowed_methods=["GET","POST"])
    a=HTTPAdapter(pool_connections=32,pool_maxsize=32,max_retries=r)
    s.mount("https://",a); s.mount("http://",a)
    s.headers.update({"User-Agent":"BalinaRadari-V5/1.0"})
    return s
S=session()

app=Flask(__name__)
@app.route("/")
def home(): return "🐋 Balina Radarı V5 Aktif!"
@app.route("/health")
def health(): return {"status":"ok","bot":"Balina Radarı V5"}

def api(base,path,params=None):
    r=S.get(base+path,params=params,timeout=CFG.timeout)
    r.raise_for_status()
    return r.json()

def telegram(text):
    if not TOKEN or not CHAT: return False
    try:
        r=S.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                 json={"chat_id":CHAT,"text":text},timeout=CFG.timeout)
        r.raise_for_status()
        return bool(r.json().get("ok"))
    except Exception as e:
        log.error("Telegram: %s",e); return False

def tickers(base):
    try:return api(base,"/api/v3/ticker/24hr" if base==SPOT else "/fapi/v1/ticker/24hr")
    except Exception as e: log.error("Ticker: %s",e); return []

def klines(base,symbol,interval,limit=70):
    try:
        return api(base,"/api/v3/klines" if base==SPOT else "/fapi/v1/klines",
                   {"symbol":symbol,"interval":interval,"limit":limit})
    except Exception as e:
        log.debug("%s %s: %s",symbol,interval,e); return []

def oi(symbol):
    try:return float(api(FUT,"/fapi/v1/openInterest",{"symbol":symbol})["openInterest"])
    except:return None

def funding(symbol):
    try:return float(api(FUT,"/fapi/v1/premiumIndex",{"symbol":symbol})["lastFundingRate"])
    except:return 0.0

def pct(a,b): return ((b-a)/a*100) if a>0 else 0.0
def ratio(a,b): return a/b if b>0 else 0.0
def clamp(x): return max(0,min(100,int(x)))

def rsi(c,n=14):
    if len(c)<n+1:return None
    g=[];l=[]
    for i in range(1,len(c)):
        d=c[i]-c[i-1];g.append(max(d,0));l.append(max(-d,0))
    ag=sum(g[:n])/n; al=sum(l[:n])/n
    for i in range(n,len(g)):
        ag=((n-1)*ag+g[i])/n;al=((n-1)*al+l[i])/n
    return 100 if al==0 else 100-100/(1+ag/al)

class DB:
    def __init__(self,p):
        self.p=p;self.lock=Lock()
        with self.lock,sqlite3.connect(p) as c:
            c.execute("CREATE TABLE IF NOT EXISTS state(symbol TEXT PRIMARY KEY,sent REAL,score REAL)")
            c.execute("CREATE TABLE IF NOT EXISTS oi(symbol TEXT PRIMARY KEY,value REAL,ts REAL)")
    def getoi(self,s):
        with self.lock,sqlite3.connect(self.p) as c:
            x=c.execute("SELECT value FROM oi WHERE symbol=?",(s,)).fetchone()
        return x[0] if x else None
    def putoi(self,s,v):
        if v is None:return
        with self.lock,sqlite3.connect(self.p) as c:
            c.execute("""INSERT INTO oi VALUES(?,?,?)
                         ON CONFLICT(symbol) DO UPDATE SET value=excluded.value,ts=excluded.ts""",
                      (s,v,time.time()))
    def cooldown(self,s):
        with self.lock,sqlite3.connect(self.p) as c:
            x=c.execute("SELECT sent FROM state WHERE symbol=?",(s,)).fetchone()
        return bool(x and time.time()-x[0]<CFG.cooldown)
    def sent(self,s,score):
        with self.lock,sqlite3.connect(self.p) as c:
            c.execute("""INSERT INTO state VALUES(?,?,?)
                         ON CONFLICT(symbol) DO UPDATE SET sent=excluded.sent,score=excluded.score""",
                      (s,time.time(),score))
DBS=DB(CFG.db)

def candidates(st,ft):
    fm={x.get("symbol"):x for x in ft}
    out=[]
    for x in st:
        s=x.get("symbol","")
        if not s.endswith("USDT") or s in EXCLUDED:continue
        if any(s.endswith(z) for z in ("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")):continue
        f=fm.get(s)
        if not f:continue
        try:
            if float(x.get("quoteVolume",0))<CFG.min_volume:continue
            if float(f.get("quoteVolume",0))<CFG.min_volume:continue
        except:continue
        out.append(s)
    return out

def analyze(s):
    try:
        sp5=klines(SPOT,s,"5m"); sp15=klines(SPOT,s,"15m"); fu5=klines(FUT,s,"5m")
        if min(len(sp5),len(sp15),len(fu5))<60:return {"status":"insufficient"}
        a=sp5[:-1]; b=sp15[:-1]; f=fu5[:-1]
        close=[float(x[4]) for x in a]; vol=[float(x[7]) for x in a]; buy=[float(x[10]) for x in a]
        fv=[float(x[7]) for x in f]; c15=[float(x[4]) for x in b]
        p=float(sp5[-1][4]); av=sum(vol[-25:-1])/24; af=sum(fv[-25:-1])/24
        if av<=0 or af<=0:return {"status":"insufficient"}
        vr=ratio(vol[-1],av); fvr=ratio(fv[-1],af)
        va=pct(sum(vol[-6:-3])/3,sum(vol[-3:])/3)
        bp=ratio(buy[-1],vol[-1])*100
        m5=pct(close[-2],close[-1]);m15=pct(close[-4],close[-1])
        m30=pct(close[-7],close[-1]);m60=pct(close[-13],close[-1])
        rv=rsi(c15)
        if rv is None:return {"status":"insufficient"}
        ema20=sum(c15[-20:])/20;ema50=sum(c15[-50:])/50
        now=oi(s);old=DBS.getoi(s);oc=pct(old,now) if old and now else 0
        fr=funding(s);DBS.putoi(s,now)
        score=0;why=[]
        def add(n,text):
            nonlocal score
            score+=n;why.append(text)
        if vr>=4:add(18,"🚀 Spot hacmi 4x+")
        elif vr>=3:add(14,"🔥 Spot hacmi 3x+")
        elif vr>=2:add(10,"📈 Spot hacmi 2x+")
        if fvr>=3:add(12,"⚡ Futures hacmi 3x+")
        elif fvr>=2:add(9,"⚡ Futures hacmi 2x+")
        elif fvr>=1.5:add(5,"📊 Futures hacmi destekliyor")
        if va>=100:add(10,"🚀 Hacim ivmesi çok güçlü")
        elif va>=50:add(7,"🔥 Hacim ivmesi yükseliyor")
        elif va>=25:add(4,"📈 Hacim ivmesi pozitif")
        if bp>=68:add(16,"🐋 Çok güçlü alıcı baskısı")
        elif bp>=63:add(12,"🟢 Güçlü alıcı baskısı")
        elif bp>=58:add(8,"🟢 Alıcı baskısı pozitif")
        elif bp>=54:add(4,"🟡 Alıcı baskısı yükseliyor")
        if oc>=8:add(18,f"🐋 OI güçlü artıyor (+%{oc:.1f})")
        elif oc>=5:add(13,f"📈 OI artıyor (+%{oc:.1f})")
        elif oc>=3:add(8,f"📊 OI yükseliyor (+%{oc:.1f})")
        structure="NEUTRAL"
        if abs(m15)<=1.5 and oc>=3:add(12,"🔮 Fiyat sakin / OI yükseliyor");structure="PRICE_FLAT_OI_UP"
        elif m15>0 and oc>0:add(7,"📈 Fiyat + OI birlikte yükseliyor");structure="PRICE_UP_OI_UP"
        elif m15<0 and oc>0:add(2,"⚠️ Fiyat düşerken OI yükseliyor");structure="PRICE_DOWN_OI_UP"
        elif m15>0 and oc<0:structure="PRICE_UP_OI_DOWN"
        if fr<-.0005:add(8,"⚡ Negatif funding / squeeze potansiyeli")
        elif fr<=.0005:add(5,"⚖️ Funding dengeli")
        elif fr>.001:score-=4;why.append("⚠️ Funding fazla pozitif")
        if .2<=m5<=2.5:add(7,"🎯 5m erken momentum")
        elif 2.5<m5<=4.5:add(4,"📈 5m momentum güçleniyor")
        elif m5>6:score-=7;why.append("⏰ 5m hareket fazla ilerledi")
        if 0<m15<4:add(6,"🎯 15m erken hareket")
        elif 4<=m15<7:add(3,"📈 15m momentum güçleniyor")
        elif m15>=10:score-=8;why.append("⏰ 15m hareket ilerledi")
        if 0<m30<7:add(4,"📈 30m kontrollü hareket")
        elif m30>=12:score-=8;why.append("⏰ 30m hareket fazla ilerledi")
        if 42<=rv<=62:add(7,"📊 RSI erken bölge")
        elif 62<rv<=70:add(3,"📊 RSI güçleniyor")
        elif rv>78:score-=10;why.append("⚠️ RSI aşırı yüksek")
        if ema20>ema50:add(6,"📈 EMA trendi yukarı")
        if vr>=2 and bp>=58 and abs(m15)<5:add(6,"🔮 Erken birikim rejimi")
        if m30>=18 or m60>=30:return {"status":"late","score":0}
        score=clamp(score)
        if score<CFG.early:return {"status":"below_score","score":score}
        typ="🚨 ÇOK GÜÇLÜ ERKEN HAREKET" if score>=CFG.whale else ("🟢 GÜÇLENEN ERKEN SİNYAL" if score>=CFG.strong else "🟡 ERKEN HAREKET UYARISI")
        return {"status":"signal","symbol":s,"type":typ,"score":score,"price":p,"vr":vr,"fvr":fvr,"va":va,
                "bp":bp,"m5":m5,"m15":m15,"m30":m30,"m60":m60,"rsi":rv,"oi":oc,"fund":fr,
                "structure":structure,"ema":ema20>ema50,"reasons":why}
    except Exception as e:
        log.debug("%s analiz: %s",s,e);return {"status":"error"}

def msg(r):
    rs="\n".join("• "+x for x in r["reasons"])
    return (f"🐋 BALİNA RADARI V5\n━━━━━━━━━━━━━━━━━━\n\n{r['type']}\n\n"
            f"🪙 #{r['symbol']}\n💰 Fiyat: {r['price']:.8g}\n🎯 SCORE: {r['score']}/100\n\n"
            f"📊 Spot Hacim: {r['vr']:.2f}x\n⚡ Futures Hacim: {r['fvr']:.2f}x\n"
            f"🚀 Hacim İvmesi: %{r['va']:.1f}\n🐋 Alıcı Baskısı: %{r['bp']:.1f}\n"
            f"🐋 OI: %{r['oi']:.2f}\n⚡ Funding: %{r['fund']*100:.4f}\n"
            f"🔗 Yapı: {r['structure']}\n\n📈 5m %{r['m5']:.2f} | 15m %{r['m15']:.2f}\n"
            f"📈 30m %{r['m30']:.2f} | 60m %{r['m60']:.2f}\n📊 RSI: {r['rsi']:.1f}\n"
            f"〽️ EMA: {'YUKARI' if r['ema'] else 'HENÜZ DÖNMEDİ'}\n\n🔎 NEDEN?\n{rs}\n\n"
            "⚠️ Erken hareket filtresidir; yatırım garantisi değildir.")

def scan():
    st=tickers(SPOT);ft=tickers(FUT)
    if not st or not ft:return
    cs=candidates(st,ft);log.info("📋 %d ortak Spot + Futures adayı",len(cs))
    stats={};signals=[]
    with ThreadPoolExecutor(max_workers=CFG.workers) as ex:
        fs=[ex.submit(analyze,s) for s in cs]
        for f in as_completed(fs):
            r=f.result();k=r.get("status","error");stats[k]=stats.get(k,0)+1
            if k=="signal":signals.append(r)
    signals.sort(key=lambda x:x["score"],reverse=True)
    sent=0
    for r in signals[:CFG.max_signals]:
        if DBS.cooldown(r["symbol"]):continue
        if telegram(msg(r)):
            DBS.sent(r["symbol"],r["score"]);sent+=1
        time.sleep(.4)
    log.info("📊 SONUÇ | Aday:%d | Sinyal:%d | 60 altı:%d | Geç:%d | Veri:%d | Hata:%d",
             len(cs),sent,stats.get("below_score",0),stats.get("late",0),stats.get("insufficient",0),stats.get("error",0))
    if signals:log.info("🏆 TOP: %s",", ".join(f"{r['symbol']}={r['score']}" for r in signals[:10]))
    else:log.info("🔕 Bu taramada sinyal yok.")

def loop():
    log.info("🐋 BALİNA RADARI V5 başlatılıyor...")
    telegram("🐋 BALİNA RADARI V5 AKTİF\n\n✅ Spot + Futures\n🐋 Open Interest\n⚡ Funding\n📊 Hacim ivmesi\n🟢 Taker baskısı\n📈 Çoklu momentum\n🎯 Composite Score")
    while True:
        t=time.time()
        try:scan()
        except Exception as e:log.exception("Tarama: %s",e)
        time.sleep(max(1,CFG.scan_interval-(time.time()-t)))

Thread(target=loop,daemon=True,name="balina-v5").start()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","8080")),use_reloader=False)


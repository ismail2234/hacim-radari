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
LONG_THRESHOLD=int(os.getenv("LONG_THRESHOLD","82"))
WATCH_THRESHOLD=int(os.getenv("WATCH_THRESHOLD","68"))
ACCUM_THRESHOLD=int(os.getenv("ACCUM_THRESHOLD","60"))
MAX_SIGNALS=int(os.getenv("MAX_SIGNALS_PER_SCAN","3"))
COOLDOWN=int(os.getenv("SIGNAL_COOLDOWN","7200"))
TIMEOUT=int(os.getenv("REQUEST_TIMEOUT","8"))
DB_PATH=os.getenv("STATE_DB_PATH","balina_v13.db")
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
CHAT=os.getenv("TELEGRAM_CHAT_ID","")
SPOT="https://api.binance.com"; FUT="https://fapi.binance.com"
EXCLUDED={"BTCUSDT","ETHUSDT","USDCUSDT","FDUSDUSDT","TUSDUSDT","USDPUSDT","DAIUSDT","BUSDUSDT"}

logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(message)s",stream=sys.stdout)
log=logging.getLogger("balina-v13")

def session():
    kw=dict(total=2,connect=2,read=2,backoff_factor=.5,status_forcelist=[429,500,502,503,504],raise_on_status=False)
    try:r=Retry(allowed_methods=["GET","POST"],**kw)
    except TypeError:r=Retry(method_whitelist=["GET","POST"],**kw)
    s=requests.Session();s.mount("https://",HTTPAdapter(pool_connections=20,pool_maxsize=20,max_retries=r));s.headers.update({"User-Agent":"BalinaRadari-V13/1.0"});return s
S=session()

def api(base,path,params=None):
    r=S.get(base+path,params=params,timeout=TIMEOUT);r.raise_for_status();return r.json()

def telegram(text):
    if not TOKEN or not CHAT:return False
    try:
        r=S.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",json={"chat_id":CHAT,"text":text},timeout=TIMEOUT);r.raise_for_status();return bool(r.json().get("ok"))
    except Exception as e:log.error("Telegram: %s",e);return False

def tickers(base):
    try:return api(base,"/api/v3/ticker/24hr" if base==SPOT else "/fapi/v1/ticker/24hr")
    except Exception as e:log.error("Ticker: %s",e);return []

def klines(base,symbol,interval,limit):
    try:return api(base,"/api/v3/klines" if base==SPOT else "/fapi/v1/klines",{"symbol":symbol,"interval":interval,"limit":limit})
    except Exception as e:log.debug("%s %s: %s",symbol,interval,e);return []

def oi(symbol):
    try:return float(api(FUT,"/fapi/v1/openInterest",{"symbol":symbol})["openInterest"])
    except Exception:return None

def pct(a,b):return (b-a)/a*100 if a and a>0 and b is not None else 0.0
def clamp(x):return max(0,min(100,int(round(x))))

class DB:
    def __init__(self,path):
        self.path=path;self.lock=Lock()
        with self.lock,sqlite3.connect(path) as d:
            d.execute("CREATE TABLE IF NOT EXISTS state(symbol TEXT PRIMARY KEY,sent REAL,score REAL)")
            d.execute("CREATE TABLE IF NOT EXISTS oi(symbol TEXT PRIMARY KEY,value REAL,ts REAL)")
    def getoi(self,s):
        with self.lock,sqlite3.connect(self.path) as d:r=d.execute("SELECT value,ts FROM oi WHERE symbol=?",(s,)).fetchone()
        return None if not r or time.time()-r[1]>SCAN_INTERVAL*5 else float(r[0])
    def putoi(self,s,v):
        if v is None:return
        with self.lock,sqlite3.connect(self.path) as d:d.execute("INSERT INTO oi VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET value=excluded.value,ts=excluded.ts",(s,v,time.time()))
    def cooldown(self,s):
        with self.lock,sqlite3.connect(self.path) as d:r=d.execute("SELECT sent FROM state WHERE symbol=?",(s,)).fetchone()
        return bool(r and time.time()-r[0]<COOLDOWN)
    def sent(self,s,score):
        with self.lock,sqlite3.connect(self.path) as d:d.execute("INSERT INTO state VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET sent=excluded.sent,score=excluded.score",(s,time.time(),score))
DBS=DB(DB_PATH)

def candidates(st,ft):
    fm={x.get("symbol"):x for x in ft};out=[]
    for x in st:
        s=x.get("symbol","")
        if not s.endswith("USDT") or s in EXCLUDED or any(s.endswith(z) for z in ("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")):continue
        f=fm.get(s)
        if not f:continue
        try:
            if float(x.get("quoteVolume",0))<MIN_VOLUME or float(f.get("quoteVolume",0))<MIN_VOLUME:continue
            if float(x.get("priceChangePercent",0))>16:continue
            out.append(s)
        except (TypeError,ValueError):continue
    return out

def analyze(s):
    try:
        sp=klines(SPOT,s,"1m",36);fu=klines(FUT,s,"1m",36);sp5=klines(SPOT,s,"5m",12)
        if len(sp)<30 or len(fu)<30 or len(sp5)<8:return {"status":"insufficient"}
        live=sp[-1];price=float(live[4]);lc=pct(float(live[1]),price)
        c5=[float(x[4]) for x in sp5];m5=pct(c5[-2],price);m15=pct(c5[-4],price)
        if lc>1.2 or m5>2.8 or m15>4.8:return {"status":"late"}
        if lc<-1.6 or m5<-3:return {"status":"weak"}

        a,b,c=sp[-2],sp[-3],sp[-4]
        a_low,b_low,c_low=float(a[3]),float(b[3]),float(c[3])
        a_high,b_high=float(a[2]),float(b[2])
        a_close,b_close=float(a[4]),float(b[4])
        higher_low=a_low>b_low and a_low>=c_low and float(live[3])>=a_low
        break_high=price>a_high
        break_zone=price>max(a_high,b_high)
        reclaim=a_close>=b_close and lc>=-0.05
        reversal=(sum([higher_low,break_high,reclaim])>=2) or break_zone

        buy3=sum(float(x[10]) for x in sp[-3:]);vol3=sum(float(x[7]) for x in sp[-3:])
        buy5=sum(float(x[10]) for x in sp[-5:]);vol5=sum(float(x[7]) for x in sp[-5:])
        bp3=buy3/vol3*100 if vol3>0 else 50;bp5=buy5/vol5*100 if vol5>0 else 50
        bp=bp3*.65+bp5*.35

        sc=sp[:-1];fc=fu[:-1]
        sv=[float(x[7]) for x in sp];fv=[float(x[7]) for x in fu];tr=[float(x[8]) for x in sp]
        avs=sum(float(x[7]) for x in sc[-18:])/18;avf=sum(float(x[7]) for x in fc[-18:])/18;avt=sum(float(x[8]) for x in sc[-18:])/18
        if min(avs,avf,avt)<=0:return {"status":"insufficient"}
        sr=sum(sv[-3:])/3/avs;fr=sum(fv[-3:])/3/avf;trr=sum(tr[-3:])/3/avt
        prev=sum(sv[-6:-3])/3;acc=sum(sv[-3:])/3/prev if prev>0 else 1
        score=0;reasons=[]
        if sr>=4:score+=17;reasons.append(f"🔥 Spot akışı güçlü ({sr:.2f}x)")
        elif sr>=2.5:score+=12;reasons.append(f"📈 Spot akışı artıyor ({sr:.2f}x)")
        elif sr>=2:score+=8;reasons.append(f"📊 Spot hacmi destekliyor ({sr:.2f}x)")
        if fr>=4:score+=15;reasons.append(f"⚡ Futures akışı güçlü ({fr:.2f}x)")
        elif fr>=2.5:score+=11;reasons.append(f"⚡ Futures aktivitesi artıyor ({fr:.2f}x)")
        elif fr>=2:score+=7;reasons.append(f"📊 Futures destekliyor ({fr:.2f}x)")
        if trr>=3:score+=15;reasons.append(f"🤖 İşlem sayısı patlıyor ({trr:.2f}x)")
        elif trr>=2:score+=12;reasons.append(f"📈 İşlem sayısı güçlü ({trr:.2f}x)")
        elif trr>=1.5:score+=7;reasons.append(f"📊 İşlem sayısı artıyor ({trr:.2f}x)")
        if bp>=80:score+=17;reasons.append(f"🐋 Sürekli alıcı baskısı (%{bp:.1f})")
        elif bp>=70:score+=14;reasons.append(f"🟢 Güçlü alıcı baskısı (%{bp:.1f})")
        elif bp>=62:score+=9;reasons.append(f"🟢 Pozitif alıcı baskısı (%{bp:.1f})")
        if sr>=2.5 and sr>=fr*.9:score+=5;reasons.append("🐋 Spot akışı öncülük ediyor")
        if .02<=lc<=.55:score+=10;reasons.append(f"🎯 Fiyat hâlâ erken (+%{lc:.2f})")
        elif -.05<=lc<.02 and bp>=70:score+=8;reasons.append(f"🎯 Fiyat sıkışık, alım korunuyor (%{lc:.2f})")
        elif .55<lc<=1.2:score+=4;reasons.append(f"📈 Hareket başladı (+%{lc:.2f})")
        if .05<=m5<=1.5:score+=8;reasons.append(f"🎯 5m momentum sağlıklı (+%{m5:.2f})")
        elif -.25<=m5<.05 and bp>=72:score+=4;reasons.append(f"📊 5m sıkışma/birikim ({m5:+.2f}%)")
        if acc>=4:score+=6;reasons.append(f"🚀 Hacim ivmesi güçlü ({acc:.2f}x)")
        elif acc>=2:score+=4;reasons.append(f"🔥 Hacim ivmesi artıyor ({acc:.2f}x)")
        elif acc>=1.5:score+=2;reasons.append(f"📈 Hacim ivmesi pozitif ({acc:.2f}x)")
        if higher_low:score+=8;reasons.append("📐 Higher-Low oluştu")
        if break_high:score+=9;reasons.append("💥 Önceki 1m tepe kırıldı")
        if break_zone:score+=5;reasons.append("💥 Kısa vadeli tepe bölgesi aşıldı")

        has_flow=(sr>=2 or fr>=2) and trr>=1.5
        has_bp=bp>=62
        distribution=bp<60 and sr>=3 and m5<-.5
        falling=m5<-.8 and m15<-1 and not reversal

        oi_change=None
        if score>=WATCH_THRESHOLD-4:
            now=oi(s);old=DBS.getoi(s)
            if old is not None and now is not None:
                oi_change=pct(old,now)
                if oi_change>=.8:score+=7;reasons.append(f"📈 OI destekli (+%{oi_change:.2f})")
                elif oi_change<=-1:score-=3;reasons.append(f"⚠️ OI geriliyor (%{oi_change:.2f})")
            DBS.putoi(s,now)
        score=clamp(score)
        confirms=sum([has_flow,has_bp,reversal])

        data=dict(symbol=s,score=score,price=price,sr=sr,fr=fr,tr=trr,bp=bp,lc=lc,m5=m5,m15=m15,acc=acc,oi=oi_change,reasons=reasons)

        if score>=LONG_THRESHOLD and confirms==3 and not distribution and not falling:
            data["status"]="LONG";data["type"]="🟢 EARLY LONG";return data
        if score>=WATCH_THRESHOLD and (sr>=2 or fr>=2) and has_bp:
            if not reversal:reasons.append("⏳ Akış güçlü; fiyat dönüş/kırılım teyidi bekleniyor")
            if falling:reasons.append("⚠️ 5m/15m hâlâ aşağı; LONG için dönüş gerekli")
            data["status"]="WATCH";data["type"]="🟡 WATCH";return data
        if score>=ACCUM_THRESHOLD and sr>=2 and has_bp and not distribution and lc<=.6 and m5<=.8:
            reasons.append("🔵 Birikim bölgesi: henüz LONG tetiklenmedi")
            data["status"]="ACCUM";data["type"]="🔵 ACCUMULATION";return data
        return {"status":"PASS","score":score}
    except Exception as e:
        log.debug("%s analiz: %s",s,e);return {"status":"error"}

def message(r):
    oi_txt="veri yok" if r["oi"] is None else f"%{r['oi']:.2f}"
    if r["status"]=="LONG": footer="🎯 Akış + alıcı + fiyat dönüşü teyit edildi."
    elif r["status"]=="WATCH": footer="👁️ Akış güçlü; fiyat tetikleyicisi bekleniyor."
    else: footer="🔵 Birikim izleniyor; henüz LONG tetiklenmedi."
    return (f"🐋 BALİNA RADARI V13 — {r['type']}\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🪙 #{r['symbol']}\n💰 Fiyat: {r['price']:.8g}\n🏆 SCORE: {r['score']}/100\n\n"
            f"📊 AKIŞ\n• Spot hacim: {r['sr']:.2f}x\n• Futures hacim: {r['fr']:.2f}x\n• İşlem sayısı: {r['tr']:.2f}x\n• Alıcı baskısı: %{r['bp']:.1f}\n\n"
            f"🚀 ERKENLİK\n• 1m: {r['lc']:+.2f}%\n• 5m: {r['m5']:+.2f}%\n• 15m: {r['m15']:+.2f}%\n• Hacim ivmesi: {r['acc']:.2f}x\n• OI: {oi_txt}\n\n"
            "🔎 TEYİTLER\n"+"\n".join("• "+x for x in r["reasons"])+f"\n\n{footer}\n⚠️ Teknik filtredir; kâr garantisi değildir.")

def scan():
    start=time.time();st=tickers(SPOT);ft=tickers(FUT)
    if not st or not ft:return True
    syms=candidates(st,ft);res=[];stats={}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        jobs=[ex.submit(analyze,s) for s in syms]
        for j in as_completed(jobs):
            r=j.result();k=r.get("status","error");stats[k]=stats.get(k,0)+1
            if k in ("LONG","WATCH","ACCUM"):res.append(r)
    pri={"LONG":3,"WATCH":2,"ACCUM":1};res.sort(key=lambda x:(pri.get(x["status"],0),x["score"]),reverse=True)
    sent=0
    for r in res[:MAX_SIGNALS]:
        if DBS.cooldown(r["symbol"]):continue
        if telegram(message(r)):DBS.sent(r["symbol"],r["score"]);sent+=1
        time.sleep(.5)
    elapsed=time.time()-start;err=stats.get("error",0);total=max(1,len(syms))
    log.info("🐋 V13 | Aday:%d | LONG:%d | WATCH:%d | ACCUM:%d | Geç:%d | Hata:%d | Gönder:%d | %.1fs",
             len(syms),stats.get("LONG",0),stats.get("WATCH",0),stats.get("ACCUM",0),stats.get("late",0),err,sent,elapsed)
    return err/total>.30 or elapsed>SCAN_INTERVAL*1.25

app=Flask(__name__)
@app.route("/")
def home():return "🐋 Balina Radarı V13 Accumulation Trigger Aktif!"
@app.route("/health")
def health():return {"status":"ok","bot":"Balina Radarı V13","long_threshold":LONG_THRESHOLD,"watch_threshold":WATCH_THRESHOLD,"accum_threshold":ACCUM_THRESHOLD}

def loop():
    log.info("🐋 BALİNA RADARI V13 başlatılıyor...")
    if TOKEN and CHAT:
        telegram("🐋 BALİNA RADARI V13 AKTİF\n\n🔵 Birikim tespiti\n🟡 WATCH → tetik bekleme\n🟢 Erken LONG\n📐 Higher-Low + tepe kırılımı\n📊 Spot/Futures/Trade Flow\n🐋 Yumuşatılmış alıcı baskısı\n🚫 Geç kalmış hareket filtresi\n🛡️ Rate-limit koruması")
    while True:
        t=time.time()
        try:backoff=scan()
        except Exception:log.exception("Tarama döngüsü hatası");backoff=True
        elapsed=time.time()-t
        if backoff:time.sleep(max(180,SCAN_INTERVAL*3))
        else:time.sleep(max(1,SCAN_INTERVAL-elapsed))

Thread(target=loop,daemon=True,name="balina-v13").start()
if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","8080")),use_reloader=False)
                                                                                                                                                        

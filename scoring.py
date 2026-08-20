from indicators import (
    adx, fibonacci, fibonacci_distance, ichimoku,
    macd, rsi, td_sequential, volume_profile,
    volume_ratio, vwap,
)
from kivrim import analyze_kivrim

BAD_SYMBOLS = {
    "USDTTRY","USDCTRY","BUSDTRY",
    "FDUSDTRY","TUSDTRY","DAITRY",
}


def num(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


def klines(client, symbol, limit):
    try:
        return client.klines(symbol, "5m", limit)
    except Exception:
        try:
            return client.klines(
                symbol=symbol,
                interval="5m",
                limit=limit,
            )
        except Exception:
            return []


def parse(data):
    h,l,c,v=[],[],[],[]
    for x in data:
        try:
            if isinstance(x,dict):
                h.append(num(x.get("high",x.get("h",0))))
                l.append(num(x.get("low",x.get("l",0))))
                c.append(num(x.get("close",x.get("c",0))))
                v.append(num(x.get("volume",x.get("v",0))))
            else:
                h.append(num(x[2]))
                l.append(num(x[3]))
                c.append(num(x[4]))
                v.append(num(x[5]))
        except Exception:
            continue
    return h,l,c,v


def safe_rsi(x):
    try:
        return num(rsi(x),50)
    except Exception:
        return 50.0


def streak(dbs,symbol):
    try:
        old=dbs.get_last_signal(symbol)
        if not old:
            return 1
        return min(int(old.get("streak",0) or 0)+1,9)
    except Exception:
        return 1


def analyze(cfg,client,dbs,market,item):

    symbol=str(item.get("symbol","")).upper()

    if not symbol or symbol in BAD_SYMBOLS:
        return None

    data=klines(
        client,
        symbol,
        int(getattr(cfg,"candles",300)),
    )

    if not data:
        return None

    highs,lows,closes,volumes=parse(data)

    if len(closes)<150:
        return None

    price=closes[-1]

    if price<=0:
        return None

    # =====================================================
    # KIVRIM
    # =====================================================

    k=analyze_kivrim(
        highs,lows,closes,volumes
    )

    if not k.get("valid",False):
        return None

    ks=int(k.get("score",0) or 0)
    ke=int(k.get("early_score",0) or 0)
    stage=str(k.get("stage","BEKLE"))

    turn=bool(k.get("ema7_turning",False))
    pre=bool(k.get("ema7_pre_turn",False))
    accel=bool(k.get("ema7_accelerating",False))
    higher=bool(k.get("higher_low",False))
    krsi=bool(k.get("rsi_turning",False))
    kmacd=bool(k.get("macd_recovering",False))
    kvol=bool(k.get("volume_start",False))
    reclaim=bool(k.get("reclaim_ema7",False))

    # =====================================================
    # SON HAREKET
    # =====================================================

    move3=(price-closes[-4])/closes[-4]*100
    move6=(price-closes[-7])/closes[-7]*100
    move12=(price-closes[-13])/closes[-13]*100

    low12=min(lows[-12:])
    low20=min(lows[-20:])

    from_low12=(price-low12)/low12*100
    from_low20=(price-low20)/low20*100

    high20=max(highs[-20:])
    high_distance=(high20-price)/price*100

    # =====================================================
    # YENİ GENİŞ HAREKET FİLTRESİ
    # Yaklaşık 24 saatlik 5 dk mumları.
    # 288 mum = 24 saat.
    # =====================================================

    wide_low=min(lows[-288:])
    wide_high=max(highs[-288:])

    wide_from_low=(
        (price-wide_low)/wide_low*100
        if wide_low>0 else 999
    )

    wide_from_high=(
        (wide_high-price)/price*100
        if price>0 else 0
    )

    # Dipten %15+ uzaklaşmışsa artık erken değil.
    wide_late=wide_from_low>=15

    # 24 saatlik zirveye %6 veya daha yakınsa yeni sinyal yok.
    wide_top=wide_from_high<=6

    # =====================================================
    # RSI
    # =====================================================

    rv=safe_rsi(closes)
    rv_prev=safe_rsi(closes[:-1])

    rsi_rising=rv>rv_prev
    rsi_early=35<=rv<=60

    # =====================================================
    # HACİM
    # =====================================================

    try:
        vr=num(volume_ratio(volumes))
    except Exception:
        vr=0.0

    volume_early=1.15<=vr<=3.00
    volume_strong=1.40<=vr<=2.80

    # =====================================================
    # MACD
    # =====================================================

    try:
        md=macd(closes)
    except Exception:
        md=()

    ml=ms=mh=0.0

    if isinstance(md,(tuple,list)):
        if len(md)>0: ml=num(md[0])
        if len(md)>1: ms=num(md[1])
        if len(md)>2: mh=num(md[2])

    macd_ok=ml>ms

    # =====================================================
    # ADX
    # =====================================================

    try:
        ad=adx(highs,lows,closes)
    except Exception:
        ad=()

    adval=plus=minus=0.0

    if isinstance(ad,(tuple,list)):
        if len(ad)>0: adval=num(ad[0])
        if len(ad)>1: plus=num(ad[1])
        if len(ad)>2: minus=num(ad[2])

    adx_ok=adval>=18
    di_ok=plus>minus

    # =====================================================
    # VWAP
    # =====================================================

    try:
        vw=num(vwap(
            highs,lows,closes,volumes
        ))
    except Exception:
        vw=0.0

    above_vwap=vw>0 and price>=vw

    vwap_reclaim=(
        vw>0
        and closes[-2]<vw
        and closes[-1]>=vw
    )

    # =====================================================
    # FIB
    # =====================================================

    try:
        fib=fibonacci(
            highs,lows,closes
        )
    except Exception:
        fib={}

    f50=num(fib.get("0.5",0))
    f618=num(fib.get("0.618",0))
    f786=num(fib.get("0.786",0))

    levels=[x for x in (f50,f618,f786) if x>0]

    fib_dist=999

    if levels:
        nearest=min(
            levels,
            key=lambda x:abs(price-x)
        )
        try:
            fib_dist=num(
                fibonacci_distance(
                    price,nearest
                ),999
            )
        except Exception:
            pass

    fib_zone=fib_dist<=1

    # =====================================================
    # VOLUME PROFILE
    # =====================================================

    try:
        vp=volume_profile(
            highs,lows,closes,volumes,
            50,70
        )
    except Exception:
        vp={}

    poc=num(vp.get("poc",0))
    va_low=num(vp.get("value_low",0))
    va_high=num(vp.get("value_high",0))

    poc_near=(
        poc>0
        and abs(price-poc)/price*100<=1
    )

    fib_poc=fib_zone and poc_near

    # =====================================================
    # ICHIMOKU
    # =====================================================

    try:
        ichi=ichimoku(
            highs,lows,closes,
            20,60,120,30
        )
    except Exception:
        ichi={}

    ich_bull=bool(
        ichi.get("bullish",False)
    )

    above_cloud=bool(
        ichi.get("above_cloud",False)
    )

    # =====================================================
    # TD
    # =====================================================

    try:
        tddata=td_sequential(closes)
    except Exception:
        tddata={}

    td=int(
        tddata.get("setup",0) or 0
    )

    td_direction=tddata.get(
        "direction",""
    )

    # =====================================================
    # GEÇ KALMA KAPISI
    # =====================================================

    late=False

    if move3>=3:
        late=True

    if move6>=5:
        late=True

    if move12>=8:
        late=True

    if from_low12>=8:
        late=True

    if from_low20>=10:
        late=True

    if high_distance<=2:
        late=True

    if rv>=65:
        late=True

    if vr>=3:
        late=True

    # YENİ GENİŞ FİLTRE
    if wide_late:
        late=True

    if wide_top:
        late=True

    # =====================================================
    # ERKEN KIVRIM
    # =====================================================

    momentum_ok=krsi or kmacd
    structure_ok=higher or reclaim

    early_gate=(
        not late
        and (pre or turn)
        and momentum_ok
        and volume_early
        and structure_ok
    )

    very_early=(
        not late
        and pre
        and krsi
        and kmacd
        and volume_early
        and higher
        and move3<2.5
        and move6<4
        and wide_from_low<12
    )

    # =====================================================
    # SKOR
    # =====================================================

    score=0
    criteria=[]

    def add(ok,pts,text):
        nonlocal score
        if ok:
            score+=pts
            criteria.append(text)

    add(pre,14,"PRE-KIVRIM")
    add(turn,20,"EMA7 KIVRIM")
    add(accel,6,"KIVRIM İVMESİ")
    add(higher,10,"HIGHER-LOW")
    add(krsi,8,"RSI DÖNÜŞÜ")
    add(kmacd,8,"MACD DÖNÜŞÜ")
    add(volume_early,9,"İLK HACİM")
    add(reclaim,8,"EMA7 GERİ ALIM")

    if early_gate:
        score+=8
        criteria.append("ERKEN KIVRIM")

    if very_early:
        score+=8
        criteria.append("1-3 MUM ÖNCESİ")

    add(rsi_rising,3,"RSI YÜKSELİYOR")
    add(rsi_early,3,"RSI ERKEN BÖLGE")
    add(macd_ok,5,"MACD")
    add(adx_ok,3,"ADX")
    add(di_ok,3,"+DI")
    add(above_vwap,4,"VWAP")
    add(vwap_reclaim,5,"VWAP GERİ ALIM")
    add(volume_strong,3,"HACİM GÜÇLÜ")
    add(fib_zone,3,"FIB")
    add(poc_near,3,"POC")
    add(fib_poc,5,"FIB + POC")
    add(ich_bull,2,"ICHIMOKU")
    add(above_cloud,2,"BULUT ÜSTÜ")

    if td>=13:
        score+=3
        criteria.append("TD 13")
    elif td>=9:
        score+=2
        criteria.append("TD 9")

    # Geç hareket cezası
    if late:
        score-=30

    score=max(
        0,min(100,score)
    )

    # =====================================================
    # EARLY SCORE
    # =====================================================

    early_score=ke

    if early_gate:
        early_score+=8

    if very_early:
        early_score+=10

    if move3>=3:
        early_score-=15

    if move6>=5:
        early_score-=15

    if move12>=8:
        early_score-=20

    if from_low12>=8:
        early_score-=20

    if high_distance<=2:
        early_score-=20

    if rv>=65:
        early_score-=20

    if vr>=3:
        early_score-=20

    # YENİ GENİŞ HAREKET CEZASI
    if wide_late:
        early_score-=30

    if wide_top:
        early_score-=20

    early_score=max(
        0,min(100,early_score)
    )

    # =====================================================
    # STATUS
    # =====================================================

    if late:
        status="PASS"

    elif (
        very_early
        and early_score>=68
        and score>=62
    ):
        status="VERY"

    elif (
        early_gate
        and early_score>=62
        and score>=58
    ):
        status="BUY"

    elif (
        early_gate
        and early_score>=58
        and score>=55
    ):
        status="ONCU"

    else:
        status="PASS"

    # =====================================================
    # STOP
    # =====================================================

    stop=min(lows[-12:])*0.995

    if stop<=0 or stop>=price:
        stop=price*0.98

    stop_distance=(
        (price-stop)/price*100
    )

    return {
        "symbol":symbol,
        "price":price,
        "status":status,
        "score":score,
        "priority":score,
        "streak":streak(dbs,symbol),

        "kivrim_score":ks,
        "kivrim_early_score":early_score,
        "kivrim_stage":stage,

        "kivrim_turning":turn,
        "kivrim_pre_turn":pre,
        "kivrim_accelerating":accel,
        "kivrim_higher_low":higher,

        "kivrim_rsi_turning":krsi,
        "kivrim_macd_recovering":kmacd,
        "kivrim_volume_start":kvol,
        "kivrim_reclaim_ema7":reclaim,

        "kivrim_reasons":k.get(
            "reasons",[]
        ),
        "kivrim_reasons_text":k.get(
            "reasons_text",""
        ),

        "ichimoku_bullish":ich_bull,
        "bullish":ich_bull,
        "above_cloud":above_cloud,

        "fib_0_5":f50,
        "fib_0_618":f618,
        "fib_0_786":f786,
        "fib50":f50,
        "fib618":f618,
        "fib786":f786,
        "fib_zone":fib_zone,
        "fib_poc":fib_poc,

        "poc":poc,
        "value_low":va_low,
        "value_high":va_high,
        "va_low":va_low,
        "va_high":va_high,

        "td":td,
        "td_setup":td,
        "td_direction":td_direction,

        "volume_ratio":vr,
        "vr":vr,
        "rsi":rv,
        "rv":rv,
        "macd":macd_ok,
        "macd_hist":mh,
        "adx":adval,
        "ad":adval,
        "price_above_vwap":above_vwap,
        "vwap":vw,

        "stop_loss":stop,
        "stop":stop,
        "stop_distance":stop_distance,
        "trap":late,

        "criteria":criteria,
        "criteria_list":criteria,

        "move_3":move3,
        "move_6":move6,
        "move_12":move12,

        "from_low_12":from_low12,
        "from_low_20":from_low20,

        "wide_from_low":wide_from_low,
        "wide_from_high":wide_from_high,

        "high_distance":high_distance,
        "late":late,
    }


def rank_signals(signals,cfg=None):

    order={
        "VERY":3,
        "BUY":2,
        "ONCU":1,
        "PASS":0,
    }

    return sorted(
        signals,
        key=lambda x:(
            order.get(
                str(x.get("status","PASS")),
                0
            ),
            num(
                x.get(
                    "kivrim_early_score",0
                )
            ),
            num(
                x.get(
                    "kivrim_score",0
                )
            ),
            num(x.get("score",0)),
        ),
        reverse=True,
    )

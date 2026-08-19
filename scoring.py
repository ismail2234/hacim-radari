from indicators import adx, fibonacci, fibonacci_distance, ichimoku, macd, rsi, td_sequential, volume_profile, volume_ratio, vwap

BAD_SYMBOLS={"USDTTRY","USDCTRY","BUSDTRY","FDUSDTRY","TUSDTRY","DAITRY"}

def num(v,d=0.0):
    try:
        v=float(v)
        return v if v==v else d
    except:
        return d

def get_klines(client,symbol,limit):
    try:
        return client.klines(symbol,"5m",limit)
    except:
        try:
            return client.klines(symbol=symbol,interval="5m",limit=limit)
        except:
            return []

def parse_data(data):
    h=[];l=[];c=[];v=[]
    for x in data:
        try:
            if isinstance(x,dict):
                hi=num(x.get("high",x.get("h",0)))
                lo=num(x.get("low",x.get("l",0)))
                cl=num(x.get("close",x.get("c",0)))
                vo=num(x.get("volume",x.get("v",0)))
            else:
                hi=num(x[2]);lo=num(x[3]);cl=num(x[4]);vo=num(x[5])
            if hi>0 and lo>0 and cl>0 and vo>=0:
                h.append(hi);l.append(lo);c.append(cl);v.append(vo)
        except:
            pass
    return h,l,c,v

def analyze(cfg,client,dbs,market,item):
    symbol=str(item.get("symbol","")).upper()
    if not symbol or symbol in BAD_SYMBOLS:
        return None

    data=get_klines(client,symbol,int(getattr(cfg,"candles",300)))
    if not data:
        return None

    highs,lows,closes,volumes=parse_data(data)
    if len(closes)<150 or len(set(closes[-20:]))<2:
        return None

    price=closes[-1]
    if price<=0 or volumes[-1]<=0:
        return None

    ichi=ichimoku(highs,lows,closes,20,60,120,30)
    ichi_bull=bool(ichi.get("bullish",False))
    above_cloud=bool(ichi.get("above_cloud",False))

    fib=fibonacci(highs,lows,closes)
    f50=num(fib.get("0.5"))
    f618=num(fib.get("0.618"))
    f786=num(fib.get("0.786"))
    levels=[x for x in (f50,f618,f786) if x>0]

    nearest=min(levels,key=lambda x:abs(price-x)) if levels else 0
    fd=fibonacci_distance(price,nearest) if nearest else 999
    fib_zone=fd<=1.0

    profile=volume_profile(highs,lows,closes,volumes,50,70)
    poc=num(profile.get("poc"))
    va_low=num(profile.get("value_low"))
    va_high=num(profile.get("value_high"))
    poc_dist=abs(price-poc)/price*100 if poc>0 else 999
    poc_near=poc_dist<=1.0
    fib_poc=fib_zone and poc_near

    td=td_sequential(closes)
    td_setup=int(td.get("setup",0) or 0)
    td_9=td_setup>=9
    td_13=td_setup>=13

    vr=num(volume_ratio(volumes))
    volume_ok=1.15<=vr<=3.5
    strong_volume=1.5<=vr<=3.5

    rv=num(rsi(closes))
    rsi_prev=num(rsi(closes[:-1]))
    rsi_rising=rv>rsi_prev
    rsi_ok=45<=rv<=65

    md=macd(closes)
    ml=num(md[0]) if len(md)>0 else 0
    ms=num(md[1]) if len(md)>1 else 0
    mh=num(md[2]) if len(md)>2 else 0
    macd_ok=ml>ms and mh>=0

    ad=adx(highs,lows,closes)
    av=num(ad[0]) if len(ad)>0 else 0
    plus=num(ad[1]) if len(ad)>1 else 0
    minus=num(ad[2]) if len(ad)>2 else 0
    adx_ok=av>=18 and av<=45
    di_ok=plus>minus

    vw=num(vwap(highs,lows,closes,volumes))
    above_vwap=vw>0 and price>=vw

    prev_vol=sum(volumes[-6:-1])/5 if len(volumes)>=6 else 0
    impulse=volumes[-1]/prev_vol if prev_vol>0 else 0

    recent_high=max(highs[-20:])
    distance_high=(recent_high-price)/price*100 if price>0 else 999
    not_chased=distance_high>=0 and distance_high<=8

    score=0
    criteria=[]

    if ichi_bull:
        score+=15
        criteria.append("Ichimoku")
    if above_cloud:
        score+=8
        criteria.append("Bulut üstü")
    if fib_zone:
        score+=12
        criteria.append("Fib")
    if poc_near:
        score+=10
        criteria.append("POC")
    if fib_poc:
        score+=12
        criteria.append("Fib+POC")
    if volume_ok:
        score+=10
        criteria.append(f"Hacim {vr:.1f}x")
    if strong_volume:
        score+=5
    if rsi_ok:
        score+=7
        criteria.append("RSI")
    if rsi_rising:
        score+=4
        criteria.append("RSI yükseliyor")
    if macd_ok:
        score+=8
        criteria.append("MACD")
    if adx_ok:
        score+=5
        criteria.append("ADX")
    if di_ok:
        score+=5
        criteria.append("+DI")
    if above_vwap:
        score+=6
        criteria.append("VWAP")
    if not_chased:
        score+=5
        criteria.append("Erken bölge")

    score=min(score,100)

    core=(
        volume_ok
        and rsi_ok
        and macd_ok
        and adx_ok
        and di_ok
        and above_vwap
    )

    if not core:
        status="PASS"
    elif fib_poc and td_13 and strong_volume and score>=80:
        status="VERY"
    elif fib_poc and (td_9 or strong_volume) and score>=72:
        status="BUY"
    elif (
        ichi_bull
        and fib_zone
        and poc_near
        and above_vwap
        and macd_ok
        and score>=65
        and not_chased
    ):
        status="ONCU"
    else:
        status="PASS"

    stop=f786 if f786>0 and f786<price else price*0.99
    stop_distance=abs(price-stop)/price*100

    priority=score
    if fib_poc: priority+=5
    if td_9: priority+=3
    if td_13: priority+=5
    priority=min(priority,100)

    entry=min(
        100,
        score
        +(5 if fib_poc else 0)
        +(4 if strong_volume else 0)
        +(3 if macd_ok else 0)
    )

    return {
        "symbol":symbol,
        "status":status,
        "price":price,
        "score":score,
        "priority":priority,
        "entry_quality":entry,
        "ichimoku_bullish":ichi_bull,
        "above_cloud":above_cloud,
        "fib_0_5":f50,
        "fib_0_618":f618,
        "fib_0_786":f786,
        "fib_zone":fib_zone,
        "poc":poc,
        "va_low":va_low,
        "va_high":va_high,
        "poc_distance":poc_dist,
        "fib_poc":fib_poc,
        "td_setup":td_setup,
        "td_direction":td.get("direction",""),
        "td_9":td_9,
        "td_13":td_13,
        "volume_ratio":vr,
        "impulse":impulse,
        "rsi":rv,
        "rsi_rising":rsi_rising,
        "macd":macd_ok,
        "macd_line":ml,
        "macd_signal":ms,
        "macd_hist":mh,
        "adx":av,
        "plus_di":plus,
        "minus_di":minus,
        "adx_ok":adx_ok,
        "di_ok":di_ok,
        "vwap":vw,
        "price_above_vwap":above_vwap,
        "stop":stop,
        "stop_loss":stop,
        "stop_distance":stop_distance,
        "criteria":criteria,
        "criteria_list":criteria,
        "streak":1,
        "previous_signal":"İlk sinyal",
    }

def rank_signals(signals,cfg=None):
    valid=[x for x in signals if x.get("status") in ("ONCU","BUY","VERY")]
    return sorted(
        valid,
        key=lambda x:
        float(x.get("score",0))
        +float(x.get("priority",0))*0.25
        +float(x.get("entry_quality",0))*0.25,
        reverse=True,
    )

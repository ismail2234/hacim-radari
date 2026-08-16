"""
Eski koddaki `analyze()` tek fonksiyonda ~250 satırdı: 5m/1m veri çekme,
indikatör hesaplama, trap tespiti, setup/confirmation/penalty/entry
puanlama ve stage kararı hepsi iç içeydi. Sonuç: hiçbir alt parça tek
başına test edilemiyordu.

Burada aynı MANTIK korunarak (davranış değiştirilmedi) adımlar ayrı, saf
fonksiyonlara bölündü. `analyze()` artık sadece bu adımları sırayla
çağıran bir orkestratör -- indikatör hesaplama, puanlama ve stage kararı
birbirinden bağımsız test edilebilir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from binance_client import BinanceClient
from config import Settings
from db import DB
from indicators import (
    adx, atr, avg, bb, bullish_divergence, clamp, ema, keltner_channel,
    macd, macd_hist_series, obv, pct, rsi, soft_cap, vwap,
)
from market import MarketData

log = logging.getLogger("balina.scoring")


@dataclass
class Features:
    price: float
    momentum1: float
    momentum5: float
    location: float
    close_position: float
    vr: float
    vr5: float
    impulse: float
    bp: float
    trades1: int
    trades5: int
    ema_up: bool
    ema_cross: bool
    price_above_ema50: bool
    rv: float
    old_rsi: float
    macd_up: bool
    ad: float
    plus_di: float
    minus_di: float
    squeeze: bool
    expanding: bool
    dist: float
    breakout: bool
    closed_breakout: bool
    higher_low: bool
    low_activity: bool
    weak_volume: bool
    trap: bool
    # YENİ (Faz B / ÖNCÜ AL): erken hareket tespiti için "yön/ivme" özellikleri.
    # Bunlar "şu an ne kadar yüksek" değil "yönü ne tarafa" sorusuna cevap verir --
    # ÖNCÜ AL'ın kalbi bu beştir.
    adx_rising: bool = False
    momentum_building: bool = False
    volume_accelerating: bool = False
    trades_accelerating: bool = False
    rsi_healthy_rising: bool = False
    # YENİ (V25): birikim/akümülasyon özellikleri -- "hareket henüz
    # başlamadı ama sessizce toplanıyor olabilir" tespiti için.
    obv_rising: bool = False
    price_flat_or_down: bool = False
    obv_divergence: bool = False
    macd_bullish_divergence: bool = False
    keltner_squeeze: bool = False
    quiet_volume_rising: bool = False
    # YENİ: VWAP (Hacim Ağırlıklı Ortalama Fiyat) -- kurumsal referans
    # noktası. Sadece bilgilendirici/öncelik ayarlayıcı, mevcut kriter
    # sayım sistemine (count_early_criteria) dahil EDİLMEDİ -- o sistem
    # zaten dikkatle test edilmiş 7 kritere göre kalibre edilmiş, sekizinci
    # bir kriter eklemek eşik anlamlarını değiştirip yeniden doğrulama
    # gerektirirdi.
    vwap_value: float = 0.0
    price_above_vwap: bool = False
    trap_reasons: list[str] = field(default_factory=list)


def trade_confidence(cfg: Settings, trades: int, volume_ratio: float) -> float:
    if trades <= 0:
        return 0
    if volume_ratio >= 2 and trades < cfg.min_1m_trades:
        return 0.25
    if trades < cfg.min_1m_trades:
        return 0.40
    return min(1.0, max(0.40, trades / cfg.trade_reference))


def extract_features(cfg: Settings, c1: list, close5: list[float], volume5: list[float],
                      price_5m_close: float, trades5_sum: int) -> Features:
    """1m kline'lardan tüm türetilmiş metrikleri ve indikatörleri hesaplar."""
    close = [float(x[4]) for x in c1]
    high = [float(x[2]) for x in c1]
    low = [float(x[3]) for x in c1]
    volume = [float(x[7]) for x in c1]
    trades = [int(x[8]) for x in c1]

    price = close[-1]

    avg5 = avg(volume5[-12:])
    recent5 = avg(volume5[-3:])
    vr5 = recent5 / avg5 if avg5 else 0
    momentum5 = pct(close5[-4], price)

    momentum1 = pct(close[-2], price)

    low90 = min(low[-90:])
    high90 = max(high[-90:])
    location = (price - low90) / (high90 - low90) * 100 if high90 > low90 else 50

    avg_volume = avg(volume[-30:])
    last3 = avg(volume[-3:])
    previous = avg(volume[-10:-3])
    vr = last3 / avg_volume if avg_volume else 0
    impulse = min(last3 / previous if previous else 1, 10)

    buy_volume = sum(float(x[10]) for x in c1[-5:])
    total_volume = sum(float(x[7]) for x in c1[-5:])
    bp = buy_volume / total_volume * 100 if total_volume else 50

    trades1 = sum(trades[-5:])

    ema9, ema21, ema50 = ema(close, 9), ema(close, 21), ema(close, 50)
    ema9_old, ema21_old = ema(close[:-3], 9), ema(close[:-3], 21)
    ema_up = ema9 > ema21 and ema9 > ema9_old
    ema_cross = ema9 > ema21 and ema9_old <= ema21_old

    rv = rsi(close)
    old_rsi = rsi(close[:-3])

    _, _, macd_now = macd(close)
    _, _, macd_old = macd(close[:-3])
    macd_up = macd_now > macd_old

    ad, plus_di, minus_di = adx(high, low, close)

    lower, middle, upper = bb(close)
    width = (upper - lower) / middle * 100 if middle else 0
    old_lower, old_middle, old_upper = bb(close[:-5])
    old_width = (old_upper - old_lower) / old_middle * 100 if old_middle else width

    squeeze = width <= 2.2 or (old_width > 0 and width < old_width * 0.80)
    expanding = old_width > 0 and width > old_width * 1.08

    resistance = max(high[-30:-2])
    dist = max(0, (resistance - price) / price * 100)
    breakout = price > resistance
    closed_breakout = close[-1] > resistance

    higher_low = low[-1] > low[-3] and low[-3] >= low[-6]

    candle_range = high[-1] - low[-1]
    close_position = (close[-1] - low[-1]) / candle_range * 100 if candle_range > 0 else 50

    low_activity = trades1 < cfg.min_1m_trades or trades5_sum < cfg.min_5m_trades
    weak_volume = vr < 1.0 or vr5 < 1.0

    # ============================================================
    # YENİ (Faz B / ÖNCÜ AL): "yön/ivme" metrikleri.
    # Hepsi zaten çekilen 1m kline dizisinden, ek API çağrısı olmadan
    # hesaplanıyor. Amaç: "şu an güçlü mü" değil "güçleniyor mu" sorusuna
    # cevap vermek -- ADX gibi geç kalan göstergelerin henüz eşiği
    # geçmediği anlarda bile hareketin oluştuğunu yakalayabilmek.
    # ============================================================

    # ADX yönü: 3 bar önceki ADX'e göre şimdi yükseliyor mu?
    # (old_rsi/ema9_old/macd_old ile aynı desen: close[:-3] ile "3 bar önce")
    # Not: 14 barlık ADX periyodu + 3 barlık geri kaydırma için güvenli pay.
    if len(high) > 14 * 2 + 1 + 3:
        ad_prev, _, _ = adx(high[:-3], low[:-3], close[:-3])
    else:
        ad_prev = ad
    adx_rising = ad > ad_prev

    # Momentum yeni mi oluşuyor: son barın hareketi, ondan önceki bardan
    # daha güçlü VE pozitif mi? (17->19->22 tarzı, LINK örneğindeki gibi
    # kademeli oluşumu yakalamak için)
    m_prev = pct(close[-3], close[-2]) if len(close) >= 3 else 0.0
    momentum_building = momentum1 > 0 and momentum1 > m_prev

    # Hacim ivmesi: son 9 barı 3'erli 3 pencereye bölüp, ortalama hacim
    # oranının art arda arttığına bakıyoruz (1.1x -> 1.5x -> 2.0x gibi).
    # Tek bir "şu an kaç kat" sayısı yerine YÖNÜ ölçüyoruz.
    if avg_volume and len(volume) >= 9:
        vr_a = avg(volume[-9:-6]) / avg_volume
        vr_b = avg(volume[-6:-3]) / avg_volume
        vr_c = vr
        volume_accelerating = vr_c > vr_b >= vr_a
    else:
        volume_accelerating = False

    # İşlem sayısı ivmesi: aynı mantık, 15 barı 5'erli 3 pencereye bölüp
    # işlem sayısının art arda arttığına bakıyoruz. Bu, MIN_1M_TRADES gibi
    # sabit bir eşiğin "henüz düşükken" bile hareketin başladığını
    # gösterebilir (madde 4).
    if len(trades) >= 15:
        trades_a = sum(trades[-15:-10])
        trades_b = sum(trades[-10:-5])
        trades_c = trades1
        trades_accelerating = trades_c > trades_b >= trades_a
    else:
        trades_accelerating = False

    # RSI sağlıklı bantta ve yükseliyor (madde 6): RSI 70'i geçti diye
    # otomatik "geç sinyal" saymıyoruz -- asıl önemli olan sağlıklı bir
    # bantta (varsayılan 50-75) yükseliş yönünde olması.
    rsi_healthy_rising = cfg.healthy_rsi_low <= rv <= cfg.healthy_rsi_high and rv > old_rsi

    trap_reasons = []
    if bp < cfg.trap_buyer and vr >= cfg.trap_volume:
        trap_reasons.append("zayıf alıcı")
    if momentum5 < cfg.trap_momentum and not higher_low:
        trap_reasons.append("negatif momentum")
    if low_activity and vr >= 2:
        trap_reasons.append("düşük işlem")
    if low_activity and weak_volume and bp >= 90:
        trap_reasons.append("güvenilmez baskı")

    # YENİ: VWAP -- son 1m kline penceresi (bu fonksiyona giren c1) üzerinden
    # hesaplanıyor. Borsa "seans" kavramı olmadığı için (kripto 7/24) tam
    # gün başlangıcı yerine son ~3 saatlik (180 bar) pencereyi kullanıyoruz.
    vwap_value = vwap(high, low, close, volume)
    price_above_vwap = vwap_value > 0 and price > vwap_value

    return Features(
        price=price, momentum1=momentum1, momentum5=momentum5, location=location,
        close_position=close_position, vr=vr, vr5=vr5, impulse=impulse, bp=bp, trades1=trades1, trades5=trades5_sum,
        ema_up=ema_up, ema_cross=ema_cross, price_above_ema50=price >= ema50,
        rv=rv, old_rsi=old_rsi, macd_up=macd_up, ad=ad, plus_di=plus_di, minus_di=minus_di,
        squeeze=squeeze, expanding=expanding, dist=dist, breakout=breakout,
        closed_breakout=closed_breakout, higher_low=higher_low,
        low_activity=low_activity, weak_volume=weak_volume,
        trap=bool(trap_reasons),
        adx_rising=adx_rising, momentum_building=momentum_building,
        volume_accelerating=volume_accelerating, trades_accelerating=trades_accelerating,
        rsi_healthy_rising=rsi_healthy_rising,
        vwap_value=vwap_value, price_above_vwap=price_above_vwap,
        trap_reasons=trap_reasons,
    )


def count_early_criteria(f: Features) -> tuple[int, list[str]]:
    """YENİ (Faz B): 7 bağımsız erken-hareket kriteri.

    Her biri farklı bir göstergeden gelir, aynı göstergenin farklı eşikleri
    ayrı kriter sayılmaz (madde 9/10 -- çifte cezalandırma/ödüllendirme
    denetimi). decide_stage() bu sayıyı kullanarak ÖNCÜ AL / AL / GÜÇLÜ AL
    kararı verir -- eski "setup puanı >= 25" gibi tek-boyutlu eşiklerin
    yerini alır.
    """
    criteria = []

    if f.adx_rising and f.plus_di > f.minus_di:
        criteria.append("ADX yükseliyor (+DI baskın)")
    if f.momentum_building:
        criteria.append("Momentum yeni oluşuyor")
    if f.volume_accelerating:
        criteria.append("Hacim ivmeleniyor")
    if f.trades_accelerating:
        criteria.append("İşlem sayısı ivmeleniyor")
    if f.rsi_healthy_rising:
        criteria.append("RSI sağlıklı bantta ve yükseliyor")
    if f.ema_up:
        criteria.append("EMA yukarı")
    if f.macd_up:
        criteria.append("MACD güçleniyor")

    return len(criteria), criteria


def extract_accumulation_features(f: Features, c1: list) -> Features:
    """YENİ (V25): birikim/akümülasyon özelliklerini hesaplayıp `f`'ye
    ekler. Bilinçli olarak `extract_features()`'dan AYRI tutuldu ve
    sadece normal momentum tier'ları (ÖNCÜ/AL/GÜÇLÜ AL) hiçbiri
    tetiklenmediğinde çağrılır -- her sembolde gereksiz hesaplama
    yapmamak için (OBV serisi, MACD histogram serisi, Keltner Channel
    hepsi ekstra O(n) iş). `c1` ham 1m kline listesidir -- close/high/
    low/volume dizilerini extract_features() ile aynı şekilde türetir.
    """
    close = [float(x[4]) for x in c1]
    high = [float(x[2]) for x in c1]
    low = [float(x[3]) for x in c1]
    volume = [float(x[7]) for x in c1]

    obv_series = obv(close, volume)

    obv_rising = len(obv_series) >= 20 and obv_series[-1] > obv_series[-20]
    price_change_20 = pct(close[-20], close[-1]) if len(close) >= 20 else 0.0
    # "Yatay/düşük" -- son 20 barda %1'den az yükseliş (düşüş dahil).
    price_flat_or_down = price_change_20 < 1.0

    obv_divergence = (
        len(close) >= 40 and len(obv_series) >= 40
        and bullish_divergence(close[-40:], obv_series[-40:], lookback=40, pivot_window=3)
    )

    hist_series = macd_hist_series(close)
    macd_bullish_div = False
    if hist_series and len(close) >= len(hist_series):
        aligned_close = close[-len(hist_series):]
        lookback = min(40, len(hist_series))
        macd_bullish_div = bullish_divergence(aligned_close, hist_series, lookback=lookback, pivot_window=3)

    bb_lower, bb_middle, bb_upper = bb(close, period=20, k=2)
    kc_lower, kc_middle, kc_upper = keltner_channel(high, low, close, period=20, multiplier=1.5)
    # Gerçek TTM Squeeze: Bollinger Bands TAMAMEN Keltner Channel'ın içinde.
    keltner_squeeze = bool(kc_upper) and bb_upper < kc_upper and bb_lower > kc_lower

    quiet_volume_rising = False
    if len(volume) >= 60:
        recent_avg = avg(volume[-15:])
        base_avg = avg(volume[-60:-15])
        vol_ratio = recent_avg / base_avg if base_avg else 0
        # Hafif ama tutarlı bir hacim artışı (1.15x-1.8x) -- fiyat henüz
        # tepki vermemişken. Zaten momentum tier'ları elenmiş bir coin'e
        # bakıyoruz (bu fonksiyon sadece o durumda çağrılıyor), o yüzden
        # "fiyat tepkisi yok" şartını price_flat_or_down zaten sağlıyor.
        quiet_volume_rising = 1.15 <= vol_ratio <= 1.8 and price_flat_or_down

    f.obv_rising = obv_rising
    f.price_flat_or_down = price_flat_or_down
    f.obv_divergence = obv_divergence
    f.macd_bullish_divergence = macd_bullish_div
    f.keltner_squeeze = keltner_squeeze
    f.quiet_volume_rising = quiet_volume_rising

    return f


def count_accumulation_criteria(f: Features) -> tuple[int, list[str]]:
    """YENİ (V25): 5 bağımsız birikim kriteri. Tanınmış, kanıtlanmış
    yöntemlere dayanır (bkz. indicators.py'daki fonksiyon yorumları):
    Granville'in OBV'si, Carter'ın TTM Squeeze'i, Wilder'ın divergence
    takibi. Momentum kriterlerinden (count_early_criteria) TAMAMEN AYRI
    -- bu kriterler "henüz hareket yok" durumunu tarar.
    """
    criteria = []

    if f.obv_rising and f.price_flat_or_down:
        criteria.append("OBV yükseliyor, fiyat yatay/düşük (Granville)")
    if f.obv_divergence:
        criteria.append("OBV pozitif uyumsuzluk")
    if f.macd_bullish_divergence:
        criteria.append("MACD pozitif uyumsuzluk")
    if f.keltner_squeeze:
        criteria.append("Gerçek TTM Squeeze")
    if f.quiet_volume_rising:
        criteria.append("Sessiz hacim artışı")

    return len(criteria), criteria


def decide_watch_stage(cfg: Settings, f: Features, accumulation_count: int) -> str:
    """YENİ (V25): birikim tier kararı. ÖNCÜ AL'dan daha erken bir aşama
    -- momentum henüz YOK, sadece "biri toplıyor olabilir" işaretleri var.
    Bu yüzden ayrı, açıkça daha spekülatif bir etiketle (🔵 İZLE) gösterilir.
    """
    if f.trap:
        return "PASS"
    if accumulation_count >= cfg.watch_min_criteria:
        return "WATCH"
    return "PASS"


def score_setup(cfg: Settings, f: Features) -> int:
    setup = 0
    if f.ema_up:
        setup += 12
    if f.ema_cross:
        setup += 6
    if f.squeeze:
        setup += 8
    if f.higher_low:
        setup += 6
    if 35 <= f.rv <= 65 and f.rv > f.old_rsi:
        setup += 8
    if f.price_above_ema50:
        setup += 5
    # DÜZELTME (kırılım aşırı ödüllendiriliyordu): eskiden dist<=0.35 VE
    # dist<=0.70 aynı +8'i veriyordu (0.35 zaten 0.70'in altında olduğu
    # için ikinci koşul hiç ayrım yapmıyordu). Şimdi mesafeye göre
    # kademeli: gerçekten yakınsa daha çok, uzaksa daha az puan.
    if f.dist <= 0.35:
        setup += 6
    elif f.dist <= 0.70:
        setup += 3
    if f.vr >= 1.5 and f.trades1 >= cfg.min_1m_trades:
        setup += 8
    if f.bp >= 58 and f.trades1 >= cfg.min_1m_trades:
        setup += 5
    return setup


def score_confirmation(cfg: Settings, f: Features) -> int:
    confirmation = 0

    # DÜZELTME (kırılım aşırı ödüllendiriliyordu): closed_breakout 18->12,
    # gerçekleşmemiş (intrabar) breakout 10->5. Kapanmamış bir kırılım
    # fitil/wick ile kolayca sahte olabilir, bu yüzden ağırlığı düşürüldü.
    if f.closed_breakout:
        confirmation += 12
    elif f.breakout:
        confirmation += 5

    if f.vr >= 2:
        confirmation += 12
    elif f.vr >= 1.5:
        confirmation += 7

    if f.vr5 >= 1.5:
        confirmation += 8

    if f.bp >= 65 and f.trades1 >= cfg.min_1m_trades:
        confirmation += 7

    if f.macd_up:
        confirmation += 6

    # DÜZELTME (ADX 18 yeterince güçlü kabul ediliyordu): teknik analizde
    # genel kabul ADX>25 güçlü trend, <20 zayıf/yatay piyasadır. Eşik artık
    # cfg.min_adx_trend (varsayılan 25). 18-24 arası "trend var ama zayıf"
    # kabul edilip küçük bir ceza alıyor -- eskiden tam puan alıyordu.
    if f.plus_di > f.minus_di and f.ad >= cfg.min_adx_trend:
        confirmation += 7
    elif f.plus_di > f.minus_di and f.ad >= 10:
        confirmation -= cfg.weak_adx_penalty

    if f.close_position >= 65:
        confirmation += 4

    if f.expanding:
        confirmation += 4

    if f.trades1 >= cfg.min_1m_trades and f.trades5 >= cfg.min_5m_trades:
        confirmation += 3

    if f.weak_volume:
        confirmation -= 8
    if f.ad < 10:
        confirmation -= 10
    if f.low_activity:
        confirmation -= 8

    return confirmation
def score_penalty(cfg: Settings, f: Features, d30: float | None, d90: float | None,
                   market_momentum: float) -> int:
    # DÜZELTME (madde 8): 30/90 günlük düşüş cezası buradan tamamen
    # kaldırıldı. Bir coin 90 günde çok düşmüş olabilir ama şu an hacim,
    # işlem, alıcı baskısı, EMA, MACD ve momentum birlikte güçleniyor
    # olabilir -- bu gerçek, bugünkü hareketi geçmişin cezalandırması
    # yanlış olurdu. Uzun vadeli risk artık sadece mesajda ayrı bir rozet
    # olarak gösteriliyor (bkz. trend_state_label), skoru etkilemiyor.
    penalty = 0

    if f.momentum1 > 2.5:
        penalty -= 10
    if f.momentum5 > 5:
        penalty -= 12
    if f.rv > 78:
        penalty -= 10
    if f.rv >= 85:
        penalty -= 8
    if f.bp < 50 and f.vr >= 1.8:
        penalty -= 8
    if f.momentum5 < -1.2 and not f.higher_low:
        penalty -= 12
    if f.vr >= 2 and f.trades1 < cfg.min_1m_trades:
        penalty -= 8
    if f.trap:
        penalty -= 12

    if abs(market_momentum) >= cfg.market_move * 2:
        penalty -= 8
    elif abs(market_momentum) >= cfg.market_move:
        penalty -= 4

    return penalty


def score_entry_quality(cfg: Settings, f: Features, d30: float | None, d90: float | None) -> int:
    entry = 100

    if f.rv >= 85:
        entry -= 30
    elif f.rv >= 78:
        entry -= 15

    if f.momentum1 >= 5:
        entry -= 25
    elif f.momentum1 >= 2.5:
        entry -= 12

    if f.momentum5 >= 5:
        entry -= 20
    elif f.momentum5 >= 3:
        entry -= 10

    if f.dist <= 0.15:
        entry -= 8
    elif f.dist <= 0.35:
        entry -= 4

    # DÜZELTME (kırılım aşırı ödüllendiriliyordu): closed_breakout bonusu
    # 5'ten 2'ye düşürüldü -- kırılım zaten confirmation'da ayrıca
    # ödüllendiriliyor, entry_quality'de tekrar büyük bonus vermesi
    # gereksiz şişirme yaratıyordu.
    if f.closed_breakout:
        entry += 2
    if f.higher_low:
        entry += 3

    if f.trades1 < cfg.min_1m_trades:
        entry -= 18
    if f.trades1 < 5:
        entry -= 15

    if f.vr < 1.0:
        entry -= 12
    if f.vr5 < 1.0:
        entry -= 10
    if f.ad < 10:
        entry -= 12
    # DÜZELTME (ADX 18 yeterince güçlü kabul ediliyordu): 10-25 arası
    # "zayıf trend" için de küçük bir ceza -- eskiden sadece <10 ceza alıyordu.
    elif f.ad < cfg.min_adx_trend:
        entry -= cfg.weak_adx_penalty

    if d30 is not None and d30 >= 20:
        entry -= 5

    # DÜZELTME (madde 8): d90 tabanlı büyük ceza bloğu (eskiden -28/-18/-8)
    # tamamen kaldırıldı. Uzun vadeli çöküş artık entry_quality'yi
    # otomatik bastırmıyor -- mesajda ayrı bir risk rozeti olarak
    # gösteriliyor, kararı insana bırakıyoruz.

    if f.trap:
        entry -= 20

    entry = max(0, min(100, entry))
    # DÜZELTME (entry 90+ fazla kolay oluyordu): 78'in üzerini sıkıştır.
    # 90+ görmek için artık gerçekten istisnai bir kurulum gerekiyor.
    entry = soft_cap(entry, cfg.entry_soft_cap, cfg.entry_soft_cap_factor)

    return max(0, min(100, int(round(entry))))


def decide_stage(cfg: Settings, f: Features, criteria_count: int) -> str:
    """YENİ (Faz B): kriter-sayımına dayalı üç aşamalı karar.

    Eski sistem tek boyutlu skor eşiklerine (setup>=25, score>=68 vb.)
    dayanıyordu. Yeni sistem 7 bağımsız kriterden kaçının sağlandığını
    sayıyor (bkz. count_early_criteria) ve buna göre üç aşamaya ayırıyor:

      🟡 ÖNCÜ AL  : en az `oncu_min_criteria` (varsayılan 3) kriter,
                    ADX mutlak seviye VEYA kırılım ŞART DEĞİL.
      🟢 AL       : en az `buy_min_criteria` (varsayılan 5) kriter
                    + ayrıca momentum/hacim TEYİDİ (ÖNCÜ AL'dan farkı).
      🚀 GÜÇLÜ AL : en az `very_min_criteria` (varsayılan 6) kriter
                    + (kırılım VEYA güçlü ADX trend teyidi) -- ikisi de
                    zorunlu değil, biri yeterli (madde 7).

    trap varsa hiçbir aşama tetiklenmez. d30/d90 (uzun vade) burada HİÇ
    kullanılmıyor (madde 8) -- o bilgi sadece mesajda risk rozeti olarak
    gösterilir, kararı etkilemez. Streak de burada kullanılmıyor (madde 1)
    -- ilk uygun sinyal hiçbir streak şartına takılmadan gönderilir.
    """
    if f.trap:
        return "PASS"

    stage = "PASS"

    if criteria_count >= cfg.oncu_min_criteria and f.rv < cfg.oncu_rsi_max:
        stage = "ONCU"

    momentum_confirmed = f.momentum1 > cfg.al_momentum_confirm or f.vr >= cfg.al_volume_confirm
    if criteria_count >= cfg.buy_min_criteria and momentum_confirmed:
        stage = "BUY"

    strong_trend_confirmed = f.closed_breakout or (f.plus_di > f.minus_di and f.ad >= cfg.min_adx_trend)
    if criteria_count >= cfg.very_min_criteria and strong_trend_confirmed:
        stage = "VERY"

    return stage


def trend_state_label(trend_ok: bool, d30: float | None, d90: float | None, cfg: Settings) -> str:
    if not trend_ok:
        return "VERİ YOK"
    if d30 > 10 and d90 > 0:
        return "POZİTİF TREND"
    if d90 <= cfg.lt90_extreme or d30 <= cfg.lt30_strong:
        return "YÜKSEK DÜŞÜŞ RİSKİ"
    if d90 <= cfg.lt90_strong or d30 <= cfg.lt30_mild:
        return "DÜŞÜŞ RİSKİ"
    return "NÖTR"


def analyze(cfg: Settings, client: BinanceClient, db: DB, market: MarketData, item: dict) -> dict:
    """Orkestratör: veri çeker, özellik çıkarır, puanlar, stage'e karar verir."""
    symbol = item["symbol"]

    try:
        k5 = client.klines(symbol, "5m", 80)
        if len(k5) < 40:
            return {"status": "PASS"}

        c5 = k5[:-1]
        close5 = [float(x[4]) for x in c5]
        volume5 = [float(x[7]) for x in c5]
        trades5_list = [int(x[8]) for x in c5]

        price_estimate = close5[-1]
        avg5 = avg(volume5[-12:])
        recent5 = avg(volume5[-3:])
        vr5_early = recent5 / avg5 if avg5 else 0
        momentum5_early = pct(close5[-4], price_estimate)

        if momentum5_early < -3 and vr5_early < 1.3:
            return {"status": "PASS"}

        trend = market.daily_trend(symbol)
        d30 = trend["d30"] if trend["ok"] else None
        d90 = trend["d90"] if trend["ok"] else None

        k1 = client.klines(symbol, "1m", 180)
        if len(k1) < 100:
            return {"status": "PASS"}

        c1 = k1[:-1]
        trades5_sum = sum(trades5_list[-1:])

        f = extract_features(cfg, c1, close5, volume5, price_estimate, trades5_sum)

        setup = score_setup(cfg, f)
        confirmation = score_confirmation(cfg, f)
        market_ctx = market.context()
        market_momentum = market_ctx.get("momentum", 0)
        penalty = score_penalty(cfg, f, d30, d90, market_momentum)

        raw_score = clamp(setup + confirmation + penalty)
        # DÜZELTME (skor fazla kolay 90+ oluyordu): cap'in (80) üzerini
        # sıkıştır. Artık 90+ görmek için ham toplamın çok daha yüksek
        # (yaklaşık 105+) olması gerekiyor -- sıradan konfluanslar artık
        # 80'in biraz üzerinde kalıyor, gerçekten istisnai durumlar 90'a
        # yaklaşabiliyor.
        score = int(round(soft_cap(raw_score, cfg.score_soft_cap, cfg.score_soft_cap_factor)))
        score = clamp(score)

        if f.low_activity:
            score = min(score, 78)
        if f.weak_volume:
            score = min(score, 82)
        if f.ad < 10:
            score = min(score, 72)
        elif f.ad < cfg.min_adx_trend:
            score = min(score, 80)
        if f.rv >= 90 and f.trades1 < cfg.min_1m_trades:
            score = min(score, 65)
        # DÜZELTME (madde 8): "d90 <= extreme ise score'u 82'de bastır"
        # satırı kaldırıldı -- uzun vadeli çöküş artık bugünkü hareketin
        # skorunu otomatik olarak bastırmıyor.

        entry = score_entry_quality(cfg, f, d30, d90)

        # YENİ (Faz B): stage kararı artık kriter sayımına dayanıyor,
        # eski score/setup/confirmation eşiklerine değil (onlar hâlâ
        # hesaplanıp mesajda gösteriliyor, ama artık KARAR VERİCİ değiller).
        criteria_count, criteria_list = count_early_criteria(f)
        stage = decide_stage(cfg, f, criteria_count)

        # YENİ (V25): momentum tier'larının HİÇBİRİ tetiklenmediyse
        # (yani coin henüz hareket etmemiş), birikim/akümülasyon
        # kriterlerine bakıyoruz -- "hareket henüz yok ama biri
        # topluyor olabilir" sorusu. Sadece bu durumda çağrılıyor (gereksiz
        # OBV/MACD-seri/Keltner hesaplamasından kaçınmak için).
        accumulation_count, accumulation_list = 0, []
        if stage == "PASS":
            f = extract_accumulation_features(f, c1)
            accumulation_count, accumulation_list = count_accumulation_criteria(f)
            watch_stage = decide_watch_stage(cfg, f, accumulation_count)
            if watch_stage == "WATCH":
                stage = "WATCH"

        # DÜZELTME (madde 1 -- streak ilk sinyali engellemesin): eskiden
        # "if level=='BUY' and streak<cfg.buy_streak: level='INTERNAL'"
        # gibi bloklar ilk uygun sinyali Telegram'a gitmeden önce
        # bastırıyordu. Artık stage == level: karar netse doğrudan
        # gönderiliyor. Streak sadece hareketin DEVAM ettiğini göstermek
        # için hesaplanıp mesajda gösteriliyor -- bir gate değil.
        level = stage
        # WATCH, gerçek bir momentum teyidi değil -- streak'e (hareketin
        # DEVAM ettiği bilgisine) dahil edilmiyor, sadece kendi başına
        # gösteriliyor.
        qualified = stage in ("ONCU", "BUY", "VERY") and not f.trap
        streak = db.update_streak(symbol, qualified, f.trap)

        return {
            "status": level,
            "symbol": symbol,
            "score": score,
            "setup": setup,
            "confirmation": confirmation,
            "penalty": penalty,
            "price": f.price,
            "chg": item["chg"],
            "loc": f.location,
            "bp": f.bp,
            "vr": f.vr,
            "vr5": f.vr5,
            "impulse": f.impulse,
            "rv": f.rv,
            "ad": f.ad,
            "dist": f.dist,
            "ema": f.ema_up,
            "macd": f.macd_up,
            "squeeze": f.squeeze,
            "hl": f.higher_low,
            "breakout": f.breakout,
            "closed_breakout": f.closed_breakout,
            "trades_1m": f.trades1,
            "trades_5m": f.trades5,
            "trade_conf": trade_confidence(cfg, f.trades1, f.vr),
            "d30": d30,
            "d90": d90,
            "trend_state": trend_state_label(trend["ok"], d30, d90, cfg),
            "trap": f.trap,
            "trap_reasons": f.trap_reasons,
            "entry_quality": entry,
            "streak": streak,
            "market_momentum": market_momentum,
            "market_state": market_ctx.get("state", "VERİ YOK"),
            # YENİ (Faz B): erken hareket kriterleri -- mesajda ve
            # ileride tanılama loglarında (Faz E) kullanılacak.
            "criteria_count": criteria_count,
            "criteria_list": criteria_list,
            "adx_rising": f.adx_rising,
            "momentum_building": f.momentum_building,
            "volume_accelerating": f.volume_accelerating,
            "trades_accelerating": f.trades_accelerating,
            "rsi_healthy_rising": f.rsi_healthy_rising,
            # YENİ (V25): birikim kriterleri -- sadece WATCH tetiklendiğinde
            # dolu, diğer tier'larda boş liste/0 olarak kalır.
            "accumulation_count": accumulation_count,
            "accumulation_list": accumulation_list,
            # YENİ: VWAP -- göreceli güç yüzdelik dilimi scan() içinde
            # (main.py) TÜM adaylar arasında hesaplanıp buraya sonradan
            # eklenir (analyze() tek sembol bazlı çalıştığı için burada
            # hesaplanamaz).
            "vwap_value": f.vwap_value,
            "price_above_vwap": f.price_above_vwap,
        }

    except Exception as e:
        # DÜZELTME (acil): eskiden log.debug kullanıyordu -- sistem INFO
        # seviyesinde çalıştığı için bu satır hiçbir zaman görünmüyordu.
        # %95 hata oranı olsa bile sebep tamamen görünmezdi. Artık WARNING
        # seviyesinde ve tam traceback ile logluyor -- bir sonraki taramada
        # asıl kök nedeni göreceğiz.
        log.warning("%s analiz hatası: %s", symbol, e, exc_info=True)
        return {"status": "error", "symbol": symbol}


def priority_score(cfg: Settings, r: dict) -> float:
    value = (
        r["score"] * 0.50
        + r["entry_quality"] * 0.25
        + r["trade_conf"] * 100 * 0.10
    )

    if r["streak"] >= 3:
        value += 8
    elif r["streak"] >= 2:
        value += 4

    # DÜZELTME (kırılım aşırı ödüllendiriliyordu): priority'de de
    # closed_breakout bonusu 8'den 4'e düşürüldü. Kırılım zaten score ve
    # entry_quality üzerinden priority'ye dolaylı olarak yansıyor;
    # burada ayrıca büyük bir bonus vermek üçüncü kez saymak oluyordu.
    if r["closed_breakout"]:
        value += 4
    elif r["breakout"]:
        value += 1

    # YENİ: VWAP ve göreceli güç -- küçük, sınırlı bir ayar (max ±5).
    # Bilinçli olarak DÜŞÜK ağırlıklı tutuldu: bunlar destekleyici bağlam,
    # ana karar hâlâ kriter sayımı + momentum/entry/hacim.
    if r.get("price_above_vwap"):
        value += 2

    rs_pct = r.get("relative_strength_pct", 50)
    if rs_pct >= 80:
        value += 3
    elif rs_pct <= 20:
        value -= 3

    if r["bp"] >= 75:
        value += 5
    elif r["bp"] >= 65:
        value += 3

    if r["vr"] >= 3:
        value += 5
    elif r["vr"] >= 2:
        value += 3
    elif r["vr"] >= 1.5:
        value += 1

    if r["vr5"] >= 2:
        value += 4
    elif r["vr5"] >= 1.5:
        value += 2

    if r["trades_1m"] < 5:
        value -= 15
    elif r["trades_1m"] < cfg.min_1m_trades:
        value -= 8

    if r["vr5"] < 0.75:
        value -= 8
    if r["ad"] < 10:
        value -= 8
    if r["rv"] >= 85:
        value -= 8

    # DÜZELTME (madde 8): d30/d90 tabanlı priority cezası kaldırıldı.
    # Bu ceza, ÖNCÜ AL gibi zaten düşük priority'yle başlayan erken
    # sinyalleri MIN_PRIORITY_ONCU'nun altına düşürüp gönderilmeden
    # elenmesine yol açabiliyordu -- tam da "erken hareketi otomatik
    # bastırmasın" talimatının ihlali. Uzun vade riski artık sadece
    # mesajda gösteriliyor, priority'yi etkilemiyor.

    if r["trap"]:
        value -= 25

    value = max(0, min(100, value))
    # DÜZELTME (priority 95-100'e fazla kolay çıkıyordu): 75'in üzerini
    # sıkıştır. Artık 95+ görmek için neredeyse tüm kriterlerin aynı anda
    # ve güçlü şekilde sağlanması gerekiyor.
    value = soft_cap(value, cfg.priority_soft_cap, cfg.priority_soft_cap_factor)

    return max(0, min(100, round(value, 1)))


def watch_priority(cfg: Settings, accumulation_count: int) -> float:
    """YENİ (V25): WATCH sinyalleri için ayrı öncelik hesabı.

    priority_score() momentum-odaklı alanlara (breakout, hacim patlaması
    vb.) dayanır -- bunlar tanım gereği bir WATCH coin'inde nötr/baseline
    kalır (henüz hareket yok). Bu yüzden WATCH'ı SADECE kaç birikim
    kriterinin sağlandığına göre sıralıyoruz (5 kriterin yüzdesi).
    """
    return round(accumulation_count / 5 * 100, 1)


def rank_signals(cfg: Settings, signals: list[dict]) -> list[dict]:
    for r in signals:
        if r["status"] == "WATCH":
            r["priority"] = watch_priority(cfg, r["accumulation_count"])
        else:
            r["priority"] = priority_score(cfg, r)

    signals.sort(key=lambda x: (x["priority"], x["entry_quality"], x["score"]), reverse=True)

    for i, r in enumerate(signals, 1):
        r["rank"] = i

    return signals
    


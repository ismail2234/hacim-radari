"""
Saf matematik fonksiyonları: RSI, MACD, ADX, Bollinger Bands, EMA.

Bunların hiçbiri ağ, DB veya global state'e dokunmuyor -> birim testi
yazmak trivial. Eski kodda bu fonksiyonlar da aynıydı ama tek bir 900
satırlık dosyanın içinde kayboluyorlardı; buraya taşımak davranışı
DEĞİŞTİRMEZ, sadece test edilebilir ve tekrar kullanılabilir hale getirir.
"""

from __future__ import annotations


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def pct(a: float, b: float | None) -> float:
    if not a or b is None:
        return 0.0
    return ((b - a) / a) * 100


def clamp(value: float) -> int:
    return max(0, min(100, int(round(value))))


def soft_cap(value: float, cap: float, factor: float) -> float:
    """Eşiğin (cap) altını olduğu gibi bırakır, üstünü `factor` ile sıkıştırır.

    Örnek: cap=80, factor=0.4 -> ham değer 80 ise sonuç 80. Ham değer 105
    ise sonuç 80 + (105-80)*0.4 = 90. Yani 90'a ulaşmak için ham puanın
    105 olması gerekir -- eskiden 90 ham puanla da 90 elde ediliyordu.
    Bu, "skor/priority/entry fazla kolay 90+ oluyor" sorununu doğrudan
    hedefler: sıradan bir sinyal cap'in altında kalır, sadece gerçekten
    istisnai (çok kriterin aynı anda sağlandığı) durumlar tavana yaklaşır.
    """
    if value <= cap:
        return value
    return cap + (value - cap) * factor


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return avg(values)

    k = 2 / (period + 1)
    result = avg(values[:period])
    for value in values[period:]:
        result = value * k + result * (1 - k)
    return result


def rsi(values: list[float], period: int = 14) -> float:
    """Wilder'ın klasik RSI'ı: gain/loss ortalamaları, son N barın basit
    ortalaması yerine BAŞTAN İTİBAREN üstel (Wilder) yumuşatmayla hesaplanır.

    DÜZELTME: eski implementasyon `avg(gains[-period:])` kullanıyordu --
    yani sadece son 14 barın düz ortalamasıydı, geçmişin etkisini tamamen
    unutuyordu. Bu, borsa uygulamalarının (TradingView, Binance vb.)
    gösterdiği RSI'dan sistematik olarak sapan bir değer üretiyordu.
    Wilder yumuşatması RSI'ın orijinal ve yaygın kabul gören tanımıdır.
    """
    if len(values) < period + 1:
        return 50.0

    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]

    avg_gain = avg(gains[:period])
    avg_loss = avg(losses[:period])

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def ema_series(values: list[float], period: int) -> list[float | None]:
    """`ema()` ile aynı seed mantığını kullanan ama TÜM SERİYİ döndüren
    versiyon (her nokta için ayrı ayrı). MACD'yi O(n) hesaplamak için
    gerekli -- eski `macd()` her adımda `ema(values[:i], ...)` çağırarak
    O(n^2) çalışıyordu. İlk `period-1` eleman tanımsızdır (None); henüz
    seed için yeterli veri yoktur.
    """
    if not values:
        return []
    if len(values) < period:
        return [None] * len(values)

    k = 2 / (period + 1)
    series: list[float | None] = [None] * (period - 1)
    result = avg(values[:period])
    series.append(result)

    for value in values[period:]:
        result = value * k + result * (1 - k)
        series.append(result)

    return series


def macd(values: list[float]) -> tuple[float, float, float]:
    """DÜZELTME: eski implementasyon her `i` için `ema(values[:i], 12)` ve
    `ema(values[:i], 26)` çağırıyordu -- bu, aynı EMA'yı baştan baştan
    tekrar tekrar hesaplamak demekti (O(n^2)). `ema_series()` ile artık
    tek geçişte (O(n)) hesaplanıyor. Sonuç değeri (main/signal/histogram)
    matematiksel olarak aynıdır -- sadece hesaplama verimliliği değişti.
    """
    if len(values) < 35:
        return 0, 0, 0

    ema12 = ema_series(values, 12)
    ema26 = ema_series(values, 26)

    macd_line = [
        e12 - e26
        for e12, e26 in zip(ema12, ema26)
        if e12 is not None and e26 is not None
    ]

    if not macd_line:
        return 0, 0, 0

    main = macd_line[-1]
    signal = ema(macd_line, 9)
    return main, signal, main - signal


def bb(values: list[float], period: int = 20, k: float = 2) -> tuple[float, float, float]:
    if len(values) < period:
        return 0, 0, 0

    sample = values[-period:]
    middle = avg(sample)
    deviation = avg([(x - middle) ** 2 for x in sample]) ** 0.5

    return middle - k * deviation, middle, middle + k * deviation


def adx(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> tuple[float, float, float]:
    """Standart Wilder ADX.

    DÜZELTME (kritik): eski implementasyon, DX'i (Directional Index) hiç
    Wilder ile yumuşatmadan doğrudan "ADX" olarak döndürüyordu -- yani
    fonksiyonun döndürdüğü ilk değer aslında ADX değil, TEK BARLIK DX'ti.
    Gerçek ADX, DX serisinin kendisinin de Wilder ortalamasıyla
    yumuşatılmasıyla elde edilir (iki katmanlı yumuşatma: önce TR/+DM/-DM,
    sonra DX). Bu yüzden eski değerler borsa uygulamalarındaki (TradingView
    vb.) ADX'ten sistematik olarak farklı ve daha oynak (volatile) çıkıyordu.
    """
    if len(closes) < period * 2 + 1:
        return 0, 0, 0

    trs, plus_dms, minus_dms = [], [], []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dms.append(up if up > down and up > 0 else 0)
        minus_dms.append(down if down > up and down > 0 else 0)

    # Wilder smoothing (running sum) seed: ilk `period` barın toplamı.
    atr = sum(trs[:period])
    plus_sum = sum(plus_dms[:period])
    minus_sum = sum(minus_dms[:period])

    def _dx(atr_v: float, plus_v: float, minus_v: float) -> tuple[float, float, float]:
        if atr_v <= 0:
            return 0.0, 0.0, 0.0
        plus_di = 100 * plus_v / atr_v
        minus_di = 100 * minus_v / atr_v
        total = plus_di + minus_di
        dx_v = 100 * abs(plus_di - minus_di) / total if total else 0.0
        return dx_v, plus_di, minus_di

    dx, plus_di, minus_di = _dx(atr, plus_sum, minus_sum)
    dx_series = [dx]

    for i in range(period, len(trs)):
        atr = atr - (atr / period) + trs[i]
        plus_sum = plus_sum - (plus_sum / period) + plus_dms[i]
        minus_sum = minus_sum - (minus_sum / period) + minus_dms[i]
        dx, plus_di, minus_di = _dx(atr, plus_sum, minus_sum)
        dx_series.append(dx)

    # İkinci katman: DX serisinin kendisi Wilder ortalamasıyla yumuşatılır.
    if len(dx_series) < period:
        adx_value = avg(dx_series)
    else:
        adx_value = avg(dx_series[:period])
        for x in dx_series[period:]:
            adx_value = (adx_value * (period - 1) + x) / period

    return adx_value, plus_di, minus_di


# ============================================================================
# YENİ (V25): Birikim/akümülasyon tespiti için gösterge seti.
# Amaç: "hareket zaten başladı" değil "hareket henüz başlamadı ama biri
# sessizce topluyor olabilir" sorusuna cevap vermek -- tanınmış, kanıtlanmış
# yöntemlere dayanıyor (Granville'in OBV'si, Carter'ın TTM Squeeze'i,
# Wilder'ın divergence takibi). Mevcut RSI/ADX/MACD/BB'ye dokunulmadı.
# ============================================================================

def atr(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> float:
    """Wilder ATR (Average True Range) -- adx() içindeki TR yumuşatmasıyla
    aynı yöntem, tek başına kullanılabilir hale getirildi (Keltner Channel
    için gerekli).
    """
    if len(closes) < period + 1:
        return 0.0

    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))

    atr_value = sum(trs[:period]) / period
    for x in trs[period:]:
        atr_value = (atr_value * (period - 1) + x) / period

    return atr_value


def obv(closes: list[float], volumes: list[float]) -> list[float]:
    """Joe Granville'in On-Balance Volume'u (1963). Fiyat yükselirse o
    barın hacmini ekle, düşerse çıkar, sabitse dokunma. Granville'in tezi:
    hacim akışı fiyattan ÖNCE döner -- fiyat henüz yatayken OBV'nin
    yükselmesi, sessiz bir birikimin klasik işaretidir.
    """
    if not closes or not volumes:
        return []

    result = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            result.append(result[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            result.append(result[-1] - volumes[i])
        else:
            result.append(result[-1])

    return result


def keltner_channel(highs: list[float], lows: list[float], closes: list[float],
                     period: int = 20, multiplier: float = 1.5) -> tuple[float, float, float]:
    """Keltner Channel: EMA orta çizgi + ATR tabanlı bantlar.

    Bollinger Bands (standart sapma tabanlı) bu kanalın TAMAMEN İÇİNE
    sıkışırsa, bu John Carter'ın "TTM Squeeze" olarak tanındığı, momentum
    piyasalarında yaygın kabul gören bir "patlamaya hazır" sinyalidir --
    volatilitenin normalin de altına indiği, sıkışmanın gerçekten anormal
    olduğu anlamına gelir (sadece dar bir BB yetmez).
    """
    if len(closes) < period:
        return 0.0, 0.0, 0.0

    middle = ema(closes, period)
    band = multiplier * atr(highs, lows, closes, period)

    if band <= 0:
        return middle, middle, middle

    return middle - band, middle, middle + band


def macd_hist_series(values: list[float]) -> list[float]:
    """MACD histogramının TAM SERİSİ (tek değer değil) -- divergence
    tespiti için gerekli. `macd()` ile aynı hesaplamayı kullanır, sadece
    her nokta için sonucu döndürür.
    """
    if len(values) < 35:
        return []

    ema12 = ema_series(values, 12)
    ema26 = ema_series(values, 26)

    macd_line = [
        e12 - e26 if e12 is not None and e26 is not None else None
        for e12, e26 in zip(ema12, ema26)
    ]
    valid = [v for v in macd_line if v is not None]

    if len(valid) < 9:
        return []

    signal_series = ema_series(valid, 9)
    return [
        v - s if s is not None else 0.0
        for v, s in zip(valid, signal_series)
    ]


def bullish_divergence(price_series: list[float], indicator_series: list[float],
                        lookback: int = 40, pivot_window: int = 3) -> bool:
    """Basit, pivot-tabanlı bullish (pozitif) uyumsuzluk tespiti.

    Wilder'ın RSI makalesinde tanımladığı klasik desen: fiyat DAHA DÜŞÜK
    bir dip yaparken gösterge (RSI/MACD/OBV) DAHA YÜKSEK bir dip yapıyorsa,
    bu satış baskısının tükendiğinin işaretidir. Burada son `lookback` bar
    içindeki son iki "yerel dip"i (pivot low) karşılaştırıyoruz.

    Not: Bu basitleştirilmiş bir tespit yöntemidir -- profesyonel grafik
    yazılımlarındaki gibi çizgi/trendline analizi yapmaz, sadece iki pivot
    noktasını karşılaştırır. Yanlış pozitif üretebilir, tek başına karar
    verici değil, sadece bir "kriter" olarak kullanılmalı.
    """
    if len(price_series) < lookback or len(indicator_series) < lookback:
        return False

    window_p = price_series[-lookback:]
    window_i = indicator_series[-lookback:]

    def pivot_lows(series: list[float], w: int) -> list[int]:
        pivots = []
        for i in range(w, len(series) - w):
            segment = series[i - w:i + w + 1]
            if series[i] == min(segment):
                pivots.append(i)
        return pivots

    lows = pivot_lows(window_p, pivot_window)
    if len(lows) < 2:
        return False

    i1, i2 = lows[-2], lows[-1]
    return window_p[i2] < window_p[i1] and window_i[i2] > window_i[i1]


def vwap(highs: list[float], lows: list[float], closes: list[float],
          volumes: list[float]) -> float:
    """Hacim Ağırlıklı Ortalama Fiyat -- kurumsal/algoritmik trader'ların
    en yaygın kullandığı referans noktalarından biri. Fiyatın VWAP'ın
    üzerinde olması "şu anki fiyat, bu pencerede işlem gören ortalama
    değerden daha pahalı" demektir (alıcı baskısı VWAP'ı geçmiş).
    Klasik tanım: tipik fiyat = (high+low+close)/3, hacimle ağırlıklandırılır.
    """
    if not closes or not volumes or len(closes) != len(volumes):
        return 0.0

    cum_pv = 0.0
    cum_v = 0.0
    for h, l, c, v in zip(highs, lows, closes, volumes):
        typical = (h + l + c) / 3
        cum_pv += typical * v
        cum_v += v

    return cum_pv / cum_v if cum_v else 0.0
    

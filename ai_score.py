def ai_early_score(data):
    """
    V29 AI-style erken hareket skoru.
    0-100 arası sonuç üretir.
    """

    score = 0
    reasons = []

    def add(condition, points, text):
        nonlocal score
        if condition:
            score += points
            reasons.append(text)

    # 🌀 KIVRIM
    add(data.get("kivrim_pre_turn"), 18, "PRE-KIVRIM")
    add(data.get("kivrim_turning"), 15, "EMA7 KIVRIM")
    add(data.get("kivrim_accelerating"), 8, "KIVRIM İVMESİ")

    # 📈 YAPI
    add(data.get("kivrim_higher_low"), 12, "HIGHER-LOW")
    add(data.get("kivrim_reclaim_ema7"), 7, "EMA7 GERİ ALIM")

    # RSI / MACD
    add(data.get("kivrim_rsi_turning"), 8, "RSI DÖNÜŞÜ")
    add(data.get("kivrim_macd_recovering"), 8, "MACD DÖNÜŞÜ")

    # 💧 HACİM
    vr = float(data.get("volume_ratio", 0) or 0)

    if 1.15 <= vr <= 2.5:
        score += 10
        reasons.append("ERKEN HACİM")

    # Geç hacim patlamasını cezalandır
    if vr >= 3:
        score -= 12
        reasons.append("AŞIRI HACİM")

    # 📊 FİYAT HAREKETİ
    move3 = float(data.get("move_3", 0) or 0)
    move6 = float(data.get("move_6", 0) or 0)

    if move3 < 2.5:
        score += 6
        reasons.append("HAREKET ERKEN")

    if move6 < 4:
        score += 4

    # 🚨 GEÇ KALMA CEZASI
    if move3 >= 3:
        score -= 12

    if move6 >= 5:
        score -= 15

    if data.get("late"):
        score -= 25
        reasons.append("GEÇ KALDI")

    score = max(0, min(100, score))

    if score >= 75:
        signal = "VERY EARLY"
    elif score >= 65:
        signal = "EARLY BUY"
    elif score >= 55:
        signal = "WATCH"
    else:
        signal = "PASS"

    return {
        "ai_score": score,
        "ai_signal": signal,
        "ai_reasons": reasons,
    }

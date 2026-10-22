# Weinstein Weekly — Large/Mega Cap Swing

نسخة مجانية بالكامل ولا تحتاج Finviz Elite أو أي API key.

## المصادر
- Nasdaq Stock Screener: اسم الشركة، القطاع، والقيمة السوقية لتحديد Large/Mega Caps.
- Yahoo Chart API: تاريخ OHLCV اليومي، ويُحوّل داخليًا إلى Weekly.
- Stooq: مصدر احتياطي للشموع التاريخية إذا تعطل Yahoo.

## مهم
المنهج أسبوعي، لذلك لا نحتاج أسعار لحظية. آخر إغلاق يومي/أسبوعي كافٍ للتحليل.

## التقييم
- Stage Quality: 35%
- Relative Strength vs SPY: 20%
- Weekly Base: 15%
- Volume: 10%
- Distance from 30-week MA: 10%
- Market Regime: 10%

## Render
لا تضف أي Environment Variable.
فقط ارفع المشروع وشغله.

## الكون المستهدف
- افتراضي: Market Cap >= $50B
- خيار Mega focus: Market Cap >= $100B

الأداة تعليمية وليست توصية شراء أو بيع.

## عرض السعر
- تحليل السهم يعرض آخر تداول متاح من Yahoo 1m مع includePrePost.
- يشمل Pre-market وRegular وAfter-hours عند توفرها.
- خارج الجلسات يعرض آخر سعر/إغلاق متاح.
- حساب Stage و30W لا يستخدم السعر الممتد؛ يبقى مبنيًا على الإغلاقات المكتملة.


## Overnight / 24H V4
- Overnight 8:00 PM-4:00 AM ET: TradingView BOATS (Blue Ocean ATS) — best effort.
- Pre-market / Regular / After-hours: Yahoo Chart 1m with includePrePost.
- Closed / source unavailable: falls back to the latest available price/close.
- No API key or paid subscription is required by the app.
- The BOATS scanner route is an unofficial TradingView endpoint; if TradingView changes it, the site falls back automatically rather than breaking.
- Stage Analysis remains based on completed bars only; overnight price is display-only.


## Large/Mega company directory V6
- New tab: "كل الشركات $10B+"
- Includes every company returned by Nasdaq Stock Screener with market cap >= $10B.
- No top-N truncation.
- Search by ticker, company name, sector, or industry.
- Click a row to analyze the stock directly.
- Main weekly scanner now defaults to the agreed Large/Mega universe ($10B+).


## V6 change
- The entire target universe now starts at $10B.
- Default analysis threshold: $10B.
- Default scanner threshold: $10B.
- Company directory: only $10B+.
- Optional filters remain $50B+, $100B+, and $200B+ Mega.

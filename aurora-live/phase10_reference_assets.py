from __future__ import annotations

from typing import Any

import app


_ROWS = """
S&P 500|SPX|index|40.71|-74.01
Nasdaq Composite|IXIC|index|40.71|-74.01
Nasdaq 100|NDX|index|40.71|-74.01
Dow Jones Industrial Average|DJI|index|40.71|-74.01
Russell 2000|RUT|index|41.88|-87.63
NYSE Composite|NYA|index|40.71|-74.01
S&P MidCap 400|MID|index|40.71|-74.01
S&P SmallCap 600|SML|index|40.71|-74.01
VIX|VIX|volatility|41.88|-87.63
TSX Composite|GSPTSE|index|43.65|-79.38
S&P/TSX 60|TX60|index|43.65|-79.38
Bovespa|BVSP|index|-23.55|-46.63
IPC Mexico|MXX|index|19.43|-99.13
Merval|MERV|index|-34.60|-58.38
IPSA Chile|IPSA|index|-33.45|-70.67
FTSE 100|FTSE|index|51.51|-0.09
FTSE 250|FTMC|index|51.51|-0.09
DAX|DAX|index|50.11|8.68
MDAX|MDAX|index|50.11|8.68
CAC 40|CAC|index|48.87|2.34
Euro Stoxx 50|SX5E|index|50.11|8.68
Stoxx Europe 600|SXXP|index|50.11|8.68
IBEX 35|IBEX|index|40.42|-3.70
FTSE MIB|FTMIB|index|45.46|9.19
AEX|AEX|index|52.37|4.90
BEL 20|BFX|index|50.85|4.35
SMI|SSMI|index|47.38|8.54
OMX Stockholm 30|OMXS30|index|59.33|18.07
OBX|OBX|index|59.91|10.75
OMX Helsinki 25|OMXH25|index|60.17|24.94
WIG 20|WIG20|index|52.23|21.01
ATX|ATX|index|48.21|16.37
PSI 20|PSI20|index|38.72|-9.14
Nikkei 225|N225|index|35.68|139.76
TOPIX|TOPX|index|35.68|139.76
Hang Seng|HSI|index|22.28|114.16
Hang Seng Tech|HSTECH|index|22.28|114.16
Shanghai Composite|SSEC|index|31.23|121.47
CSI 300|CSI300|index|31.23|121.47
Shenzhen Component|SZCOMP|index|22.54|114.06
KOSPI|KS11|index|37.57|126.98
KOSDAQ|KQ11|index|37.57|126.98
Taiwan Weighted|TWII|index|25.04|121.56
Sensex|SENSEX|index|19.08|72.88
Nifty 50|NIFTY|index|19.08|72.88
Nifty Bank|NIFTYBANK|index|19.08|72.88
ASX 200|AXJO|index|-33.87|151.21
NZX 50|NZ50|index|-36.85|174.76
Straits Times|STI|index|1.35|103.82
Jakarta Composite|JKSE|index|-6.21|106.85
Kuala Lumpur Composite|KLSE|index|3.14|101.69
SET Index|SET|index|13.76|100.50
PSEi|PSEI|index|14.60|120.98
VN Index|VNINDEX|index|10.82|106.63
Tadawul All Share|TASI|index|24.71|46.67
Dubai Financial Market|DFMGI|index|25.20|55.27
Abu Dhabi Securities Exchange|FTFADGI|index|24.45|54.38
Qatar Exchange|QSI|index|25.29|51.53
Borsa Istanbul 100|XU100|index|41.01|28.97
TA-35|TA35|index|32.08|34.78
EGX 30|EGX30|index|30.04|31.24
JSE Top 40|JTOPI|index|-26.20|28.04
Nairobi All Share|NASI|index|-1.29|36.82
NGX All Share|NGXASI|index|6.45|3.39
WTI Crude|CL|commodity|29.76|-95.37
Brent Crude|BZ|commodity|51.51|-0.09
Dubai Crude|DUBAI|commodity|25.20|55.27
Natural Gas|NG|commodity|29.76|-95.37
Heating Oil|HO|commodity|40.71|-74.01
RBOB Gasoline|RB|commodity|40.71|-74.01
Gold|GC|commodity|40.71|-74.01
Silver|SI|commodity|40.71|-74.01
Copper|HG|commodity|41.88|-87.63
Platinum|PL|commodity|40.71|-74.01
Palladium|PA|commodity|40.71|-74.01
Iron Ore|IRON|commodity|-33.87|151.21
Aluminum|ALI|commodity|51.51|-0.09
Nickel|NICKEL|commodity|51.51|-0.09
Wheat|ZW|commodity|41.88|-87.63
Corn|ZC|commodity|41.88|-87.63
Soybeans|ZS|commodity|41.88|-87.63
Coffee|KC|commodity|40.71|-74.01
Cocoa|CC|commodity|40.71|-74.01
Cotton|CT|commodity|40.71|-74.01
Sugar|SB|commodity|40.71|-74.01
Bitcoin|BTC|crypto|0|0
Ether|ETH|crypto|0|0
Solana|SOL|crypto|0|0
BNB|BNB|crypto|0|0
XRP|XRP|crypto|0|0
EUR/USD|EURUSD|fx|50.11|8.68
USD/JPY|USDJPY|fx|35.68|139.76
GBP/USD|GBPUSD|fx|51.51|-0.09
USD/CHF|USDCHF|fx|47.38|8.54
AUD/USD|AUDUSD|fx|-33.87|151.21
USD/CAD|USDCAD|fx|43.65|-79.38
USD/CNY|USDCNY|fx|31.23|121.47
USD/INR|USDINR|fx|19.08|72.88
USD/PKR|USDPKR|fx|24.86|67.01
US 2Y Treasury|US2Y|rates|38.90|-77.04
US 10Y Treasury|US10Y|rates|38.90|-77.04
US 30Y Treasury|US30Y|rates|38.90|-77.04
German 10Y Bund|DE10Y|rates|50.11|8.68
UK 10Y Gilt|GB10Y|rates|51.51|-0.09
Japan 10Y JGB|JP10Y|rates|35.68|139.76
"""


def reference_assets() -> list[dict[str, Any]]:
    rows = []
    for line in _ROWS.strip().splitlines():
        name, symbol, asset_class, latitude, longitude = line.split("|")
        rows.append({
            "id": app.stable_id("market", symbol),
            "type": "market",
            "name": name,
            "symbol": symbol,
            "asset_class": asset_class,
            "latitude": float(latitude),
            "longitude": float(longitude),
            "provenance": "curated market reference catalog",
        })
    return rows

from __future__ import annotations

from collections import Counter
from typing import Any

import app
from phase10_catalog import static_assets as base_static_assets

MARKET_SYMBOLS = """
SPX NDX DJI RUT VIX FTSE DAX CAC STOXX50 AEX IBEX MIB SMI OMX N225 TOPIX HSI HSCEI SSEC SZSE CSI300 KOSPI TWII STI SENSEX NIFTY ASX200 NZX50 TSX BOVESPA MEXBOL MERVAL IPSA COLCAP JSE EGX30 TA35 TASI ADX DFM QE MSM30 KSE100 CSEALL SET KLCI IDX PSEI VNINDEX MSCIWORLD MSCIEM MSCIEAFE US10Y US02Y US30Y DE10Y GB10Y JP10Y CN10Y CL BZ NG HO RB GC SI HG PL PA ALUMINUM NICKEL ZINC LEAD TIN IRONORE COCOA COFFEE SUGAR COTTON CORN WHEAT SOYBEAN RICE OATS LUMBER LIVE_CATTLE LEAN_HOGS EURUSD USDJPY GBPUSD AUDUSD USDCAD USDCHF NZDUSD USDCNY USDINR USDBRL USDMXN USDZAR USDTRY USDKRW USDSGD BTC ETH SOL BNB XRP ADA DOGE AVAX DOT LINK LTC BCH XMR TON TRX UNI ATOM AAPL MSFT NVDA AMZN GOOGL META TSLA BRK_B JPM V MA XOM UNH JNJ PG HD COST ORCL NFLX AMD INTC TSM ASML SAP BABA TCEHY NVO LLY SHEL TM SONY SAMSUNG RELIANCE ARAMCO""".split()

MARKET_HUBS = [(40.71,-74.01),(51.51,-0.09),(50.11,8.68),(35.68,139.76),(22.28,114.16),(31.23,121.47),(19.08,72.88),(-33.87,151.21),(43.65,-79.38),(-23.55,-46.63),(25.2,55.27),(1.29,103.85)]


def all_static_assets() -> list[dict[str, Any]]:
    rows=base_static_assets();existing={str(row.get('symbol')) for row in rows if row.get('type')=='market'}
    for index,symbol in enumerate(MARKET_SYMBOLS):
        if symbol in existing:continue
        lat,lon=MARKET_HUBS[index%len(MARKET_HUBS)]
        rows.append({'id':app.stable_id('market',symbol),'type':'market','name':symbol.replace('_',' '),'symbol':symbol,'latitude':lat,'longitude':lon,'tracking':'reference_asset'})
    return rows


def static_counts() -> dict[str,int]:
    return dict(Counter(str(row.get('type')) for row in all_static_assets()))

# GOOG 长期数据管道 - 数据血统与爬取方法

## 目录结构
```
data/GOOG/
├── options_chain/      # 期权链快照 CBOE延时15min + Yahoo v7备份 + web_search fallback
├── 13F/                # 机构持仓 13F-HR
├── earnings/           # 财报日历 + 财报数据
├── financials/         # SEC EDGAR 10-K/Q companyfacts
├── short_interest/     # 做空数据 FINRA/MarketBeat
├── RS/                 # 相对强弱 vs SPY/QQQ/MAG7
├── insider/            # 内部人 Form4
├── news/               # 新闻/业务拆分/反垄断
└── macro/              # 宏观 FOMC/CPI/利率/板块
```

## 数据源与爬取方法（长期用）

### 1. 期权链 options_chain
- **主源**: CBOE `https://cdn.cboe.com/api/global/delayed_quotes/options/GOOG.json` 延时15min全链IV/希腊/OI/量
- **备份**: Yahoo `https://query2.finance.yahoo.com/v7/finance/options/GOOG` 按到期分页
- **沙箱问题**: TLS/SSL EOF被关，需代理或本机跑 `curl --insecure` 或 `yfinance` + `curl_cffi`
- **本库实现**: `scripts/GOOG/crawler/options_cboe.py` 三次重试 + fallback web_search聚合
- **落盘**: `GOOG_options_chain_YYYYMMDD_cboe.md` 按 CHAIN_SNAPSHOT_TEMPLATE.md 六件套 + raw json
- **长期**: 每周三主操作日跑 `python crawler/options_cboe.py`，需在本地Windows/Mac跑，沙箱仅作fallback

### 2. 13F机构持仓
- **主源**: SEC EDGAR `https://data.sec.gov/api/xbrl/companyfacts/CIK0001652044.json` + 13F-HR filings `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=1652044&type=13F`
- **备份**: Fintel `https://fintel.io/so/us/googl` + Yahoo holders `https://finance.yahoo.com/quote/GOOG/holders/`
- **本库**: `sec_edgar.py` + web_search聚合 5791家 4.55B股
- **长期**: 每季度13F截止后45天更新，需解析 13F-HR XML

### 3. 财报与财务
- **财报日历**: WallStreetHorizon `https://www.wallstreethorizon.com/alphabet-earnings-calendar` + MarketBeat `https://www.marketbeat.com/stocks/NASDAQ/GOOG/earnings/`
- **财务数据**: SEC 10-K/Q + Yahoo Finance key-statistics
- **本库**: `news_macro.py` 已含 Q2 2026 $119.8B +24% Cloud $24.8B +82% backlog $514B YouTube $11.055B +13%
- **长期**: 每季财报后跑 `quant_core.py` 更新State/筹码 + `weekly_rs_update.py`

### 4. 做空
- **主源**: FINRA `https://api.finra.org/data/group/otcMarket/name/regShoDaily` + MarketBeat `https://www.marketbeat.com/stocks/NASDAQ/GOOG/short-interest/`
- **本库**: web_search已抓 0.37% GOOG 0.77% GOOGL ratio 2.1/2.9 borrow 0.25%
- **落盘**: `short_interest/GOOG_short_YYYYMMDD.json`

### 5. 相对强弱RS
- **主源**: yfinance `GOOG, SPY, QQQ` OHLC + Stooq `https://stooq.com/q/d/l/?s=goog.us&i=d` CSV备份
- **沙箱**: yfinance SSL失败，Stooq SSL失败，改用web_search + portfolioslab
- **本库**: `market_data.py` + `GOOG_RS_YYYYMMDD.json` 含YTD/1Y/相关性0.74 beta1.23 Sharpe等
- **长期**: 每周跑 `weekly_rs_update.py` 拉SPY/QQQ对比，30天超额

### 6. 内部人
- **主源**: SEC Form4 `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=1652044&type=4`
- **本库**: 需补充，当前用Investopedia Top holders Larry Page/Brin 42%
- **落盘**: `insider/GOOG_insider_YYYYMMDD.json`

### 7. 新闻与业务
- **主源**: web_search + fetch_page Yahoo Finance
- **本库**: `news/GOOG_news_YYYYMMDD.json` 已含Cloud/YouTube/Waymo/反垄断
- **长期**: 每日web_search `GOOG Cloud revenue`, `GOOG antitrust`, `Waymo valuation`

### 8. 宏观
- **主源**: FRED `https://fred.stlouisfed.org/` + FOMC calendar
- **本库**: `macro/` 待补充，需FOMC/CPI/利率对广告周期影响

## 沙箱限制与 workaround
- 沙箱出站TLS常被关（CBOE/SEC/Yahoo/Stooq均EOF），yfinance依赖curl_cffi也失败
- **workaround**: 
  1. 本机跑 crawler，推Git
  2. 用 `web_search` + `fetch_page` 作为fallback（已实现，数据来自Barchart/UnusualWhales/Fintel/Yahoo页面抓取）
  3. 用 `gh` + GitHub Actions定时跑（Actions网络正常）

## 长期使用建议
1. **每周三**主操作日：`python scripts/GOOG/crawler/master.py` + `python scripts/GOOG/run_goog_full_quant_20260916.py` + `python scripts/GOOG/gen_html_20260916.py`
2. **每季财报**：更新 `GOOG N=3000.csv` + 跑 `quant_core.py` + 更新HTML V4/V5
3. **每月**：13F更新 + short interest + insider
4. **数据血统**：官方OHLC为锚，链快照为辅，web_search为fallback，Key Results唯一在册
5. **防线**：panel_check.py + INTEGRITY_AUDIT_METHOD双轮法 + 勘误只增不删

## 已落盘数据（V2/V3）
- `options_chain/GOOG_options_chain_20260916_web_search.md` IV 39.62% Rank 74.9% 600K+ OI 8M+
- `13F/GOOG_13F_20260916.json` 5791家 BlackRock 6.69% Vanguard 5.55%
- `RS/GOOG_RS_20260916.json` YTD 8-10% vs QQQ 16% vs SPY 10% 1Y 61-76% vs QQQ 26%
- `news/GOOG_news_20260916.json` Q2 $119.8B Cloud $24.8B +82% backlog $514B YouTube $11B Waymo $126B 反垄断上诉至2028+

## 待补齐（需本机或Actions跑）
- `financials/GOOG_companyfacts_*.json` SEC EDGAR companyfacts
- `earnings/GOOG_earnings_*.json` 完整财报历史
- `short_interest/GOOG_short_*.json` FINRA
- `insider/GOOG_insider_*.json` Form4
- `macro/GOOG_macro_*.json` FOMC/CPI

## 一句跑一下=本库全量刷新
```bash
python scripts/GOOG/crawler/master.py
python scripts/GOOG/run_goog_full_quant_20260916.py
python scripts/GOOG/gen_html_20260916.py
# 生成新HTML V4
```

## 引用
- CBOE: https://cdn.cboe.com/api/global/delayed_quotes/options/GOOG.json
- SEC: https://data.sec.gov/api/xbrl/companyfacts/CIK0001652044.json
- Yahoo: https://finance.yahoo.com/quote/GOOG/
- Barchart: https://www.barchart.com/stocks/quotes/GOOG/expected-move
- MarketBeat: https://www.marketbeat.com/stocks/NASDAQ/GOOG/short-interest/
- Fintel: https://fintel.io/so/us/googl

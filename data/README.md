Put historical bar CSVs here (git-ignored).

From TradingView: open the chart (e.g. your broker's NAS100 at 1m or 5m),
then chart menu → Export chart data → keep "ISO time" or Unix time.
Load with: atlas backtest --data data/NAS100_5m.csv --tf 5m

Note: TradingView only exports bars loaded on the chart. For years of
intraday history you'll want a dedicated data source (see README).

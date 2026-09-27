RECONCILIATION EXERCISE — DATA FILES
=====================================

You have daily market data for two assets (BTCUSD, ETHUSD) from three venues plus
an external reference price feed:

  binance_<ASSET>.csv     columns: timestamp, open, high, low, close, volume
  kraken_<ASSET>.csv      columns: date, open, high, low, close, volume
  coinbase_<ASSET>.csv    columns: time, open, high, low, close, volume
  reference_<ASSET>.csv   columns: date, reference_close_usd

The period is roughly 2026-01-01 to 2026-04-30 (daily).

These are real-world-shaped files: the sources DO NOT agree perfectly, column names and
formats differ, and some files contain errors or quirks. Treating the differences correctly
is the whole exercise. We have not told you what the issues are — finding and handling them
is your job.

All figures are illustrative and generated for this exercise; they are not real market prices.

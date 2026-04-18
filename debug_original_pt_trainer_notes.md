# Purpose

This relates to a clarification question related to the training process documented in Issue #27 here: https://github.com/garagesteve1155/PowerTrader_AI/issues/27

The following is an extract of the console output from pt_trainer training filtered by lines containing "[DEBUG]" whilst training the BTC coin.

# Observations

```
1. The 1hour timeframe trains on the same 25% of prices two times (restart count <= 2), then on restart count 3, it processes 100% of the 1hour prices.

2. The trainer processes 7 timeframes (see seven groups of output below), but they are not the timeframes you expect.

	  For example:
	  - group 1 process 25% of 1hour, then 25% of 1hour  then all of 1hour
	  - group 2 process 25% of 1hour, then 25% of 2hour  then all of 2hour
	  - group 3 process 25% of 1hour, then 25% of 4hour  then all of 4hour
	  - group 4 process 25% of 1hour, then 25% of 8hour  then all of 8hour
	  - group 5 process 25% of 1hour, then 25% of 12hour then all of 12hour
	  - group 6 process 25% of 1hour, then 25% of 1day   then all of 1day
	  - group 7 process 25% of 1hour, then 25% of 1week  then all of 1week
```
	  
### The clarification is to check the below is what the author expected as the design doesn't seem correct.

```
[DEBUG] Training timeframe 1hour, restart count:0
[DEBUG] Coin BTC-GBP: 25.0% of 13121 prices for timeframe 1hour have been processed. Restarting.
[DEBUG] Training timeframe 1hour, restart count:1
[DEBUG] Coin BTC-GBP: 25.0% of 13121 prices for timeframe 1hour have been processed. Restarting.
[DEBUG] Training timeframe 1hour, restart count:2
[DEBUG] Coin BTC-GBP: 100% of 13121 prices for timeframe 1hour have been processed. Saving training status then exiting.


[DEBUG] Training timeframe 1hour, restart count:0
[DEBUG] Coin BTC-GBP: 25.0% of 13121 prices for timeframe 1hour have been processed. Restarting.
[DEBUG] Training timeframe 2hour, restart count:1
[DEBUG] Coin BTC-GBP: 25.0% of 6474 prices for timeframe 2hour have been processed. Restarting.
[DEBUG] Training timeframe 2hour, restart count:2
[DEBUG] Coin BTC-GBP: 100% of 6474 prices for timeframe 2hour have been processed. Saving training status then exiting.


[DEBUG] Training timeframe 1hour, restart count:0
[DEBUG] Coin BTC-GBP: 25.0% of 13121 prices for timeframe 1hour have been processed. Restarting.
[DEBUG] Training timeframe 4hour, restart count:1
[DEBUG] Coin BTC-GBP: 25.0% of 3150 prices for timeframe 4hour have been processed. Restarting.
[DEBUG] Training timeframe 4hour, restart count:2
[DEBUG] Coin BTC-GBP: 100% of 3150 prices for timeframe 4hour have been processed. Saving training status then exiting.


[DEBUG] Training timeframe 1hour, restart count:0
[DEBUG] Coin BTC-GBP: 25.0% of 13121 prices for timeframe 1hour have been processed. Restarting.
[DEBUG] Training timeframe 8hour, restart count:1
[DEBUG] Coin BTC-GBP: 25.0% of 1619 prices for timeframe 8hour have been processed. Restarting.
[DEBUG] Training timeframe 8hour, restart count:2
[DEBUG] Coin BTC-GBP: 100% of 1619 prices for timeframe 8hour have been processed. Saving training status then exiting.


[DEBUG] Training timeframe 1hour, restart count:0
[DEBUG] Coin BTC-GBP: 25.0% of 13121 prices for timeframe 1hour have been processed. Restarting.
[DEBUG] Training timeframe 12hour, restart count:1
[DEBUG] Coin BTC-GBP: 25.0% of 1050 prices for timeframe 12hour have been processed. Restarting.
[DEBUG] Training timeframe 12hour, restart count:2
[DEBUG] Coin BTC-GBP: 100% of 1050 prices for timeframe 12hour have been processed. Saving training status then exiting.


[DEBUG] Training timeframe 1hour, restart count:0
[DEBUG] Coin BTC-GBP: 25.0% of 13121 prices for timeframe 1hour have been processed. Restarting.
[DEBUG] Training timeframe 1day, restart count:1
[DEBUG] Coin BTC-GBP: 25.0% of 1049 prices for timeframe 1day have been processed. Restarting.
[DEBUG] Training timeframe 1day, restart count:2
[DEBUG] Coin BTC-GBP: 100% of 1049 prices for timeframe 1day have been processed. Saving training status then exiting.


[DEBUG] Training timeframe 1hour, restart count:0
[DEBUG] Coin BTC-GBP: 25.0% of 13121 prices for timeframe 1hour have been processed. Restarting.
[DEBUG] Training timeframe 1week, restart count:1
[DEBUG] Coin BTC-GBP: 24.8% of 149 prices for timeframe 1week have been processed. Restarting.
[DEBUG] Training timeframe 1week, restart count:2
[DEBUG] Coin BTC-GBP: 100% of 149 prices for timeframe 1week have been processed. Saving training status then exiting.
```

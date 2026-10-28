
# PowerTrader_AI
Fully automated crypto trading powered by a custom price prediction AI and a structured/tiered DCA system.

I have not checked any PowerTrader AI forks and cannot confirm or deny their legitimacy.

This is my personal trading bot that I decided to make open source. I made this strategy to match my personal goals. This system is meant to be a foundation/framework for you to build your dream bot! You are responsible for all financial and security risks associated with PowerTrader AI.
I know there are "commonly essential" trading features that are missing (like no stop loss for example). This is by design because many of those things would just not work with this system's strategy as it stands, for my personal reasons below:

I do not believe in selling worthwhile coins at a loss (and why would you trade anything besides worthwhile coins with a trading bot, anyways???).
I DO believe in crypto. I'd rather just wait and maybe add more money to my account if need be so that the bot can buy even more of the coin while the price is down.
I personally feel like many of those common things people use, like stop loss, are actually a trick or something, and I personally have absolutely no problem adding more money to my account to afford more DCA or having to wait for extended periods of time, if need be. In my opinion, anything else is just greedy and desperate, which is the exact OPPOSITE of needed attributes for long term growth. Plus, this is just spot trading...
there's no worry of liquidation and it feels to me like many "risk management" tactics are really only meant for futures trading but people blindly apply them to spot trading when it just plain isn't necessary.
I know the AI and the trading strategy are extremely simple because I'm the one that designed and made them. I've been developing this specific trading strategy for almost a decade and the design of the AI system for the last few years. The overall strategy is based on what ACTUALLY works from real trading experience, not just stuff I read in LLM responses or search engine results.


Ok now that all of that is out of the way...
I am not selling this trading bot. This trading bot is not a product. This system is for experimentation and education. The only reason you would EVER send me money is if you are voluntarily donating (donation routes can be found at the bottom of this readme :) ) or buying merch. Do not fall for any scams! PowerTrader AI is COMPLETELY FREE FOREVER!!!!
IMPORTANT: This software places real trades automatically. You are responsible for everything it does to your money and your account. Keep your API keys private. I am not giving financial advice. I am not responsible for any losses incurred or any security breaches to your computer (the code is entirely open source and can be confirmed non-malicious). You are fully responsible for doing your own due diligence to learn and understand this trading system and to use it properly.
You are fully responsible for all of your money and all of the bot's actions, and any gains or losses.
“It’s an instance-based (kNN/kernel-style) predictor with online per-instance reliability weighting, used as a multi-timeframe trading signal.” - ChatGPT on the type of AI used in this trading bot.

So what exactly does that mean?

When people think AI, they usually think about LLM style AIs and neural networks. What many people don't realize is there are many types of Artificial Intelligence and Machine Learning - and the one in my trading system falls under the "Other" category.
When training for a coin, it goes through the entire history for that coin on multiple timeframes and saves each pattern it sees, along with what happens on the next candle AFTER the pattern. It uses these saved patterns to generate a predicted candle by taking a weighted average of the closest matches in memory to the current pattern in time. This weighted average output is done once for each timeframe, from 1 hour up to 1 week. Each timeframe gets its own predicted candle.
The low and high prices from these candles are what are shown as the blue and orange horizontal lines on the price charts.
After a candle closes, it checks what happened against what it predicted, and adjusts the weight for each "memory pattern" that was used to generate the weighted average, depending on how accurate each pattern was compared to what actually happened.

Yes, it is EXTREMELY simple. Yes, it is STILL considered AI.

Here is how the trading bot utilizes the price prediction ai to automatically make trades:
For determining when to start trades, the AI's Thinker script sends a signal to start a trade for a coin if the ask price for the coin drops below at least 3 of the the AI's predicted low prices for the coin (it predicts the currently active candle's high and low prices for each timeframe across all timeframes from 1hr to 1wk).
For determining when to DCA, it uses either the current price level from the AI that is tied to the current amount of DCA buys that have been done on the trade (for example, right after a trade starts when 3 blue lines get crossed, its first DCA wont happen until the price crosses the 4th line, so on so forth), or it uses the hardcoded drawdown % for its current level, whichever it hits first.
It only allows a max of 2 DCAs within a rolling 24hr window to keep from dumping all of your money in too quickly on coins that are having an extended downtrend. Other risk management features can easily be added, as well, with just a bit of Python code!
For determining when to sell, the bot uses a trailing profit margin to maximize the potential gains. The margin line is set at either 5% gain if no DCA has happened on the trade, or 2.5% gain if any DCA has happened. The trailing margin gap is 0.5% (this is the amount the price has to go over the profit margin to begin raising the profit margin up to TRAIL after the price and maximize how much profit is gained once the price drops below the profit margin again and the bot sells the trade.
# Setup & First-Time Use (Windows)

THESE INSTRUCTIONS WERE WRITTEN BY AI! PLEASE LET ME KNOW IF THERE ARE ANY ERRORS OR ISSUES WITH THIS SETUP PROCESS!

If you have any crypto holdings in Robinhood currently, either transfer them out of your Robinhood account or sell them to dollars BEFORE going through this setup process!
This page walks you through installing PowerTrader AI from start to finish, in the exact order a first-time user should do it.
No coding knowledge needed.
These instructions are Windows-based but PowerTrader AI *should* be able to run on any OS.
IMPORTANT: This software places real trades automatically. You are responsible for everything it does to your money and your account. Keep your API keys private. I am not giving financial advice. I am not responsible for any losses incurred or any security breaches to your computer (the code is entirely open source and can be confirmed non-malicious). You are fully responsible for doing your own due diligence to learn and understand this trading system and to use it properly.
You are fully responsible for all of your money and all of the bot's actions, and any gains or losses.
---
## Step 1 — Install Python

1. Go to **python.org** and download Python for Windows.
2. Run the installer.
3. **Check the box** that says: **“Add Python to PATH”**.
4. Click **Install Now**.

---
## Step 2 — Download PowerTrader AI

1. Do not download the zip file of the repo! There is an issue I have to fix.
2. Create a folder on your computer, like: `C:\PowerTraderAI\`
3. On the PowerTrader_AI repo page, go to the code page for pt_hub.py, click the "Download Raw File" button, save it into the folder you just created.
4. Repeat that for all files in the repo (except the readme and the license).

---
## Step 3 — Install PowerTrader AI (one command)

1. Open **Command Prompt** (Windows key → type **cmd** → Enter).
2. Go into your PowerTrader AI folder. Example:

   `cd C:\PowerTraderAI`

3. If using Python 3.12 or higher (or, later on, if you just run into the pkg_resources error) , run this command:

   `python -m pip install "setuptools==81.0.0"`

 v81 is required, pkg_resources is not included with v82. I'll change the code away from it soon.

4. Install everything PowerTrader AI needs:
   `python -m pip install -r requirements.txt`

---
## Step 4 — Start PowerTrader AI

From the same Command Prompt window (inside your PowerTrader folder), run:

`python pt_hub.py`

The app that opens is the **PowerTrader Hub**.
This is the only thing you need to run day-to-day.

---

## Step 5 — Set your folder, coins, and Robinhood keys (inside the Hub)
### Open Settings

In the Hub, open **Settings** and do this in order:

- **Main Neural Folder**: set this to the same folder that contains `pt_hub.py` (recommended easiest).
- **Choose which coins to trade**: start with **BTC**.
- **While you are still in Settings**, click **Robinhood API Setup** and do this:
1. Click **Generate Keys**.
2. Copy the **Public Key** shown in the wizard.
3. On Robinhood, add a new API key and paste that Public Key.
4. Set permissions to allow trading (the wizard tells you what to select).
5. Robinhood will show your API Key (often starts with `rh`). Copy it.
6. Paste the API Key back into the wizard and click **Save**.
7. Close the wizard and go back to the **Settings** screen.
8. **NOW** click **Save** in Settings.
After saving, you will have two files in your PowerTrader AI folder:
`r_key.txt` and `r_secret.txt`
Keep them private.

PowerTrader AI uses a simple folder style:
**BTC uses the main folder**, and other coins use their own subfolders (like `ETH\`).

---
## Step 6 — Train (inside the Hub)

Training builds the system’s coin “memory” so it can generate signals.

1. In the Hub, click **Train All**.
2. Wait until training finishes.

---

## Step 7 — Start the system (inside the Hub)

When all coins have completed training, click:

1. **Start All**

The Hub will:
**start pt_thinker.py**, wait until it is ready, then it will **start pt_trader.py**.
You don’t need to manually start separate programs. The hub handles everything!

---
## Neural Levels (the LONG/SHORT numbers)
- These are signal strength levels from low to high.
- They are the predicted high and low prices for all timeframes from 1hr to 1wk.
- They are used to show how stretched a coin's price is and for determining when to start trades and potentially when to DCA for the first few levels of DCA (Whichever price is higher, the Neural level or the hardcoded drawdown % for the current DCA level.
- Higher number = stronger signal.
- LONG = buy-direction signal. SHORT = No-start signal
A TRADE WILL START FOR A COIN IF THAT COIN REACHES A LONG LEVEL OF 3 OR HIGHER WHILE HAVING A SHORT LEVEL OF 0! This is adjustable in the settings.

---
## Long-Term Holdings (LTH)

PowerTrader AI now has a Long-Term Holdings system that allows you to automatically grow your long-term crypto holdings using a percentage of the profits made from trading!

The whole point is to allow the trading bot to continue trading normally while also building up a separate collection of crypto that it doesn't touch or sell.

### How it works

- In the Hub Settings, choose which coins you want to use for long-term holdings.
- Set the percentage of realized trading profits you want allocated to LTH. The default is 50%, but you can change it anywhere from 0% to 100%.
- When the bot sells a trade, it allocates your selected percentage of the realized profit toward LTH.
- If the allocated amount is less than $0.50, it saves it in a profit bucket until enough has accumulated to make a purchase.
- Once the allocated amount reaches $0.50 or more, the bot automatically buys the selected LTH coin.
- Trading losses can also reduce the accumulated LTH profit bucket, but never below $0.
- Setting the allocation percentage to 0% disables automatic LTH purchases.

### How it chooses which coin to buy

The Thinker calculates the daily 200 EMA (Exponential Moving Average) for each coin selected for LTH and compares its current price to that EMA.

- If multiple coins are below their daily 200 EMA, the bot picks whichever coin is the furthest below it.
- If none of the coins are below their daily 200 EMA, it picks whichever is closest to its EMA.
- It uses KuCoin's daily candle history to calculate the EMA200 and Robinhood's current ask price for the price comparison.
- These values are refreshed approximately every 30 seconds.
- If valid EMA200 data isn't available for ALL selected LTH coins, it waits instead of buying based on an incomplete comparison.

This way, the bot automatically puts your selected portion of trading profits into whichever LTH coin is currently the most discounted relative to its daily 200 EMA!

### Keeping LTH separate from trading

The bot tracks its own trade orders and positions separately from any extra coins you hold.

- LTH purchases are tagged separately and are NOT included in the bot's active trading positions.
- Extra holdings beyond the bot's tracked position are ignored by its trading logic.
- Manual and LTH purchases do not affect the bot's DCA calculations or average cost basis.
- The bot only manages the quantity it has tracked as belonging to its current trade.
- You do NOT need to enter the quantity of your long-term holdings into Settings. Just enter the coin symbols.

You can see your long-term holdings in the Hub's **Long-term Holdings** tab, and the **LTH % vs Daily EMA200** display shows the current EMA comparisons and which coin would be selected.

The Hub also shows the amount currently saved in the LTH profit bucket.

---
## Adjustable Trading Settings

The Hub now allows you to adjust most of the trading strategy directly from Settings without editing the Python scripts.

These include:

- **Trade Start Level:** Choose the minimum LONG signal level required to start a trade, from 1 to 7.
- **Start Allocation %:** Choose what percentage of your account value the bot uses when starting a new trade, with a $0.50 minimum order amount.
- **DCA Multiplier:** Choose how much larger each DCA purchase is compared to the previous purchase.
- **DCA Levels:** Set the percentage drawdown levels used to trigger DCA purchases.
- **Max DCA Buys Per 24h:** Limit how many DCA buys can happen for each coin within a rolling 24-hour period.
- **Minimum Time Before First DCA:** Choose how many actual hours must pass between the initial buy and the first DCA.
- **Trailing PM Start (No DCA):** Set the percentage gain required to begin trailing profit for trades without DCA.
- **Trailing PM Start (With DCA):** Set the percentage gain required to begin trailing profit for trades that have used DCA.
- **Trailing Gap %:** Choose how far the trailing profit margin stays behind the price.
- **LTH Profit Allocation %:** Choose what percentage of realized profits goes toward long-term holdings.

The current default settings are:

- Trade Start Level: 4
- Start Allocation: 0.5%
- DCA Multiplier: 2x
- DCA Levels: -5%, -10%, -20%, -30%, -40%, -50%, -50%
- Max DCA Buys Per 24h: 1
- Minimum Time Before First DCA: 4 hours
- Trailing PM Start (No DCA): 3%
- Trailing PM Start (With DCA): 3%
- Trailing Gap: 0.1%
- LTH Profit Allocation: 50%

These are the current defaults, and some are different from the older examples in the trading strategy explanation above.

### First DCA timing

The first DCA now has its own adjustable minimum waiting period.

For example, if the waiting period is set to 4 hours, the bot will not make its first DCA until at least 4 actual hours have passed since the initial buy.

After the first DCA happens, there is no additional time or candle restriction between later DCA purchases. The normal DCA signals and rolling 24-hour limit still apply.

Setting the minimum waiting period to 0 disables this restriction.

The bot reads updated trading settings while running, so you don't have to restart everything just to change these settings.

---
## Hub Features and Trade Tracking

The PowerTrader Hub provides a live view of what the AI and trading bot are doing.

### Current Trades

The Current Trades tab displays information about each active trade, including:

- Coin and quantity currently held by the bot.
- Current position value and average cost.
- Current ask and bid prices.
- DCA and sell profit/loss.
- Current DCA stage and number of DCA buys in the last 24 hours.
- Next DCA price and trailing profit margin line.

### Charts

The Hub includes live candlestick charts for your coins with the AI's predicted high and low prices overlaid.

The charts can also display trading information such as current prices, DCA levels, and the trailing sell line.

There is also an Account Value chart for tracking account value over time.

### Account Information

The Hub displays:

- Total account value.
- Current holdings value.
- Available buying power.
- Percentage of funds currently in trades.
- DCA affordability information.
- Total realized trading profits.
- Current LTH profit bucket.

### Trade History and Live Output

The Hub keeps a trade history showing the bot's purchases and sales, and provides live output from the Thinker, Trader, and Trainer scripts.

This makes it easier to see exactly what the bot is doing without having to open each script separately.

### Other Settings

You can also adjust the Hub's refresh speed, chart refresh speed, candle history limit, and script locations.

There is also an option to automatically start the trading scripts when the Hub opens.

---
## Adding more coins (later)

1. Open **Settings**
2. Add one new coin
3. Save
4. Click **Train All**, wait for training to complete
5. Click **Start All**

---

## Donate

PowerTrader AI is COMPLETELY free and open source! If you want to support the project, you can donate or become a member:

- Cash App: **$garagesteve**
- PayPal: **@garagesteve**
- Facebook (Subscribe to my Facebook page for only $1/month): **https://www.facebook.com/stephen.bryant.hughes**

---
## License

PowerTrader AI is released under the **Apache 2.0** license.

---
IMPORTANT: This software places real trades automatically. You are responsible for everything it does to your money and your account. Keep your API keys private. I am not giving financial advice. I am not responsible for any losses incurred or any security breaches to your computer (the code is entirely open source and can be confirmed non-malicious). You are fully responsible for doing your own due diligence to learn and understand this trading system and to use it properly.
You are fully responsible for all of your money and all of the bot's actions, and any gains or losses.

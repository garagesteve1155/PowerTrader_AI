# Explication du projet PowerTrader_AI

## Vue d'ensemble
PowerTrader_AI est un bot de trading crypto avec:
- un Runner (pt_thinker.py) qui produit les signaux et les niveaux
- un Trader (pt_trader.py) qui execute les ordres + DCA + trailing
- un Trainer (pt_trainer.py) qui genere les fichiers de memoire
- une UI (pt_hub.py) pour tout piloter

Le flux standard est:
Train -> Runner -> Trader.

## Composants
- pt_hub.py: interface principale (Start All, Train, charts, logs)
- pt_trainer.py: entraine les modeles par coin/timeframe (memories_*.txt)
- pt_thinker.py: calcule les niveaux (low/high bounds) + signaux long/short
- pt_trader.py: gere les ordres, DCA, trailing, ledger
- exchanges/binance_client.py: client Binance (live/testnet/paper)
- indicators.py: indicateurs (RSI, MACD, etc) utilises par la strategie

## Modes d'execution
- Binance live: ordres reels (BINANCE_API_KEY/SECRET)
- Binance testnet: ordres sur testnet (BINANCE_TESTNET=true)
- Binance paper: simulation locale (BINANCE_PAPER=true)

Paper mode:
- utilise les prix Binance publics
- frais simules (voir ci-dessous)
- les soldes sont locaux (BINANCE_PAPER_BALANCE)

Paper test (force des cycles buy/dca/sell):
- BINANCE_PAPER_TEST=true
- BINANCE_PAPER_TEST_COIN=BNB
- BINANCE_PAPER_TEST_ALLOC_USD=50
- BINANCE_PAPER_TEST_DCA_SECONDS=60
- BINANCE_PAPER_TEST_HOLD_SECONDS=120

## Training
Le Trainer cree les fichiers:
- memories_*.txt
- memory_weights_*.txt
- memory_weights_high_*.txt
- memory_weights_low_*.txt
- neural_perfect_threshold_*.txt

Si un timeframe affiche INACTIVE (training data issue), il faut re-trainer le coin.

## Runner (pt_thinker.py)
Le Runner lit les fichiers de training et genere:
- low_bound_prices.html (niveaux long)
- high_bound_prices.html (niveaux short)
- long_dca_signal.txt / short_dca_signal.txt (compteurs 0..7)

Le runner ecrit aussi runner_ready.json pour que Start All sache quand demarrer le Trader.

## Trader (pt_trader.py)
Le Trader:
- lit les signaux long/short
- decide l'entree (neural ou strategie)
- gere DCA
- gere trailing profit margin pour la sortie

### Entree (buy)
Par defaut:
- entree seulement si long >= 3 et short == 0

Si Strategy est activee:
- selector: tous les indicateurs coches doivent etre OK
- super: score moyen >= 0.6
- replace_neural=true: ignore le signal neural et ne garde que les indicateurs

Important: la strategie n'affecte PAS le DCA ni le trailing. Elle ne change que l'entree.

### DCA
Niveaux de pertes (en % vs cost basis):
- -2.5, -5, -10, -20, -30, -40, -50

Regles:
- DCA limite a 2 achats par 24h et par coin
- DCA peut etre declenche par le niveau neural (N4..N7)
- apres un DCA, la ligne de trailing est recalcul?e

### Sortie (sell)
Il n'y a pas de stop loss dur.
La sortie se fait via le trailing profit margin:
- demarre a +5% sans DCA
- demarre a +2.5% avec DCA
- trailing_gap_pct = 0.5%

Quand le prix repasse sous la ligne, le sell est execute.

## Strategie (indicateurs)
Indicateurs supportes:
- RSI, MACD, Stochastique, Momentum, OBV
- Bollinger Bands, EMA, ATR, Volume Profile
- ADX, Pivots, Ichimoku Cloud

Rules par defaut (resume):
- RSI: buy si RSI < 30
- MACD: cross haussier
- Stochastique: %K < 20 + cross
- Momentum: > 0
- OBV: pente positive
- Bollinger: prix <= lower band
- EMA: prix > EMA21 ou cross EMA8 > EMA21
- ATR: filtre non bloquant (score 0.5)
- Volume Profile: volume local > moyenne
- ADX: > 20
- Pivots: rebond proche S1
- Ichimoku: prix au-dessus du cloud + tenkan > kijun

## PnL et account value
- Les valeurs sont en USD/USDT.
- Les colonnes DCA PnL % et Sell PnL % sont des pourcentages.
- Total Account Value = cash + valeur des positions (mark-to-market).
- Les gains reels apparaissent dans Total realized apres un SELL.

Si le trade est petit (ex: 74 USD), une variation de 0.05% donne quelques centimes.
Ce n'est pas un probleme de fees, c'est juste la taille de la position.

## Fees (paper mode)
Le paper mode applique des frais sur chaque trade:
- BUY: fee prise sur l'asset recu (base)
- SELL: fee prise sur la devise de cotation (quote)

Par defaut:
- taker_fee = 0.001 (0.1%)
- maker_fee = 0.001 (0.1%)

Tu peux forcer un taux moyen dans .env:
- BINANCE_PAPER_FEE_RATE=0.001
- BINANCE_TAKER_FEE_RATE=0.001
- BINANCE_MAKER_FEE_RATE=0.001

Note: c'est une approximation realiste, mais pas identique au matching engine live
(slippage/partial fills optionnels).

Slippage / partial fills (paper mode):
- BINANCE_PAPER_SLIPPAGE_PCT=0.001 -> +/-0.1% de slippage max
- BINANCE_PAPER_PARTIAL_FILL=true -> remplissages partiels
- BINANCE_PAPER_PARTIAL_FILL_MIN=0.6
- BINANCE_PAPER_PARTIAL_FILL_MAX=1.0

## Testnet vs Paper
- Testnet: simule l'exchange Binance (matching engine + ordres), mais avec des fonds factices.
- Paper: simulation locale, plus rapide, mais moins realiste (pas d'order book).

Si tu veux la simulation la plus proche du reel: utilise le testnet.

## Pine signals (TradingView)
Le bot ne peut pas executer un script Pine directement. Il peut lire des signaux
exportes dans un fichier JSONL (une ligne JSON par signal).

Exemple de ligne JSONL:
{"symbol":"BNB","action":"buy","ts":1700000000,"strength":"strong"}

Variables .env:
- PINE_SIGNAL_ENABLED=true
- PINE_SIGNAL_MODE=filter (filter|replace|off)
- PINE_SIGNAL_USE_EXIT=false
- PINE_SIGNAL_FILE=hub_data/pine_signals.jsonl
- PINE_SIGNAL_MAX_AGE_SECONDS=300

Mode:
- filter: un signal Pine non-buy bloque l'entree; buy autorise avec neural/strategie.
- replace: l'entree depend uniquement de Pine (buy requis).
- exit: si PINE_SIGNAL_USE_EXIT=true, un signal sell/stop force la sortie.

## Pourquoi pas de difference avec/sans strategie?
- La strategie ne change que l'entree.
- Si tu es deja en position, le DCA/trailing continue pareil.
- Avec tous les indicateurs, le score peut rester sous le seuil -> moins d'entrees.
- Pas besoin de retrain pour activer la strategie: les indicateurs utilisent les candles en live.

## Conseils PnL
- Commence par 1 coin + 1 timeframe pour valider la logique.
- Ajuste la taille d'allocation si tu veux des variations plus visibles.
- Observe la difference entre selector et super, et teste des sous-ensembles d'indicateurs.

## Commandes utiles
- UI: python pt_hub.py
- Tests: python -m pytest -q


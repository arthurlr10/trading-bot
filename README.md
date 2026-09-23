# Binance Futures Demo Trading Bot

Algorithmic trading bot for **Binance USD-M Futures Demo Trading** only (paper money).  
Strategy: EMA 9/21 cross + RSI(14) filter, with hard-coded risk management.

> **Note:** Binance deprecated the classic Futures testnet (`testnet.binancefuture.com`)
> for private API calls. This bot uses [Binance Demo Trading](https://demo.binance.com)
> via `demo-fapi.binance.com` (ccxt `enable_demo_trading`).

## Requirements

- Python 3.11+
- Binance **Demo Trading** API keys: https://demo.binance.com/en/my/settings/api-management
- (Optional) Telegram bot token + chat id for alerts / kill switch

## Setup

```bash
cd trading
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your DEMO keys (and Telegram if desired)
```

## Configuration

All tunable parameters live in [`config/settings.yaml`](config/settings.yaml):

- Pairs, timeframe
- EMA / RSI settings
- Risk: 1% per trade, R:R 1.5, daily 3% / weekly 6% loss limits

`exchange.testnet` **must** stay `true`. The bot exits if it is set to `false`, and also refuses to start unless the exchange URL is `demo-fapi.binance.com`.

## Run

```bash
source .venv/bin/activate
python -m src.main
```

Logs: `data/bot.log`  
Database: `data/trades.db`

Telegram: `/kill`, `/resume`, `/solde` (balance).

## Déploiement serveur (Proxmox / VPS)

Le bot doit tourner en process permanent (ce n’est pas un site Next.js / Vercel).

1. Cloner le projet (ex. `/var/www/trading-bot`)
2. Paquets système si besoin (compte sudo) :
   ```bash
   sudo apt update
   sudo apt install python3-venv python3-pip tmux
   # adapter python3.X-venv selon: python3 --version
   ```
3. Setup :
   ```bash
   cd /var/www/trading-bot   # ou ~/trading-bot
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   # Éditer .env (clés Demo Binance + Telegram)
   ```
4. Lancer dans tmux :
   ```bash
   tmux new -s bot
   source .venv/bin/activate
   python -m src.main
   ```
5. Détacher (laisser le bot tourner) :
   - Idéal : `Ctrl+B` puis `D` (relâcher Ctrl avant `D`)
   - Sur **console Proxmox web**, `Ctrl+B` marche souvent mal (`^B` apparaît dans les logs) → **fermer la console suffit**, le bot continue dans tmux
   - Depuis un autre shell : `tmux detach-client -s bot`
6. Revenir aux logs :
   ```bash
   tmux attach -t bot
   ```
   Si `duplicate session: bot` → la session existe déjà, utilise `attach` (pas `new`).

Sans tmux, alternative :
```bash
mkdir -p data
nohup python -m src.main >> data/bot.log 2>&1 &
```

**Important :** ne pas laisser le bot tourner en même temps sur le Mac et le serveur (doublons de trades). Ne pas exposer `.env` en HTTP.

## Kill switch

- **File**: create `data/KILL` (any content) → no new entries  
- **Telegram**: `/kill` → same; `/resume` → clear and continue  
- Existing open positions are **not** force-closed by default (`close_on_kill: false`)

## Weekly analysis

```bash
python scripts/weekly_report.py
python scripts/weekly_report.py --days 14
```

Reports win rate, profit factor, max drawdown, trade count, cumulative PnL.

## Risk rules (non-negotiable)

| Rule | Value |
|------|-------|
| Risk per trade | ≤ 1% of equity |
| Stop loss | Set at open, never modified |
| Reward / risk | ≥ 1 : 1.5 |
| No martingale | Size from risk % only |
| Daily loss | 3% → pause until next UTC day |
| Weekly loss | 6% → pause until next Monday UTC |
| Positions | Max 1 open per pair |

## Safety

- No withdraw / transfer code paths
- Demo-trading-only guard at startup (refuses live `fapi.binance.com`)
- API retries on rate limits and network errors
- Telegram alerts on open / close / limits / errors

## Project layout

See `src/` for exchange, strategy, risk, execution, storage, notify, and kill switch modules.

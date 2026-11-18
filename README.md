# Otryadi Telegram Bot — Cloudflare Worker

This repository now deploys the Telegram bot as a Cloudflare Worker.

## Deployment

- Root directory: `/`
- Build command: leave empty
- Deploy command: `npx wrangler deploy`
- Config: `wrangler.toml`

The original Python bot is archived in `legacy/bot.py`. It is not used by the Worker deployment.

## Required secrets

- `BOT_TOKEN`
- `WEBHOOK_SECRET`
- `SETUP_PASSWORD`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

## Variables

- `ADMIN_TELEGRAM_ID`
- `GOOGLE_SHEET_ID` (optional; defaults to the original spreadsheet)
- `SQUAD_1_NAME` ... `SQUAD_10_NAME`
- `SQUAD_1_LINK` ... `SQUAD_10_LINK`

The Google service account must have Editor access to the spreadsheet.

After deployment, visit `/setup` and enter `SETUP_PASSWORD` to set the Telegram webhook.

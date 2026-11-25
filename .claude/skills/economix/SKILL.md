---
name: economix
description: Operate, audit and expand Economix Radar (supermarket competitor price monitoring via Android apps). Use for running collections, checking the environment, exploring a new market app to write its deterministic flow, auditing collected offers against evidence screenshots, or adding a new supermarket.
---

# Economix Radar

Python package `economix/` (run with `.venv\Scripts\python.exe -m economix <cmd>`). Android SDK/AVD in `C:\Android` (emulator `economix_api34`). Data in `data/` (SQLite `economix.db`, `evidence/`, `exploration/`).

## Hard rules
- Never invent data. If a field is unreadable or absent, leave it null. An offer without a legible product name is not saved.
- Every offer must point to the screenshot it came from (`evidence_path`).
- Never type passwords, solve captchas, bypass root/emulator detection, or activate/redeem offers/coupons. Read only. If the app shows login, update-required, captcha or blocking → stop and report `needs_human`.
- `android_package` in `config/markets.yaml` only from a confirmed source (`economix discover` on the device or official store page).

## Commands
- `doctor [--appium]`: environment check (all lines must be OK).
- `markets`: status per market (package, installed, flow).
- `discover`: third-party packages installed on the device.
- `explore <market> [--label x]`: screenshot + compact UI tree of the current screen.
- `collect [ids...] [--notify]`: run enabled markets (or the given ones); `--notify` sends Telegram.
- `report [--send]`: changes + competitor comparison.
- `buscar "<termo>"`: price lookup by name/brand/EAN across all markets, cheapest first.
- `oportunidades [--enviar]`: products whose price dropped since the previous run (saved to data/reports/).

## Routine
Windows Task Scheduler task "Economix Radar - coleta diaria" runs `scripts/daily.ps1` EVERY HOUR from 08:00
(IgnoreNew if a run is still going): collect → `scripts/export_dashboard.py` (panel "quedas" = last 24h) →
`oportunidades --enviar` (drops since the previous run; Telegram only when there is at least one drop). Log: `data/logs/daily_<date>.log`.
Americanas uses CEP 15015-000 (Rio Preto) via `cep:` in markets.yaml (VTEX region cookie; changes availability, not price).
Dashboard artifact: https://claude.ai/artifact/LYJC4oATQSYptRUsJ6abYx (type Data). It does NOT refresh itself:
upload `data/dashboard/*.json` as assets and point `datasets/<id>.source.url` at them (then delete the old assets).

## Cross-market matching (economix/matching.py)
EAN first; else by name only when brand, size, pack qty and packaging agree and token Jaccard >= 0.8
("sem sal"/"com sal" are single tokens). Never loosen without auditing a sample of matches.

Boot the emulator if `adb devices` is empty:
`Start-Process C:\Android\Sdk\emulator\emulator.exe -ArgumentList "-avd economix_api34 -no-snapshot-save"`

## Exploring a new market (AI phase, token-efficient)
1. Confirm the app is installed and the user is already logged in (the user logs in, never you).
2. `adb shell monkey -p <pkg> -c android.intent.category.LAUNCHER 1`, then `explore <market> --label home`.
3. Read the `.txt` compact tree first (cheap). Open the `.png` only when the tree is ambiguous (images/prices rendered as images).
4. Navigate with `adb shell input tap x y` using the centers from the tree; `explore` after each step. Find the offers list.
5. On the offers screen identify: card container `resource-id`, and the field `resource-id`s or regexes (product, prices, size, validity, conditions).
6. Write `config/flows/<market>.yaml` (format documented in `economix/adapters/flow.py`): steps, session.require_any/block_if_any, collect.card.
7. Run `collect <market>`; audit at least 5 offers by opening their evidence PNG and comparing fields. Fix the flow until 100% of the audited fields match or are null.
8. Only then set `enabled: true` in `markets.yaml`.
If the app has no resource-ids (Flutter/React Native/webview), write a custom adapter `economix/adapters/<market>.py` exposing `class Adapter(BaseAdapter)` and set `adapter: <market>`.

## Auditing
Query `data/economix.db` table `observations` (latest run per market in `runs`). For a sample of rows, open `evidence_path` and confirm product/price/validity are literally on screen. Report mismatches; never "correct" data from memory.

## Adding a market
Add an entry to `config/markets.yaml` (`enabled: false`, `android_package: null` until confirmed), then follow the exploration flow.

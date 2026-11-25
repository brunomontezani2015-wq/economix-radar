# Economix Radar

Radar de preços de supermercados concorrentes: coleta ofertas públicas dos sites das redes,
guarda histórico, cruza o mesmo produto entre redes e avisa quedas de preço pelo Telegram.

- Coleta na nuvem: `.github/workflows/coleta.yml` (de hora em hora, 07:00–21:00, horário de Brasília).
- Segredos do repositório: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- Uso local: `python -m economix --help` (coletar, buscar, oportunidades, report).
- Regra central: nada é inventado. Campo ilegível ou ausente fica vazio, e cada preço aponta para a evidência de onde veio.

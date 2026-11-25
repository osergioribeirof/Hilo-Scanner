# HiLo Scanner · Ibovespa (diário)

Rastreia a tendência de cada ação do Ibovespa pelo **HiLo Activator** e manda **e-mail** quando uma ação vira.
100% gratuito: roda no GitHub Actions, os dados vêm do Yahoo Finance e o dashboard fica no GitHub Pages.

```
hilo_scanner.py          script (baixa candles, calcula HiLo, detecta viradas, manda e-mail)
tickers.json             lista de ações (Ibovespa)
docs/config.json         período do HiLo de cada ação (o dashboard grava aqui)
docs/index.html          dashboard
docs/data.json           candles (gerado pelo script)
state.json               última tendência avisada (gerado pelo script)
.github/workflows/hilo.yml   agenda: segunda a sexta, 18:40 (Brasília)
```

## Instalação (uma vez, ~10 min)

1. **Crie um repositório público** no GitHub (ex.: `hilo-scanner`) e envie todos estes arquivos.
   Público porque o GitHub Pages grátis exige. Seu e-mail e senha ficam em *Secrets*, nunca no código.
2. **Settings → Pages**: *Deploy from a branch* → `main` → pasta `/docs`. O dashboard fica em
   `https://SEU-USUARIO.github.io/hilo-scanner/`.
3. **Senha de app do Gmail** (a senha normal não funciona): ative a verificação em duas etapas e crie uma em
   <https://myaccount.google.com/apppasswords>.
4. **Settings → Secrets and variables → Actions**:
   - *Secrets*: `SMTP_USER` (seu Gmail), `SMTP_PASS` (a senha de app), `EMAIL_TO` (opcional; padrão = `SMTP_USER`).
   - *Variables*: `DASHBOARD_URL` (link do dashboard; vai no e-mail).
5. **Actions → HiLo Scanner → Run workflow**, marcando *test_email*: confirme que o e-mail de teste chegou.
6. **Run workflow** de novo, sem marcar nada: é a 1ª rodada. Ela gera `data.json` e **não manda e-mail**
   (só registra a tendência atual de cada ação). A partir daí, cada virada gera aviso.

## Período por ação (e como fica salvo)

No dashboard, cada linha tem um campo **Período** (e há um **Período padrão** no topo). Ao alterar:
- recalcula na hora e **salva no navegador**;
- para o **e-mail** usar os mesmos períodos, o `docs/config.json` do repositório precisa mudar. Clique em
  **⚙ Salvamento**, informe `usuario/repositorio` e um token fine-grained
  (GitHub → Settings → Developer settings → Fine-grained tokens, só para este repositório, *Contents: Read and write*).
  Depois disso cada ajuste é gravado no GitHub automaticamente (o chip no topo mostra o estado).
- sem token, use **Baixar config.json** e substitua o arquivo `docs/config.json` do repositório.

Mudar o período de uma ação **não dispara aviso falso**: o script só atualiza o estado daquela ação.

## Como o sinal é calculado

- `hi` = média simples das últimas N máximas; `lo` = média simples das últimas N mínimas.
- **Alta**: fechamento > `hi` do candle anterior. **Baixa**: fechamento < `lo` do candle anterior.
  Entre os dois, mantém a tendência anterior. Fechamento exatamente igual à média não é virada.
- Linha HiLo = `lo` anterior (em alta) ou `hi` anterior (em baixa): serve de stop móvel.
- Só candles **fechados**: a rodada é às 18:40 e candle do dia ainda aberto é descartado.
- O dashboard e o script usam a mesma conta (conferida candle a candle).

## Manutenção

- O Ibovespa muda em **janeiro, maio e setembro**. Edite `tickers.json` (campos `t` e `n`; opcional `"yahoo"`
  se o código no Yahoo for diferente de `TICKER.SA`).
- Ações sem dados no Yahoo aparecem em **"Sem dados no Yahoo"** no rodapé do dashboard e no log da execução.
- O GitHub pode pausar agendamentos de repositório sem atividade por 60 dias; se acontecer, reative em *Actions*.
- Se a execução falhar no envio do e-mail, a virada **não** é marcada como avisada e entra de novo na próxima rodada.

> Sinais automáticos a partir do fechamento. Não é recomendação de investimento.

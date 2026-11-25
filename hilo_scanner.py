#!/usr/bin/env python3
"""HiLo Activator Scanner - Ibovespa (timeframe diario).

O que faz, a cada execucao:
  1. baixa candles diarios (Yahoo Finance via yfinance, gratuito) dos tickers de tickers.json
  2. calcula o HiLo Activator de cada ticker com o periodo definido em docs/config.json
  3. compara com o estado salvo (state.json) e manda e-mail das ACOES QUE VIRARAM
  4. grava docs/data.json (candles, usados pelo dashboard) e docs/signals.json (resumo)

Uso:
  python hilo_scanner.py               # rodada normal
  python hilo_scanner.py --test-email  # so envia um e-mail de teste
  python hilo_scanner.py --no-email    # roda sem enviar e-mail (nao atualiza o estado)

Variaveis de ambiente do e-mail (no GitHub: Settings > Secrets):
  SMTP_USER, SMTP_PASS   (obrigatorias; no Gmail, use "senha de app")
  EMAIL_TO               (opcional; padrao = SMTP_USER; aceita varios separados por virgula)
  SMTP_HOST, SMTP_PORT   (opcionais; padrao smtp.gmail.com / 465)
  DASHBOARD_URL          (opcional; link do dashboard colocado no e-mail)
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, time
from email.message import EmailMessage
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TICKERS_FILE = ROOT / "tickers.json"
CONFIG_FILE = ROOT / "docs" / "config.json"
DATA_FILE = ROOT / "docs" / "data.json"
SIGNALS_FILE = ROOT / "docs" / "signals.json"
STATE_FILE = ROOT / "state.json"

TZ = ZoneInfo("America/Sao_Paulo")
KEEP_BARS = 260          # candles guardados no data.json (dashboard calcula em cima disso)
MARKET_CLOSE_OK = time(18, 30)   # antes disso, o candle de "hoje" ainda pode estar incompleto
MAX_PERIOD = 200
EPS = 1e-9


# --------------------------------------------------------------------------- HiLo
def hilo(high, low, close, n):
    """Gann HiLo Activator (Krausz).

    hi[i] = media simples das ultimas n maximas; lo[i] = idem das minimas.
    tendencia[i] = +1 se close[i] > hi[i-1]; -1 se close[i] < lo[i-1]; senao mantem a anterior.
    linha[i]     = lo[i-1] em alta (stop de compra) ; hi[i-1] em baixa.
    Soma sequencial (laco simples) para dar exatamente o mesmo numero que o JavaScript do dashboard.
    Retorna (trend, line): listas do tamanho da serie; trend 0 / line None ate haver dados.
    """
    size = len(close)
    hi = [None] * size
    lo = [None] * size
    for i in range(n - 1, size):
        sh = 0.0
        sl = 0.0
        for k in range(i - n + 1, i + 1):
            sh += high[k]
            sl += low[k]
        hi[i] = sh / n
        lo[i] = sl / n
    trend = [0] * size
    line = [None] * size
    cur = 0
    for i in range(n, size):
        # EPS: fechamento exatamente igual a media (empate) nao e virada; evita que erro de
        # ponto flutuante (1e-15) decida o sinal. Precos tem 2 casas, entao 1e-9 nunca corta sinal real.
        if close[i] - hi[i - 1] > EPS:
            cur = 1
        elif lo[i - 1] - close[i] > EPS:
            cur = -1
        trend[i] = cur
        if cur == 1:
            line[i] = lo[i - 1]
        elif cur == -1:
            line[i] = hi[i - 1]
    return trend, line


def analyze(series, n):
    """Resumo do ultimo candle: tendencia, linha, idade da virada, etc."""
    h, l, c, d = series["h"], series["l"], series["c"], series["d"]
    if len(c) < n + 3:
        return None
    trend, line = hilo(h, l, c, n)
    cur = trend[-1]
    if cur == 0:
        return None
    age = 0                       # candles desde a virada (0 = virou no ultimo candle)
    i = len(c) - 1
    while i > 0 and trend[i - 1] == cur:
        age += 1
        i -= 1
    flipped_at = d[i]
    prev_close = c[-2]
    return {
        "trend": cur,
        "line": round(line[-1], 4),
        "close": c[-1],
        "chg": round((c[-1] / prev_close - 1) * 100, 2) if prev_close else None,
        "dist": round((c[-1] / line[-1] - 1) * 100, 2),
        "age": age,
        "flip_date": flipped_at,
        "date": d[-1],
        "period": n,
    }


# --------------------------------------------------------------------------- dados
def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def fetch_all(tickers):
    """Baixa candles diarios. Retorna {ticker: {"d","h","l","c"}} e a lista dos que falharam."""
    import yfinance as yf  # import aqui para o resto do arquivo poder ser testado sem a lib

    out, missing = {}, []
    now = datetime.now(TZ)
    chunk = 30
    for start in range(0, len(tickers), chunk):
        part = tickers[start:start + chunk]
        syms = [t.get("yahoo") or f"{t['t']}.SA" for t in part]
        try:
            df = yf.download(
                syms, period="2y", interval="1d", auto_adjust=False,
                group_by="ticker", threads=True, progress=False,
            )
        except Exception as exc:  # rede/Yahoo fora do ar
            print(f"[aviso] falha no lote {part[0]['t']}..{part[-1]['t']}: {exc}")
            df = None
        for t, sym in zip(part, syms):
            try:
                sub = df[sym] if len(syms) > 1 else df
                sub = sub.dropna(subset=["High", "Low", "Close"])
            except Exception:
                missing.append(t["t"])
                continue
            if sub.empty:
                missing.append(t["t"])
                continue
            dates = [ix.strftime("%Y-%m-%d") for ix in sub.index]
            h = [round(float(v), 2) for v in sub["High"]]
            l = [round(float(v), 2) for v in sub["Low"]]
            c = [round(float(v), 2) for v in sub["Close"]]
            # candle de hoje ainda aberto? descarta
            if dates[-1] == now.strftime("%Y-%m-%d") and now.time() < MARKET_CLOSE_OK:
                dates, h, l, c = dates[:-1], h[:-1], l[:-1], c[:-1]
            if len(c) < 30:
                missing.append(t["t"])
                continue
            out[t["t"]] = {"n": t["n"], "d": dates[-KEEP_BARS:], "h": h[-KEEP_BARS:],
                           "l": l[-KEEP_BARS:], "c": c[-KEEP_BARS:]}
    return out, missing


# --------------------------------------------------------------------------- e-mail
def _smtp_send(msg):
    user = os.environ.get("SMTP_USER", "").strip()
    pwd = os.environ.get("SMTP_PASS", "").strip()
    if not user or not pwd:
        raise RuntimeError("SMTP_USER / SMTP_PASS nao configurados")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as s:
            s.login(user, pwd)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(user, pwd)
            s.send_message(msg)


def _new_msg(subject):
    user = os.environ.get("SMTP_USER", "").strip()
    to = os.environ.get("EMAIL_TO", "").strip() or user
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("EMAIL_FROM", "").strip() or user
    msg["To"] = to
    return msg


def fmt_date(iso):
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def fmt_num(v, nd=2):
    return f"{v:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def build_email(flips, date_iso, url):
    ups = [f for f in flips if f["trend"] == 1]
    downs = [f for f in flips if f["trend"] == -1]
    parts = []
    if ups:
        parts.append(f"{len(ups)} p/ alta")
    if downs:
        parts.append(f"{len(downs)} p/ baixa")
    subject = f"HiLo B3 · {' · '.join(parts)} · {fmt_date(date_iso)}"

    def block_txt(title, rows):
        if not rows:
            return ""
        lines = [title]
        for r in rows:
            lines.append(
                f"  {r['ticker']:<7} fech. R$ {fmt_num(r['close'])}  linha HiLo R$ {fmt_num(r['line'])}"
                f"  ({fmt_num(r['dist'])}%)  periodo {r['period']}"
            )
        return "\n".join(lines) + "\n"

    text = (
        f"HiLo Activator · Ibovespa · candle diario de {fmt_date(date_iso)}\n\n"
        + block_txt("VIRARAM PARA ALTA (fechamento acima da media das maximas)", ups) + "\n"
        + block_txt("VIRARAM PARA BAIXA (fechamento abaixo da media das minimas)", downs)
        + (f"\nDashboard: {url}\n" if url else "")
        + "\nSinal gerado automaticamente a partir do fechamento. Nao e recomendacao de investimento.\n"
    )

    def block_html(title, color, icon, rows):
        if not rows:
            return ""
        trs = "".join(
            f"<tr><td style='padding:6px 10px;font-weight:600'>{escape(r['ticker'])}</td>"
            f"<td style='padding:6px 10px;color:#555'>{escape(r['name'])}</td>"
            f"<td style='padding:6px 10px;text-align:right'>R$ {fmt_num(r['close'])}</td>"
            f"<td style='padding:6px 10px;text-align:right'>R$ {fmt_num(r['line'])}</td>"
            f"<td style='padding:6px 10px;text-align:right'>{fmt_num(r['dist'])}%</td>"
            f"<td style='padding:6px 10px;text-align:right'>{r['period']}</td></tr>"
            for r in rows
        )
        return (
            f"<h3 style='margin:18px 0 6px;color:{color}'>{icon} {title}</h3>"
            "<table style='border-collapse:collapse;font-size:14px;width:100%'>"
            "<tr style='color:#777;font-size:12px;text-align:right'>"
            "<th style='text-align:left;padding:4px 10px'>Ticker</th>"
            "<th style='text-align:left;padding:4px 10px'>Empresa</th>"
            "<th style='padding:4px 10px'>Fechamento</th><th style='padding:4px 10px'>Linha HiLo</th>"
            "<th style='padding:4px 10px'>Dist.</th><th style='padding:4px 10px'>Per.</th></tr>"
            f"{trs}</table>"
        )

    html = (
        "<div style='font-family:Arial,Helvetica,sans-serif;max-width:640px;color:#222'>"
        f"<h2 style='margin:0 0 4px'>HiLo Activator · Ibovespa</h2>"
        f"<div style='color:#777;font-size:13px'>Candle diário de {fmt_date(date_iso)}</div>"
        + block_html("Viraram para ALTA", "#0b7a0b", "▲", ups)
        + block_html("Viraram para BAIXA", "#b32d2d", "▼", downs)
        + (f"<p style='margin-top:20px'><a href='{escape(url)}'>Abrir dashboard</a></p>" if url else "")
        + "<p style='color:#999;font-size:12px;margin-top:22px'>Sinal automático a partir do fechamento. "
        "Não é recomendação de investimento.</p></div>"
    )
    msg = _new_msg(subject)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def send_test():
    msg = _new_msg("HiLo B3 · e-mail de teste")
    msg.set_content("Se você recebeu isto, o envio de avisos do HiLo Scanner está funcionando.")
    _smtp_send(msg)
    print("E-mail de teste enviado.")


# --------------------------------------------------------------------------- main
def main(argv):
    if "--test-email" in argv:
        send_test()
        return 0
    no_email = "--no-email" in argv

    tickers = load_json(TICKERS_FILE, [])
    cfg = load_json(CONFIG_FILE, {"default_period": 20, "periods": {}})
    default_n = int(cfg.get("default_period", 20))
    periods = cfg.get("periods", {}) or {}

    def period_of(tk):
        try:
            n = int(periods.get(tk, default_n))
        except (TypeError, ValueError):
            n = default_n
        return max(1, min(MAX_PERIOD, n))

    data, missing = fetch_all(tickers)
    if not data:
        print("[erro] nenhum ticker retornou dados (Yahoo fora do ar?). Nada foi alterado.")
        return 1

    names = {t["t"]: t["n"] for t in tickers}
    last_bar = max(s["d"][-1] for s in data.values())
    now_iso = datetime.now(TZ).isoformat(timespec="seconds")

    results = {}
    for tk, s in data.items():
        r = analyze(s, period_of(tk))
        if r:
            results[tk] = r

    # ---- estado e deteccao de viradas
    state = load_json(STATE_FILE, None)
    flips = []
    first_run = state is None
    new_bar = first_run or state.get("last_bar") != last_bar
    new_state = {"last_bar": last_bar, "trends": {}}
    for tk, r in results.items():
        new_state["trends"][tk] = {"trend": r["trend"], "period": r["period"]}
        if first_run or not new_bar:
            continue
        old = state["trends"].get(tk)
        if not old or old["period"] != r["period"]:
            continue   # periodo mudou (ou ticker novo): so atualiza o estado, sem avisar
        if old["trend"] != r["trend"]:
            flips.append({"ticker": tk, "name": names.get(tk, tk), **r})
    # se o candle nao avancou (feriado / rodada repetida), preserva o estado antigo
    if not new_bar and state:
        new_state = state

    # ---- saidas
    DATA_FILE.write_text(json.dumps({
        "generated_at": now_iso, "last_bar": last_bar,
        "tickers": data, "missing": sorted(missing),
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    SIGNALS_FILE.write_text(json.dumps({
        "generated_at": now_iso, "last_bar": last_bar, "default_period": default_n,
        "results": results, "flips": [f["ticker"] for f in flips],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"candle: {last_bar} | analisados: {len(results)} | sem dados: {len(missing)} {sorted(missing)}")
    if first_run:
        print("primeira rodada: estado inicializado, nenhum aviso enviado.")
    print(f"viradas: {[(f['ticker'], f['trend']) for f in flips]}")

    rc = 0
    if flips and not no_email:
        try:
            _smtp_send(build_email(flips, last_bar, os.environ.get("DASHBOARD_URL", "").strip()))
            print("e-mail enviado.")
        except Exception as exc:
            print(f"[erro] e-mail nao enviado: {exc}")
            rc = 2        # NAO grava o estado: a virada sera avisada na proxima rodada
    if not no_email and rc == 0:
        STATE_FILE.write_text(json.dumps(new_state, indent=1), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

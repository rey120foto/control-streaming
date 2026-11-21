import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from supabase import create_client

TZ = ZoneInfo("America/Caracas")
HOY = datetime.now(TZ).date()
DIAS_AVISO = 3

def main():
    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    app_url = os.environ.get("APP_URL", "").strip()

    cuentas = supabase.table("cuentas_streaming").select("*").eq("activa", True).execute().data
    movs = supabase.table("cartera_movimientos").select("monto").execute().data
    saldo = sum(float(m["monto"]) for m in movs)

    pendientes = []
    total = 0.0
    for c in cuentas:
        ultimo = datetime.strptime(c["ultimo_pago"], "%Y-%m-%d").date()
        proximo = ultimo + timedelta(days=int(c["frecuencia_dias"]))
        dias = (proximo - HOY).days
        if dias <= DIAS_AVISO:
            monto = float(c["monto"])
            total += monto
            etiqueta = (
                f"🔴 Vencido hace {abs(dias)}d" if dias < 0
                else ("🟠 Vence HOY" if dias == 0 else f"🟡 Vence en {dias}d")
            )
            pendientes.append(f"• *{c['servicio']}* (`{c['usuario']}`)\n  💵 ${monto:.2f} — {etiqueta}")

    if not pendientes:
        print("Sin pagos pendientes hoy.")
        return

    restante = saldo - total
    resumen_saldo = (
        f"✅ *Te quedarían:* ${restante:.2f}"
        if restante >= 0
        else f"⚠️ *Te faltan:* ${abs(restante):.2f} para cubrir todo"
    )

    texto = (
        "🔔 *Recordatorio de Pagos Streaming*\n\n"
        + "\n\n".join(pendientes)
        + f"\n\n💰 *Saldo en cartera:* ${saldo:.2f}"
        + f"\n📉 *Total pendiente:* ${total:.2f}\n"
        + resumen_saldo
    )

    payload = {"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"}
    if app_url:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": "📲 Abrir Mini App y Confirmar Pago", "url": app_url}]]
        }

    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=15)
    r.raise_for_status()
    print("Alerta enviada con éxito.")

if __name__ == "__main__":
    main()

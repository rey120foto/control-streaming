import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from supabase import create_client

TZ = ZoneInfo("America/Caracas")
HOY = datetime.now(TZ).date()

st.set_page_config(page_title="Control Streaming", page_icon="📺", layout="wide")

# Conexión a Supabase
@st.cache_resource
def get_supabase():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = get_supabase()

def cargar_datos():
    cuentas = supabase.table("cuentas_streaming").select("*").order("id").execute().data
    movs = supabase.table("cartera_movimientos").select("*").order("fecha", desc=True).order("id", desc=True).execute().data
    df_c = pd.DataFrame(cuentas)
    df_m = pd.DataFrame(movs)

    if not df_c.empty:
        df_c["ultimo_pago"] = pd.to_datetime(df_c["ultimo_pago"]).dt.date
        df_c["proximo_pago"] = df_c.apply(
            lambda r: r["ultimo_pago"] + timedelta(days=int(r["frecuencia_dias"])), axis=1
        )
        df_c["dias_restantes"] = df_c["proximo_pago"].apply(lambda d: (d - HOY).days)
        df_c["estado"] = df_c.apply(clasificar_estado, axis=1)
        df_c = df_c.sort_values(by=["activa", "dias_restantes"], ascending=[False, True])

    saldo = float(df_m["monto"].sum()) if not df_m.empty else 0.0
    return df_c, df_m, saldo

def clasificar_estado(row):
    if not row["activa"]:
        return "⏸️ Pausada"
    d = row["dias_restantes"]
    if d < 0:
        return f"🔴 Vencido ({abs(d)}d)"
    if d == 0:
        return "🟠 Vence Hoy"
    if d <= 3:
        return f"🟡 En {d}d"
    return f"🟢 Al día ({d}d)"

def registrar_pago(cuenta):
    monto = float(cuenta["monto"])
    # 1. Actualizar fecha de último pago
    supabase.table("cuentas_streaming").update(
        {"ultimo_pago": HOY.isoformat()}
    ).eq("id", int(cuenta["id"])).execute()
    # 2. Descontar de cartera
    supabase.table("cartera_movimientos").insert({
        "fecha": HOY.isoformat(),
        "tipo": "Pago",
        "concepto": f"{cuenta['servicio']} - {cuenta['usuario']}",
        "monto": -abs(monto),
        "cuenta_id": int(cuenta["id"]),
    }).execute()

def enviar_telegram_prueba(df_c, saldo):
    token = st.secrets.get("TELEGRAM_TOKEN")
    chat_id = st.secrets.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en Secrets."

    pendientes = df_c[(df_c["activa"]) & (df_c["dias_restantes"] <= 3)] if not df_c.empty else pd.DataFrame()
    if pendientes.empty:
        msg = f"✅ *Sin pagos urgentes hoy*\n💰 *Saldo en cartera:* ${saldo:.2f}"
    else:
        total = float(pendientes["monto"].sum())
        lineas = [
            f"• *{r['servicio']}* ({r['usuario']}) — ${float(r['monto']):.2f} | {r['estado']}"
            for _, r in pendientes.iterrows()
        ]
        msg = (
            "🔔 *Pagos Streaming Pendientes*\n\n"
            + "\n".join(lineas)
            + f"\n\n💰 *Saldo:* ${saldo:.2f}\n📉 *Pendiente:* ${total:.2f}\n"
            + f"📊 *Quedarían:* ${saldo - total:.2f}"
        )

    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
        timeout=10,
    )
    return r.ok, r.text

# --- INTERFAZ ---
df_c, df_m, saldo = cargar_datos()

st.title("📺 Control de Pagos Streaming")

# KPIs superiores
comprometido_7d = (
    float(df_c[(df_c["activa"]) & (df_c["dias_restantes"] <= 7)]["monto"].sum())
    if not df_c.empty else 0.0
)
gasto_mensual = (
    float(((df_c[df_c["activa"]]["monto"].astype(float) / df_c[df_c["activa"]]["frecuencia_dias"]) * 30).sum())
    if not df_c.empty and df_c["activa"].any() else 0.0
)
urgentes = (
    int(len(df_c[(df_c["activa"]) & (df_c["dias_restantes"] <= 3)]))
    if not df_c.empty else 0
)

k1, k2, k3, k4 = st.columns(4)
k1.metric("💰 Saldo en Cartera", f"${saldo:,.2f}")
k2.metric("📅 Comprometido (7 días)", f"${comprometido_7d:,.2f}", delta=f"${saldo - comprometido_7d:,.2f} libres")
k3.metric("📆 Gasto Mensual Est.", f"${gasto_mensual:,.2f}")
k4.metric("🔔 Alertas (≤3 días)", urgentes)

with st.sidebar:
    st.subheader("📲 Telegram")
    if st.button("Enviar alerta de prueba", use_container_width=True):
        ok, resp = enviar_telegram_prueba(df_c, saldo)
        if ok:
            st.success("Mensaje enviado a tu Telegram.")
        else:
            st.error(f"Error: {resp}")

tab1, tab2, tab3 = st.tabs(["📋 Cuentas y Pagos", "➕ Nueva Cuenta", "💳 Cartera"])

# --- TAB 1: CUENTAS Y PAGOS ---
with tab1:
    if df_c.empty:
        st.info("Aún no tienes cuentas registradas. Agrega la primera en la pestaña 'Nueva Cuenta'.")
    else:
        por_pagar = df_c[(df_c["activa"]) & (df_c["dias_restantes"] <= 3)]
        if not por_pagar.empty:
            st.subheader("⚡ Acciones rápidas (vencidas o por vencer)")
            for _, row in por_pagar.iterrows():
                c1, c2, c3 = st.columns([4, 2, 2])
                c1.markdown(f"**{row['servicio']}** — `{row['usuario']}`  \n{row['estado']} | Próximo: `{row['proximo_pago']}`")
                c2.markdown(f"### ${float(row['monto']):.2f}")
                if c3.button("✅ Confirmar Pago", key=f"pay_{row['id']}", use_container_width=True):
                    registrar_pago(row)
                    st.toast(f"Pago de {row['servicio']} registrado.")
                    st.rerun()
            st.divider()

        st.subheader("Todas las cuentas")
        mostrar_pausadas = st.checkbox("Mostrar cuentas pausadas", value=True)
        vista = df_c if mostrar_pausadas else df_c[df_c["activa"]]

        st.dataframe(
            vista[["estado", "usuario", "servicio", "monto", "frecuencia_dias", "ultimo_pago", "proximo_pago", "activa"]],
            use_container_width=True,
            hide_index=True,
        )

        # Gestión rápida de cualquier cuenta
        with st.expander("⚙️ Pagar adelantado, pausar o eliminar una cuenta"):
            opciones = {f"#{r['id']} | {r['servicio']} ({r['usuario']})": r for _, r in df_c.iterrows()}
            sel = st.selectbox("Selecciona una cuenta", list(opciones.keys()))
            cuenta_sel = opciones[sel]
            b1, b2, b3 = st.columns(3)
            if b1.button("💵 Registrar Pago Hoy", use_container_width=True):
                registrar_pago(cuenta_sel)
                st.rerun()
            nuevo_estado = not bool(cuenta_sel["activa"])
            texto_toggle = "▶️ Reactivar" if nuevo_estado else "⏸️ Pausar"
            if b2.button(texto_toggle, use_container_width=True):
                supabase.table("cuentas_streaming").update({"activa": nuevo_estado}).eq("id", int(cuenta_sel["id"])).execute()
                st.rerun()
            if b3.button("🗑️ Eliminar", use_container_width=True):
                supabase.table("cuentas_streaming").delete().eq("id", int(cuenta_sel["id"])).execute()
                st.rerun()

# --- TAB 2: NUEVA CUENTA ---
with tab2:
    with st.form("form_nueva_cuenta", clear_on_submit=True):
        c1, c2 = st.columns(2)
        usuario = c1.text_input("Correo / Usuario", placeholder="correo@gmail.com")
        servicio = c2.text_input("Servicio", placeholder="Netflix, Disney+, Max, Spotify...")
        c3, c4, c5 = st.columns(3)
        monto = c3.number_input("Monto ($)", min_value=0.01, value=15.0, step=0.5)
        frecuencia = c4.number_input("Frecuencia (días)", min_value=1, value=30, step=1)
        ultimo_pago = c5.date_input("Fecha del último pago", value=HOY)

        if st.form_submit_button("Guardar cuenta", use_container_width=True):
            if not usuario.strip() or not servicio.strip():
                st.warning("Completa el correo y el servicio.")
            else:
                supabase.table("cuentas_streaming").insert({
                    "usuario": usuario.strip(),
                    "servicio": servicio.strip(),
                    "monto": float(monto),
                    "frecuencia_dias": int(frecuencia),
                    "ultimo_pago": ultimo_pago.isoformat(),
                    "activa": True,
                }).execute()
                st.success(f"Cuenta {servicio} ({usuario}) agregada.")
                st.rerun()

# --- TAB 3: CARTERA ---
with tab3:
    with st.form("form_cartera", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        tipo = c1.selectbox("Tipo de movimiento", ["Recarga", "Ajuste", "Pago"])
        monto_mov = c2.number_input("Monto ($)", min_value=0.01, value=50.0, step=1.0)
        concepto = c3.text_input("Concepto", placeholder="Ej. Recarga Binance / Pago móvil")

        if st.form_submit_button("Registrar movimiento", use_container_width=True):
            valor_final = -abs(float(monto_mov)) if tipo == "Pago" else abs(float(monto_mov))
            supabase.table("cartera_movimientos").insert({
                "fecha": HOY.isoformat(),
                "tipo": tipo,
                "concepto": concepto.strip() or tipo,
                "monto": valor_final,
            }).execute()
            st.success("Movimiento registrado en cartera.")
            st.rerun()

    st.subheader("Historial de movimientos")
    if not df_m.empty:
        st.dataframe(df_m[["fecha", "tipo", "concepto", "monto"]], use_container_width=True, hide_index=True)
    else:
        st.info("Sin movimientos registrados.")

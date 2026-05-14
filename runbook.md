# Runbook operativo — API Cantina La Castellana

Procedimientos diarios, semanales y de excepción. Cubre el requisito **runbook mínimo** del prerrequisito ready-to-adopt (Sección 5.2 M4, Tier A).

---

## 1. Operación diaria (rutina)

### 1.1 Mañana — 9:30 AM (gerente, antes del surtido de 10:00)

1. **Abrir el frontend** y autenticarse como gerente.
2. **Generar la predicción del día:**
   - El frontend invoca `POST /model/predict` con la fecha de hoy.
   - El sistema responde con la lista de SKUs, botellas, refuerzos y alertas contextuales del LLM.
3. **Revisar la propuesta:** ¿hay algún SKU con cantidad obviamente fuera de rango? ¿alguna alerta contextual importante (clima, marcha, evento)?
4. **Firmar la orden:**
   - Si todo es correcto → `POST /model/predict/{fecha}/signoff` con `modifications: {}`.
   - Si hay ajustes → registrar cada modificación: `modifications: {"SKU_X": {"original": 12, "modified": 8}}`.
5. **Descargar PDF:** `GET /model/predict/{fecha}/pdf`. Archivar en la carpeta del día.

**Modo shadow:** el PDF se archiva pero **no se entrega al corredor**. La operación sigue como siempre.

### 1.2 Durante la jornada (barman, cada vez que sea necesario)

Cada reposición intra-día se registra como nota a barra:

`POST /model/logs/nota-barra`
```json
{
  "nota_date": "2026-05-13",
  "sku": "RON BACARDI BLANCO (Sencillo)",
  "quantity": 3,
  "bloque_horario": "DEMANDA MEDIA",
  "reason": "Acabado para la mesa 8"
}
```

> **Este es el dato más valioso del proyecto (impact proxy Tier B).** Cuantas menos notas se registren, mejor está funcionando el sistema.

### 1.3 Noche — al cierre (gerente)

1. **Cargar ventas reales del día.** Dos opciones:
   - Una por una: `POST /model/logs/actual-sale`
   - Bulk desde CSV exportado del POS: `POST /model/logs/actual-sales/bulk` con `{"csv_b64": "..."}` (columnas: `sale_date, sku, units_sold, factor_impacto`).
2. **Revisar la comparación del día:** `GET /model/logs/daily-comparison/{fecha}`. Muestra predicho vs real vs notas, y WAPE observado.

---

## 2. Operación semanal (admin / data scientist)

### 2.1 Lunes — chequeo de drift

`POST /model/drift/check`

El sistema devuelve:
- `data_drift`: test KS sobre distribuciones de venta reciente vs baseline (alerta si `p < 0.01`).
- `model_drift`: ratio WAPE observado / esperado (alerta si `>= 1.20`).
- `pipeline_health`: % de días con predicción en últimos 14 días (alerta si `< 0.95`).

**Si `alert_triggered: true`:**
1. Revisar `alert_reasons` y la pestaña de "evidencia" en el frontend.
2. Decidir si procede reentrenar (no es automático).
3. Si sí, ejecutar el procedimiento de reentrenamiento (sección 3).

**Si `alert_triggered: false`:** no hacer nada. Documentar el chequeo en la bitácora.

### 2.2 Reportes Tier B

Antes del próximo Milestone, exportar los datos para el paquete `04_TIER_B_METRICAS/`:

```bash
# Ventana últimos 30 días
curl -H "Authorization: Bearer $TOKEN" \
  https://cantinalcastellana.com/model/metrics/adoption > adoption_30d.json
curl -H "Authorization: Bearer $TOKEN" \
  https://cantinalcastellana.com/model/metrics/impact > impact_30d.json
curl -H "Authorization: Bearer $TOKEN" \
  https://cantinalcastellana.com/model/metrics/wape > wape_30d.json
```

---

## 3. Procedimiento de reentrenamiento manual

**Cuándo ejecutar:**
- `drift/check` arroja alerta de model_drift sostenida (>2 semanas seguidas).
- Cambia de manera material la operación de la cantina (nuevos SKUs, cambios de horario, etc.).
- El gerente reporta que el modelo está consistentemente fuera.

**No ejecutar automáticamente.** Cada retrain queda registrado en `retrain_jobs` con todas las configuraciones probadas para auditoría (Sección 3.6 M4).

### 3.1 Pasos

1. **Login como admin.**
2. **(Opcional) Hacer backup** del modelo activo antes:
   ```bash
   cp data/models/current/*.joblib data/models/backup_$(date +%Y%m%d).joblib
   ```
3. **Invocar retrain:**
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     https://cantinalcastellana.com/model/admin/retrain \
     -d '{"include_staging_logs": true, "notes": "Drift alert sostenida; reentrene rutinario"}'
   ```
4. **Esperar** (5-15 minutos dependiendo del tamaño del dataset y el número de configs en el grid).
5. **Revisar respuesta:**
   - `decision: "promote"` → la mejor config superó al modelo actual; se promovió y ahora es la activa.
   - `decision: "reject"` → ninguna config superó al modelo actual; **se mantiene el modelo previo (failsafe)**. Esto es **comportamiento correcto**, no un error.
6. **Verificar:** `GET /model/admin/models` debe mostrar el modelo correcto como `is_active: true`.
7. **Smoke test:** `POST /model/predict` con la fecha de mañana; verificar que `fallback_used: false`.

### 3.2 Si el retrain falla

- Mirar `GET /model/admin/retrain/jobs/{id}` para ver `error_message`.
- El modelo previo sigue activo (failsafe).
- Reportar el error al equipo de DS para diagnóstico.

### 3.3 Revertir a una versión anterior

```bash
# Listar versiones
curl -H "Authorization: Bearer $TOKEN" https://cantinalcastellana.com/model/admin/models

# Promover una específica
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  https://cantinalcastellana.com/model/admin/models/original_M3/promote \
  -d '{"notes": "Rollback - el último candidato no rendía en producción"}'
```

---

## 4. Procedimiento de reset (sinodales / evaluación)

**Para qué:** los sinodales necesitan probar la API con el modelo entregado en el M3 sin importar cuántos reentrenamientos se hayan hecho después.

```bash
# 1. Login con cuenta sinodal
TOKEN=$(curl -s -X POST https://cantinalcastellana.com/model/auth/login \
  -d "username=sinodal_demo&password=Sinodal2026!Demo" | jq -r .access_token)

# 2. Reset
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://cantinalcastellana.com/model/admin/reset

# 3. Verificar
curl -H "Authorization: Bearer $TOKEN" \
  https://cantinalcastellana.com/model/health
# Debe mostrar "active_model": "original_M3"
```

Una vez probado, el admin puede volver a promover el modelo de producción usando `/admin/models/{version}/promote`.

---

## 5. Manejo de fallos (CD / acción ante falla)

### 5.1 Si el modelo principal falla

El sistema **automáticamente** cae al baseline PM 4 semanas y marca `fallback_used: true` en la respuesta de `/predict` y en el log JSON correspondiente. Esto cumple con el requisito de "acción ante falla" del Tier A.

**Acciones manuales:**
1. Revisar `data/logs/pm2-error.log` para identificar la causa.
2. Si es por modelo corrupto: restaurar de `data/models/backup_*.joblib` con `/admin/models/{version}/promote`.
3. Si es por datos: revisar el snapshot CSV.

### 5.2 Si la API no responde

PM2 reinicia el proceso automáticamente (`autorestart: true` en `ecosystem.config.js`). Si entra en bucle, ver:
```bash
pm2 logs castellana-model-api --lines 200
```

### 5.3 Si el LLM (OpenAI) falla

Fallback silencioso: el sistema devuelve alertas vacías y marca `fallback_used: true` en el cache. **No bloquea la predicción.** Se registra en `llm_alerts` para auditoría.

### 5.4 Si la BD se corrompe

```bash
# Backup automático recomendado en cron diario:
0 3 * * * cp /home/castellana/api/data/castellana.db \
              /home/castellana/api/data/backups/castellana_$(date +\%Y\%m\%d).db
```

Restaurar:
```bash
cp data/backups/castellana_20260512.db data/castellana.db
pm2 restart castellana-model-api
```

---

## 6. Auditoría (sinodales)

Toda acción sensible queda en `audit_log`. Para consultar:

```bash
# Últimas 100 acciones
curl -H "Authorization: Bearer $TOKEN" \
  https://cantinalcastellana.com/model/admin/audit-log

# Filtrar por acción específica
curl -H "Authorization: Bearer $TOKEN" \
  "https://cantinalcastellana.com/model/admin/audit-log?action=model_reset_to_original"
```

Acciones registradas:
- `login_success`, `login_failed`
- `user_created`
- `predict_success`, `predict_failed`
- `signoff`
- `nota_barra_registrada`, `venta_real_registrada`, `ventas_bulk_upload`
- `drift_check_executed`
- `retrain_started`, `retrain_completed`, `retrain_failed`
- `model_reset_to_original`, `model_promoted`
- `llm_alert_refreshed`

---

## 7. Responsabilidades

| Rol                          | Quién                       | Frecuencia          | Responsabilidad                                                       |
|------------------------------|-----------------------------|---------------------|-----------------------------------------------------------------------|
| Generar predicción diaria    | Gerente                     | Diaria 9:30 AM      | Login, generar, revisar, firmar                                       |
| Registrar notas a barra      | Barman                      | Cada reposición     | Una entrada por nota; preciso en cantidad y SKU                       |
| Cargar ventas reales         | Gerente                     | Diaria al cierre    | Bulk CSV o entrada manual                                             |
| Chequeo de drift             | Admin (Data Scientist)      | Semanal lunes       | Revisar resultado, decidir si reentrenar                              |
| Reentrenamiento manual       | Admin (Data Scientist)      | Por demanda         | Sólo cuando hay justificación de drift sostenido                      |
| Backup BD                    | Sysadmin                    | Diaria cron         | Cron a las 3 AM (ver Sec 5.4)                                         |
| Reset modelo (evaluación)    | Sinodal                     | Durante examen      | Login → `/admin/reset` → probar                                       |

---

## 8. Lista de comprobación de salud del sistema (semanal)

- [ ] `GET /model/health` responde 200 OK con `active_model` no nulo
- [ ] `pm2 status` muestra `castellana-model-api` en `online`
- [ ] `pytest tests/` pasa al 100% (correr antes de cualquier despliegue)
- [ ] `GET /metrics/dashboard` devuelve datos coherentes con la operación de la semana
- [ ] `POST /drift/check` ejecutado al menos 1 vez en la semana, resultado documentado
- [ ] Espacio disponible en `data/` (>1 GB libre)
- [ ] Backup más reciente <24h en `data/backups/`

---

## 9. Contacto de escalación

| Tipo de incidencia                          | Contacto                                |
|---------------------------------------------|-----------------------------------------|
| Servidor caído, errores 500 repetidos       | Pepito (dueño operativo)                |
| Modelo dando predicciones erráticas         | José Emilio Kuri (DS)                   |
| Pregunta metodológica / académica           | Prof. Luis Fernando Lupián (UP)         |
| Cambios al esquema de BD o flujo            | Pepito + José Emilio (coordinar)        |

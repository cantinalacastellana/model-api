# API Cantina La Castellana — Predicción de Demanda

Wrapper FastAPI del modelo LightGBM M3 (Jose Emilio Kuri Otero, Maestría en Ciencia de Datos UP, Primavera 2026). Genera órdenes diarias de surtido, monitorea drift, soporta reentrenamiento manual con grid search, y mantiene una bitácora de auditoría completa.

Este servicio es el componente "ready-to-adopt" descrito en la Sección 5.2 del entregable M4 (Tier A) y el habilitador del Tier B vía registro de signoffs, notas a barra y ventas reales.

---

## 1. Arquitectura

```
┌────────────────┐    HTTPS     ┌──────────┐    proxy_pass /model    ┌──────────────────────┐
│ Frontend React │ ───────────▶ │  nginx   │ ──────────────────────▶ │  FastAPI (Python)    │
│ (Vite + TS)    │              │          │                         │  127.0.0.1:8001      │
└────────────────┘              │          │ ──── /menu /chat ─────▶ │  Node.js (existente) │
                                └──────────┘                         └──────────────────────┘
                                                                              │
                                                                              ▼
                                                                     ┌──────────────────────┐
                                                                     │ SQLite castellana.db │
                                                                     │ data/models/         │
                                                                     │ data/outputs/        │
                                                                     │ data/logs/           │
                                                                     └──────────────────────┘
```

**No invade los endpoints de Node.js.** Las rutas Node siguen sirviendo `/menu` y `/chat`. Las rutas Python viven bajo `/model` (configurable en `nginx`).

---

## 2. Instalación inicial

### 2.1 Requisitos

- Python 3.10+ (probado en 3.11/3.12)
- Node.js (ya instalado para los servicios existentes)
- PM2 (ya corriendo)
- nginx (ya configurado)

### 2.2 Setup

```bash
# 1. Clonar / copiar a /home/castellana/api
cd /home/castellana/api

# 2. Instalar dependencias (idealmente en un venv)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configurar variables de entorno
cp .env.example .env
# Editar .env: poner OPENAI_API_KEY y generar JWT_SECRET_KEY con:
# openssl rand -hex 32

# 4. Inicializar BD + crear primer admin
python scripts/init_db.py

# 5. Colocar el artefacto M3
# Copiar lgbm_v1.joblib       -> data/models/original/
# Copiar df_ml_ready_for_M3.csv -> data/models/original/

# 6. Registrar el modelo original
python scripts/seed_original_model.py

# 7. (Opcional) Crear usuarios de prueba para los sinodales
python scripts/seed_users.py
```

### 2.3 Levantar la API

**Modo desarrollo:**
```bash
uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

**Producción con PM2:**
```bash
pm2 start ecosystem.config.js
pm2 save
pm2 logs castellana-model-api
```

### 2.4 Configurar nginx

Agregar al server block existente:

```nginx
location /model/ {
    proxy_pass http://127.0.0.1:8001/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
    proxy_read_timeout 600s;   # reentrenamiento puede tardar
    proxy_send_timeout 600s;
    client_max_body_size 50M;  # uploads de CSVs grandes
}
```

Reload nginx:
```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## 3. Endpoints

Toda la URL relativa al server externo es `https://cantinalcastellana.com/model/<endpoint>`. Documentación interactiva: `/model/docs`.

### 3.1 Autenticación

| Método | Endpoint        | Rol         | Descripción                                  |
|--------|-----------------|-------------|----------------------------------------------|
| POST   | `/auth/login`   | público     | OAuth2 password; devuelve JWT                |
| GET    | `/auth/me`      | cualquiera  | Datos del usuario actual                     |
| POST   | `/auth/users`   | admin       | Crear nuevo usuario                          |
| GET    | `/auth/users`   | admin       | Listar usuarios                              |

### 3.2 Predicción y firma

| Método | Endpoint                          | Rol                          | Descripción                              |
|--------|-----------------------------------|------------------------------|------------------------------------------|
| POST   | `/predict`                        | admin/manager/sinodal        | Generar orden del día                    |
| GET    | `/predict/{fecha}`                | cualquiera                   | Consultar predicción existente           |
| GET    | `/predict/{fecha}/pdf`            | cualquiera                   | Descargar PDF de orden                   |
| GET    | `/predict/{fecha}/csv`            | cualquiera                   | Descargar CSV de predicción              |
| POST   | `/predict/{fecha}/signoff`        | manager/admin                | Firma del gerente (con modificaciones)   |
| GET    | `/predict/{fecha}/signoff`        | cualquiera                   | Consultar firma                          |

### 3.3 Logs operativos (Tier B)

| Método | Endpoint                            | Rol                   | Descripción                              |
|--------|-------------------------------------|-----------------------|------------------------------------------|
| POST   | `/logs/nota-barra`                  | barman/manager/admin  | Registrar reposición intra-día           |
| GET    | `/logs/notas/{fecha}`               | cualquiera            | Listar notas del día                     |
| POST   | `/logs/actual-sale`                 | manager/admin         | Registrar venta real al cierre           |
| POST   | `/logs/actual-sales/bulk`           | manager/admin         | Carga masiva CSV (base64)                |
| GET    | `/logs/sales/{fecha}`               | cualquiera            | Listar ventas reales                     |
| GET    | `/logs/daily-comparison/{fecha}`    | cualquiera            | Pred vs Real vs Notas + WAPE observado   |

### 3.4 Drift (alerta, NO auto-retrain)

| Método | Endpoint           | Rol         | Descripción                                  |
|--------|--------------------|-------------|----------------------------------------------|
| POST   | `/drift/check`     | admin       | Ejecutar chequeo                             |
| GET    | `/drift/status`    | cualquiera  | Último estado (para badge en frontend)       |
| GET    | `/drift/history`   | cualquiera  | Historial                                    |

### 3.5 Administración

| Método | Endpoint                              | Rol            | Descripción                                |
|--------|---------------------------------------|----------------|--------------------------------------------|
| POST   | `/admin/retrain`                      | admin          | Grid search con failsafe (manual)          |
| GET    | `/admin/retrain/jobs`                 | admin/sinodal  | Historial                                  |
| GET    | `/admin/retrain/jobs/{id}`            | admin/sinodal  | Detalle de un job                          |
| POST   | `/admin/reset`                        | **admin/sinodal** | Restaurar modelo M3 original (sinodales) |
| GET    | `/admin/models`                       | admin/sinodal/manager | Listar versiones                     |
| POST   | `/admin/models/{version}/promote`     | admin          | Promover versión específica                |
| GET    | `/admin/audit-log`                    | admin/sinodal  | Bitácora completa de auditoría             |

### 3.6 LLM (alertas contextuales)

| Método | Endpoint                          | Rol         | Descripción                          |
|--------|-----------------------------------|-------------|--------------------------------------|
| GET    | `/llm/alerts/{fecha}`             | cualquiera  | Obtener alerta (cache)               |
| POST   | `/llm/alerts/refresh/{fecha}`     | admin       | Regenerar alerta                     |

### 3.7 Métricas (Tier B)

| Método | Endpoint                  | Rol         | Descripción                                  |
|--------|---------------------------|-------------|----------------------------------------------|
| GET    | `/metrics/adoption`       | cualquiera  | Tasa de firma + cobertura                    |
| GET    | `/metrics/impact`         | cualquiera  | Notas a barra por día (impact proxy)         |
| GET    | `/metrics/wape`           | cualquiera  | WAPE observado por día                       |
| GET    | `/metrics/dashboard`      | cualquiera  | Agregado para el frontend                    |

---

## 4. Roles y permisos

| Rol        | Capacidades                                                                                                          |
|------------|----------------------------------------------------------------------------------------------------------------------|
| **admin**  | Todo. Genera predicciones, firma, registra ventas, ejecuta retrain, promueve modelos, resetea, gestiona usuarios.    |
| **manager**| Genera predicciones, descarga PDF/CSV, firma órdenes con modificaciones, registra ventas reales y carga bulk CSV.    |
| **barman** | Sólo registra notas a barra (reposiciones intra-día).                                                                |
| **sinodal**| Read-only sobre todo el sistema **+** acceso al endpoint `POST /admin/reset` para restaurar el modelo M3 original.   |

---

## 5. Modo de operación

Variable de entorno `OPERATION_MODE` controla el comportamiento:

| Modo         | Comportamiento                                                                              |
|--------------|---------------------------------------------------------------------------------------------|
| `shadow`     | (default) Genera PDF + log con timestamp, pero el PDF *no se entrega* al corredor. La operación sigue normal sin sistema. Esta es la evidencia de Tier A (shadow mode ejecutado). |
| `piloto`     | El PDF se entrega al corredor; se siguen capturando notas a barra y ventas reales para comparación. |
| `produccion` | Operación plena.                                                                            |

**Cambiar el modo:** editar `.env` (o `ecosystem.config.js`) y reiniciar PM2.

---

## 6. Reset al modelo original (sinodales)

El endpoint `POST /admin/reset` es **crítico para la evaluación**. Permite a los sinodales restaurar el modelo M3 entregado en el Freeze, sin importar cuántos reentrenamientos se hayan hecho después.

```bash
# Login como sinodal
TOKEN=$(curl -s -X POST https://cantinalcastellana.com/model/auth/login \
  -d "username=sinodal_demo&password=Sinodal2026!Demo" | jq -r .access_token)

# Reset
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://cantinalcastellana.com/model/admin/reset
```

Respuesta:
```json
{
  "reset_at": "2026-06-10T15:30:00",
  "previous_active_version": "lgbm_20260520_181203_H2.3",
  "restored_version": "original_M3",
  "message": "Modelo restaurado al original M3. ..."
}
```

---

## 7. Tests (CI / Tier A)

```bash
# Correr toda la suite
pytest tests/ -v

# Sólo guardrails (baseline tests)
pytest tests/test_guardrails.py -v

# Sólo integración de endpoints
pytest tests/test_endpoints.py -v
```

**Resultado esperado:** 46/46 tests passing. Si CUALQUIER test falla, no levantar a producción (regla del CI mínimo de Tier A).

---

## 8. Despliegue checklist (Track 2 — piloto/shadow acotado)

- [ ] Servidor con Python 3.10+, nginx, PM2 funcionando
- [ ] `pip install -r requirements.txt` sin errores
- [ ] `.env` con `JWT_SECRET_KEY` real, `OPENAI_API_KEY` real, `OPERATION_MODE=shadow`
- [ ] `scripts/init_db.py` ejecutado, primer admin creado con password seguro
- [ ] `data/models/original/lgbm_v1.joblib` y `df_ml_ready_for_M3.csv` copiados
- [ ] `scripts/seed_original_model.py` ejecutado; `GET /admin/models` muestra `original_M3` con `is_active=1`
- [ ] `pytest tests/ -v` → 46/46 OK
- [ ] `pm2 start ecosystem.config.js` y `pm2 save`
- [ ] `nginx -t && systemctl reload nginx` con la nueva location `/model/`
- [ ] Smoke test: `curl /model/health` → 200 OK
- [ ] Sinodal puede invocar `/auth/login` → `/admin/reset` correctamente

---

## 9. Troubleshooting

| Síntoma                                         | Causa probable                                  | Solución                                                          |
|-------------------------------------------------|-------------------------------------------------|-------------------------------------------------------------------|
| `/health` responde `active_model: null`         | No se corrió `seed_original_model.py`           | Ejecutarlo                                                        |
| `/predict` siempre devuelve `fallback_used:true`| Modelo activo no se puede cargar de disco       | Verificar `data/models/.../lgbm_v1.joblib` existe y es legible    |
| `/predict` lento (>30s)                         | Snapshot CSV muy grande                         | Considerar reducir ventana histórica o cachear features           |
| LLM no devuelve alertas                         | `OPENAI_API_KEY` vacía o cuota agotada          | El sistema sigue funcionando con `fallback_used:true` en `llm_alerts` |
| `/admin/retrain` falla con "Snapshot no existe" | `df_ml_ready_for_M3.csv` faltante               | Copiarlo a `data/models/original/`                                |
| 401 inesperado                                  | Token expiró (8h)                               | Re-loguear                                                        |
| PM2 no levanta                                  | Puerto 8001 ocupado                             | `lsof -i :8001` y cambiar puerto en `.env` y `ecosystem.config.js`|

---

## 10. Mapeo a la rúbrica M4

| Criterio M4                                      | Cubierto por                                                                                              |
|--------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| Sec 5.1 — Flujo mínimo end-to-end                | `POST /predict` produce PDF + CSV + log con timestamp                                                     |
| Sec 5.2 Tier A — handoff utilizable              | Este README + `runbook.md` + `/docs` (OpenAPI)                                                            |
| Sec 5.2 Tier A — runbook                         | Ver `runbook.md`                                                                                          |
| Sec 5.2 Tier A — validación stakeholder          | `POST /predict/{fecha}/signoff` deja registro firmado                                                     |
| Sec 5.2 Tier A — CI: baseline + unit tests       | `pytest tests/` (46 tests, incluye guardrails, drift, auth, endpoints)                                    |
| Sec 5.2 Tier A — CD: drift checks                | `/drift/check` con KS test (p<0.01) + ratio WAPE (>=1.2x) + pipeline health (<95%)                        |
| Sec 5.2 Tier A — acción ante falla               | Fallback automático a baseline PM 4 semanas; alerta de drift; abstención por GR3; registro en audit_log   |
| Sec 5.2 Tier B — métrica de adopción             | `/metrics/adoption` calcula tasa firma sin modificaciones (objetivo Freeze ≥80%)                          |
| Sec 5.2 Tier B — impact proxy                    | `/metrics/impact` calcula notas a barra por día (objetivo: reducir vs baseline)                           |
| Sec 5.2 Tier B — ventana y trazabilidad          | Parámetros `desde`/`hasta` + todos los registros con `created_at`/`timestamp`                             |
| Sec 3.6 — Trazabilidad de tuning                 | `retrain_jobs` registra cada config probada en grid search; PROMPT_VERSION del LLM versionado            |
| Sec 4.4 — Política de uso (guardrails)           | GR1-GR4 implementados en `services/guardrails.py` con tests de validación                                 |
| Track 2 — ejecución verificable + validación     | `/predict` deja outputs con timestamps en `data/outputs/`; `/signoff` deja validación explícita           |

---

## 11. Estructura del proyecto

```
api/
├── main.py                          # FastAPI entry point
├── config.py                        # Settings (pydantic-settings)
├── database.py                      # Esquema SQLite + helpers
├── auth.py                          # JWT + bcrypt + roles
├── requirements.txt
├── ecosystem.config.js              # PM2 config
├── .env.example
├── README.md                        # este archivo
├── runbook.md                       # operación diaria
├── routers/                         # endpoints FastAPI
│   ├── auth_router.py
│   ├── predict_router.py
│   ├── logs_router.py
│   ├── drift_router.py
│   ├── admin_router.py
│   ├── llm_router.py
│   └── metrics_router.py
├── services/                        # lógica de negocio
│   ├── feature_engineering.py       # 32 features M3
│   ├── guardrails.py                # GR1-GR4
│   ├── conversion_botellas.py       # Recetas
│   ├── prediction_service.py        # Orquestador + fallback
│   ├── drift_service.py             # KS + WAPE + health
│   ├── retrain_service.py           # Grid search + failsafe
│   ├── llm_service.py               # OpenAI con fallback silencioso
│   └── pdf_renderer.py              # reportlab
├── schemas/                         # Pydantic models
│   ├── auth.py
│   ├── predict.py
│   └── admin.py
├── scripts/
│   ├── init_db.py
│   ├── seed_original_model.py
│   └── seed_users.py
├── tests/                           # 46 tests (CI mínimo Tier A)
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_conversion_botellas.py
│   ├── test_drift.py
│   ├── test_endpoints.py
│   └── test_guardrails.py
└── data/
    ├── castellana.db                # SQLite (auto)
    ├── models/
    │   ├── original/                # M3 inmutable
    │   ├── current/                 # activo
    │   └── candidates/              # del grid search
    ├── outputs/                     # PDFs, CSVs, logs por día
    ├── logs/                        # PM2 logs
    └── staging/                     # ventas nuevas para retrain
```

---

## 12. Autores y contacto

- **Modelo M3 y artefacto base:** José Emilio Kuri Otero (jose.kuri@up.edu.mx)
- **Stakeholder / dueño operativo:** Cantina La Castellana (Pepito)
- **Profesor evaluador:** Luis Fernando Lupián Sánchez (lflupian@up.edu.mx)

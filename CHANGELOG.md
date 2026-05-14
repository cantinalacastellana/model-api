# Changelog

Todos los cambios notables a la API quedan documentados aquí.

## [1.0.0] - 2026-05-13 — Sprint inicial M4

### Agregado
- API FastAPI con 38 endpoints bajo el prefijo `/model` para no colisionar con los servicios Node.js (`/menu`, `/chat`) que ya corren en PM2.
- Autenticación JWT con bcrypt y 4 roles (`admin`, `manager`, `barman`, `sinodal`).
- Wrapper del modelo LightGBM M3 (Jose Emilio Kuri Otero) con:
  - Feature engineering completo (32 features, anti-leakage).
  - Guardrails GR1–GR4 incrementales (test-covered).
  - Conversión a botellas (sencillo 1.5oz / doble 3oz / botella 25.36oz).
  - Refuerzo vespertino calculado de la historia diaria.
  - Fallback automático a baseline PM 4 semanas ante cualquier fallo.
- Generación de PDF de orden de surtido (reportlab) con resumen de guardrails y alertas contextuales.
- Drift checks (data drift KS, model drift ratio, pipeline health). **Sólo alertan; nunca reentrenan automáticamente.**
- Endpoint `POST /admin/retrain` con grid search de 7 configuraciones, early stopping, failsafe (sólo promueve si supera al modelo activo) y registro completo de cada configuración probada.
- Endpoint `POST /admin/reset` accesible para rol `sinodal` que restaura el modelo M3 original.
- Endpoints de logs operativos (notas a barra, ventas reales, comparación diaria) para alimentar Tier B.
- Endpoints de métricas (`/metrics/adoption`, `/metrics/impact`, `/metrics/wape`, `/metrics/dashboard`).
- Agente LLM analista con OpenAI (gpt-4o-mini), prompt versionado y fallback silencioso.
- Bitácora de auditoría completa en `audit_log` para todas las acciones sensibles.
- 46 tests (pytest) cubriendo guardrails, drift, autenticación, conversiones y endpoints clave.
- Documentación: `README.md` + `runbook.md` operativo.
- Configuración para despliegue con PM2 (`ecosystem.config.js`) y plantilla `.env.example`.
- Scripts de inicialización: `init_db.py`, `seed_original_model.py`, `seed_users.py`.

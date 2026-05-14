// ============================================================
// ecosystem.config.js - Configuracion PM2 para la API
// ------------------------------------------------------------
// La instancia de PM2 que ya corre Node.js para /menu y /chat se
// extiende con este proceso Python. Ambos coexisten en la misma
// instancia PM2.
//
// Despliegue:
//   pm2 start ecosystem.config.js
//   pm2 save
//   pm2 logs castellana-model-api
//
// Stop / restart:
//   pm2 restart castellana-model-api
//   pm2 stop castellana-model-api
// ============================================================

module.exports = {
  apps: [{
    name: "castellana-model-api",
    script: "/root/model-api/.venv/bin/uvicorn",
    args: "main:app --host 127.0.0.1 --port 8001 --workers 2",
    interpreter: "none",
    cwd: "/root/model-api",
    env: {
      PYTHONPATH: "/root/model-api"
    },
    autorestart: true,
    max_memory_restart: "1G",
    out_file: "/root/model-api/data/logs/pm2-out.log",
    error_file: "/root/model-api/data/logs/pm2-error.log",
    merge_logs: true,
    log_date_format: "YYYY-MM-DD HH:mm:ss Z",
    exec_mode: "fork",
    instances: 1,
  }],
};
EOF

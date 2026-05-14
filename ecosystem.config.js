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
  apps: [
    {
      name: "castellana-model-api",
      script: "uvicorn",
      args: "main:app --host 127.0.0.1 --port 8001 --workers 2",
      // Interprete: usar el python del venv si existe, si no el del sistema
      interpreter: "python3",
      cwd: "/home/castellana/api", // ajustar a la ruta real en el servidor
      env: {
        // PYTHONPATH para que importe modulos locales
        PYTHONPATH: "/home/castellana/api",
        // Variables de entorno propias del .env
        // NOTA: NO poner el JWT_SECRET_KEY ni el OPENAI_API_KEY aqui;
        // usar el .env que pydantic-settings lee automaticamente.
      },
      // PM2 reinicia si el proceso muere
      autorestart: true,
      // Reiniciar si la memoria pasa de 1GB (por si LightGBM crece)
      max_memory_restart: "1G",
      // Logs
      out_file: "/home/castellana/api/data/logs/pm2-out.log",
      error_file: "/home/castellana/api/data/logs/pm2-error.log",
      merge_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      // No usar cluster mode (FastAPI con un worker uvicorn unico
      // simplifica la concurrencia con SQLite)
      exec_mode: "fork",
      instances: 1,
    },
  ],
};

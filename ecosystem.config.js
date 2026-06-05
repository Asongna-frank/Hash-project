module.exports = {
  apps: [
    {
      name: "hash-api",
      cwd: "/var/www/Hash-project",
      script: "./venv/bin/uvicorn",
      interpreter: "none",
      // Single worker REQUIRED: (1) in-process WebSocket ConnectionManager for
      // real-time hospital alerts, (2) APScheduler must not double-fire
      // (2 workers = 2 schedulers = duplicate tips/check-ins/SMS).
      args: "app.main:app --host 127.0.0.1 --port 3020 --workers 1",
      autorestart: true,
      max_restarts: 10,
      env: { PYTHONUNBUFFERED: "1" }
    }
  ]
};

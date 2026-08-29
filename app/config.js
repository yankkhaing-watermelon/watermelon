// Point this at your deployed Worker, then redeploy the Pages site.
// e.g. "https://bursamusangking-app.<your-subdomain>.workers.dev"
window.BMK_CONFIG = {
  WORKER_URL: "https://bursamusangking-app.yankhaing.workers.dev",

  // Only needed if you set the optional RUN_TOKEN secret on the Worker.
  // Anything you put here is visible to anyone who opens the page, so treat it
  // as a speed bump against drive-by triggers, not real security.
  RUN_TOKEN: "",

  // How long to keep polling /status after pressing "Run scan now".
  POLL_SECONDS: 420,
};

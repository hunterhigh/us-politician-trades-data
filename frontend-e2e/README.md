# Frontend browser acceptance

This package validates the frozen handoff frontend without modifying `review-input/`.

The fixture builder publishes `backend/examples/synthetic.json` as an explicitly demo-only snapshot in a temporary bare Git repository. It then resolves the real 40-character Git commit and runs the public snapshot reader in `dashboard`, `search`, `person`, and `ticker` modes. Each selection is passed through the pinned handoff processor and renderer. The browser test opens those rendered files in a real system Chromium browser.

The suite covers:

- fixed-commit reads through the production repository contract;
- the supplied processor, evidence query, and renderer;
- shared dashboard view controls and independent 30/90-day filters;
- person-to-ticker navigation and its return path;
- disclosure evidence drawer and keyboard dismissal;
- unknown query, person, and ticker semantics;
- fail-closed content-hash validation;
- browser console and page runtime errors.

It is intentionally offline. It does not fetch GitHub, official disclosure sites, market data, fonts, or browser binaries. CI uses the system Chrome already present on the runner. A local run needs Node 24, Python 3.11+, and Chrome, Chromium, or Edge:

```text
cd frontend-e2e
npm ci
npm test
```

Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` when the browser is installed outside the known system paths.

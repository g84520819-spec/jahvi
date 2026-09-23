(() => {
  const apiBaseUrl = window.JAHVI_API_BASE_URL || '';
  window.JAHVI_API_BASE_URL = apiBaseUrl;
  const userKey = 'jahvi_user';
  const MAX_UPLOAD_BYTES = 1.1 * 1024 * 1024 * 1024; // 1.1GB, flat cap — no more per-plan limits
  const auth = {
    validateProcessingFiles: async files => {
      const user = await auth.loadUserAndCredits();
      if (!user) return 'Please sign in before processing a video.';
      if (Number(user.credits) < 2) return 'You need at least 2 credits to process a video. Please add credits and try again.';
      const limitLabel = `${(MAX_UPLOAD_BYTES / (1024 * 1024 * 1024)).toFixed(1)} GB`;
      const oversized = files.find(file => file && file.size > MAX_UPLOAD_BYTES);
      return oversized ? `This file is too large. The maximum upload is ${limitLabel}.` : null;
    },
    clearSession: () => {
      localStorage.removeItem(userKey);
    },
    saveSession: (data) => { if (data.user) localStorage.setItem(userKey, JSON.stringify(data.user)); },
    loadUserAndCredits: async () => {
      const response = await fetch(`${apiBaseUrl}/api/me`, { credentials: 'include' });
      if (response.status === 401) auth.clearSession();
      if (!response.ok) return null;
      const user = await response.json();
      localStorage.setItem(userKey, JSON.stringify(user));
      updateUser(user);
      return user;
    },
    initialize: async () => {
      const raw = localStorage.getItem(userKey);
      if (raw) updateUser(JSON.parse(raw));
      return auth.loadUserAndCredits();
    },
  };
  function updateUser(user) {
    const name = document.getElementById('userName');
    const avatar = document.getElementById('userAvatar');
    const credits = document.getElementById('creditsLeft');
    const creditsFill = document.getElementById('creditsFill');
    if (name) name.textContent = user.full_name || 'Welcome back';
    if (avatar) avatar.textContent = (user.full_name || '--').split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase();
    if (credits) credits.textContent = user.credits ?? '–';
    if (creditsFill && Number.isFinite(Number(user.credits))) {
      creditsFill.style.setProperty('--fill-percent', `${Math.min(100, Math.max(0, Number(user.credits) * 10))}%`);
    }
  }
  document.addEventListener('click', event => {
    const link = event.target.closest?.('.logout');
    if (!link) return;
    event.preventDefault();
    auth.clearSession();
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 2000);
    fetch(`${apiBaseUrl}/auth/logout`, {
      method: 'POST',
      credentials: 'include',
      signal: controller.signal,
    }).finally(() => {
      window.clearTimeout(timeout);
      window.location.replace('index.html');
    });
  }, true);
  window.JahviAuth = auth;
})();

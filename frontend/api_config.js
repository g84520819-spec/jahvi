(() => {
  const { hostname, protocol } = window.location;
  const forwardedPortPattern = /-(\d+)(?=\.(?:app\.github\.dev|githubpreview\.dev)$)/;
  let apiBaseUrl = window.JAHVI_API_BASE_URL;

  if (apiBaseUrl) {
    apiBaseUrl = apiBaseUrl.replace(/\/$/, '');
  } else if (hostname === 'localhost' || hostname === '127.0.0.1') {
    apiBaseUrl = `${protocol}//${hostname}:8000`;
  } else {
    const backendHost = hostname.replace(forwardedPortPattern, '-8000');
    apiBaseUrl = backendHost === hostname ? `${protocol}//${hostname}:8000` : `${protocol}//${backendHost}`;
  }

  window.JAHVI_API_BASE_URL = apiBaseUrl;
})();

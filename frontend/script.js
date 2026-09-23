// Validate the stored access token, or restore it from the refresh cookie.
async function restoreSession() {
  try {
    let response = await fetch(`${window.JAHVI_API_BASE_URL}/api/me`, { credentials: 'include' });

    if (response.status === 401) {
      const refreshResponse = await fetch(`${window.JAHVI_API_BASE_URL}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      });
      if (refreshResponse.ok) {
        const session = await refreshResponse.json();
        localStorage.setItem('jahvi_user', JSON.stringify(session.user));
        response = await fetch(`${window.JAHVI_API_BASE_URL}/api/me`, { credentials: 'include' });
      }
    }

    if (response.ok) {
      window.location.replace('headshot_extraction_lab.html');
    } else if (response.status === 401) {
      localStorage.removeItem('jahvi_user');
    }
  } catch (error) {
    console.warn('Could not restore the existing session:', error);
  }
}

void restoreSession();

// Sticky nav on scroll
const nav = document.getElementById('nav');
window.addEventListener('scroll', () => {
  nav.classList.toggle('scrolled', window.scrollY > 30);
});

// Generate a randomized waveform with 4 "peak" bars aligned to the AI markers
const waveform = document.getElementById('waveform');
if (waveform) {
  const bars = 64;
  const peakPositions = [9, 25, 40, 54]; // roughly matches marker left% positions
  for (let i = 0; i < bars; i++) {
    const span = document.createElement('span');
    const nearPeak = peakPositions.some(p => Math.abs(p - i) < 3);
    const height = nearPeak
      ? 55 + Math.random() * 45
      : 10 + Math.random() * 40;
    span.style.height = height + '%';
    if (nearPeak) span.classList.add('peak');
    waveform.appendChild(span);
  }
}

// Scroll reveal
const revealEls = document.querySelectorAll('.reveal');
const io = new IntersectionObserver((entries) => {
  entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
}, { threshold: 0.15 });
revealEls.forEach(el => io.observe(el));

// Count-up stats
const counters = document.querySelectorAll('[data-count]');
const countIO = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    const el = entry.target;
    const target = +el.dataset.count;
    const suffix = el.dataset.suffix || '';
    const duration = 1400;
    const start = performance.now();
    function tick(now) {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      const val = Math.floor(eased * target);
      el.textContent = (val >= 1000 ? val.toLocaleString() : val) + suffix;
      if (p < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
    countIO.unobserve(el);
  });
}, { threshold: 0.5 });
counters.forEach(c => countIO.observe(c));

// Subtle 3D tilt on the hero clip cards
document.querySelectorAll('.clip-card').forEach(card => {
  card.addEventListener('mousemove', (e) => {
    const rect = card.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width - 0.5;
    const y = (e.clientY - rect.top) / rect.height - 0.5;
    card.style.setProperty('--tiltX', (-y * 10) + 'deg');
    card.style.setProperty('--tiltY', (x * 10) + 'deg');
  });
});

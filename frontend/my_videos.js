const API_BASE_URL = window.JAHVI_API_BASE_URL || '';
const jobsList = document.getElementById('jobsList');
const jobsStatus = document.getElementById('jobsStatus');
const refreshJobsButton = document.getElementById('refreshJobsBtn');
let refreshInProgress = false;
let hasActiveJobs = false;

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}

function addDetail(container, label, value) {
  const row = document.createElement('div');
  row.className = 'job-detail';
  const name = document.createElement('span');
  name.textContent = label;
  const detail = document.createElement('strong');
  detail.textContent = value || '—';
  row.append(name, detail);
  container.appendChild(row);
}

function renderJob(job) {
  const card = document.createElement('article');
  card.className = 'job-card';
  const heading = document.createElement('div');
  heading.className = 'job-card-heading';
  const title = document.createElement('h2');
  title.textContent = job.edit_type === 'beatsync' ? 'Beat Sync' : job.edit_type === 'extraction' ? 'Headshot Extraction' : 'Dashboard Montage';
  const state = document.createElement('span');
  state.className = `job-state job-state-${job.status}`;
  state.textContent = job.status;
  heading.append(title, state);
  card.appendChild(heading);

  const details = document.createElement('div');
  details.className = 'job-details';
  addDetail(details, 'Job ID', job.id);
  if (job.status === 'queued') addDetail(details, 'Queue position', String((job.queue_position ?? 0) + 1));
  if (job.worker) addDetail(details, 'Worker', job.worker);
  addDetail(details, 'Created', formatDate(job.created_at));
  addDetail(details, 'Started', formatDate(job.started_at));
  if (job.completed_at) addDetail(details, 'Finished', formatDate(job.completed_at));
  card.appendChild(details);

  if (job.status === 'processing' || job.status === 'queued') {
    const progress = document.createElement('div');
    progress.className = 'job-progress';
    const bar = document.createElement('progress');
    bar.max = 100;
    bar.value = Number(job.progress_percent) || 0;
    const message = document.createElement('p');
    message.textContent = job.progress_message || (job.status === 'queued' ? 'Waiting for an available worker' : 'Processing video');
    progress.append(bar, message);
    card.appendChild(progress);
  }

  if (job.error) {
    const error = document.createElement('p');
    error.className = 'job-error';
    error.textContent = job.error;
    card.appendChild(error);
  }

  if (job.video_url) {
    const video = document.createElement('video');
    video.controls = true;
    video.preload = 'metadata';
    video.crossOrigin = 'use-credentials';
    video.src = new URL(job.video_url, API_BASE_URL || window.location.href).href;
    video.className = 'job-video';
    card.appendChild(video);
  }

  if (job.status !== 'processing') {
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'job-delete';
    remove.textContent = 'Delete job';
    remove.addEventListener('click', () => deleteJob(job.id, remove));
    card.appendChild(remove);
  }
  return card;
}

async function getJobs() {
  let response = await fetch(`${API_BASE_URL}/api/videos`, { credentials: 'include' });
  if (response.status === 401) {
    const refresh = await fetch(`${API_BASE_URL}/auth/refresh`, { method: 'POST', credentials: 'include' });
    if (refresh.ok) response = await fetch(`${API_BASE_URL}/api/videos`, { credentials: 'include' });
  }
  if (response.status === 401) {
    window.JahviAuth?.clearSession();
    window.location.replace('login.html');
    return null;
  }
  if (!response.ok) throw new Error(`Could not load jobs (${response.status}).`);
  return response.json();
}

async function loadJobs() {
  if (refreshInProgress) return;
  refreshInProgress = true;
  refreshJobsButton.disabled = true;
  try {
    const result = await getJobs();
    if (!result) return;
    const videos = result.videos || [];
    hasActiveJobs = videos.some(job => job.status === 'queued' || job.status === 'processing');
    jobsList.replaceChildren(...videos.map(renderJob));
    jobsStatus.textContent = videos.length ? `${videos.length} job${videos.length === 1 ? '' : 's'}` : 'No video jobs yet.';
  } catch (error) {
    jobsStatus.textContent = error.message || 'Could not load jobs.';
  } finally {
    refreshInProgress = false;
    refreshJobsButton.disabled = false;
  }
}

async function deleteJob(jobId, button) {
  button.disabled = true;
  try {
    const response = await fetch(`${API_BASE_URL}/api/videos/${encodeURIComponent(jobId)}`, {
      method: 'DELETE',
      credentials: 'include',
    });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new Error(result.detail || `Could not delete job (${response.status}).`);
    }
    await loadJobs();
  } catch (error) {
    jobsStatus.textContent = error.message || 'Could not delete job.';
    button.disabled = false;
  }
}

refreshJobsButton.addEventListener('click', loadJobs);
window.JahviAuth.initialize().then(user => {
  if (!user) {
    window.location.replace('login.html');
    return;
  }
  loadJobs();
  window.setInterval(() => {
    const isPlaying = [...jobsList.querySelectorAll('video')].some(video => !video.paused && !video.ended);
    if (hasActiveJobs && !isPlaying) loadJobs();
  }, 7000);
});

// ============================================================
// Jahvi Beat Sync Lab — plain JS, no framework.
// Same SSE/preview/signature pattern as dashboard.js, plus:
//   - optional custom audio upload (no waveform editing, just upload)
//   - a live analysis log during processing
//   - a decorative pulsing waveform while beats are being detected
//   - a "sync report" stat once the video is ready
// ============================================================

const API_BASE_URL = window.JAHVI_API_BASE_URL || '';

const pageState = {
  videoFile: null,
  audioFile: null,      // optional — stays null if the user skips this
  selectedEffectClassIds: [],
  exportRatio: '9:16',
  musicVolume: 1,
};

// ---------- Icons ----------
const iconLibrary = {
  rage:      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2c1 3-1 4-1 6 0 1.5 1 2 2 2 1.5 0 2-1.5 1.5-3 3 2 4 5 4 7a6.5 6.5 0 0 1-13 0c0-1 .3-2 1-3 .3 1 1 1.5 1.5 1.5A8 8 0 0 1 12 2z"/></svg>',
  snap:      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 3 14h7l-1 8 10-14h-7z"/></svg>',
  flow:      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.7 0-3.1.6-4.5 2-1.4-1.4-2.8-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7z"/></svg>',
  cinematic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 9h18M3 15h18"/></svg>',
  phonk:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14v-3a8 8 0 0 1 16 0v3"/><path d="M3 14h2a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-3a2 2 0 0 1 1-2Z"/><path d="M21 14h-2a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-3a2 2 0 0 0-1-2Z"/></svg>',
};

// ---------- Side nav open/close (mobile drawer) ----------
const hamburgerMenuButton = document.getElementById('menuBtn');
const sidebarPanel = document.getElementById('sidePanel');
const sidebarOverlay = document.getElementById('navOverlay');

hamburgerMenuButton.addEventListener('click', () => {
  sidebarPanel.classList.add('open');
  sidebarOverlay.classList.add('open');
});
sidebarOverlay.addEventListener('click', () => {
  sidebarPanel.classList.remove('open');
  sidebarOverlay.classList.remove('open');
});

const effectClassRow = document.getElementById('effectClassRow');
const reloadEffectsButton = document.getElementById('reloadEffectsBtn');
const effectLoadStatus = document.getElementById('effectLoadStatus');

function setEffectLoadStatus(message, isError = false) {
  effectLoadStatus.textContent = message;
  effectLoadStatus.classList.toggle('error', isError);
}

async function loadEffectCategories() {
  reloadEffectsButton.disabled = true;
  setEffectLoadStatus('Loading effects…');
  try {
    const response = await fetch(`${API_BASE_URL}/api/effects`, { credentials: 'include' });
    if (!response.ok) throw new Error(`Could not load effects (${response.status}).`);
    const { classes } = await response.json();
    renderEffects(classes);
    setEffectLoadStatus('Effects loaded.');
  } catch (error) {
    setEffectLoadStatus(error.message || 'Could not load effects.', true);
    throw error;
  } finally {
    reloadEffectsButton.disabled = false;
  }
}

function renderEffects(classes) {
  const favoritesRow = document.getElementById('effectFavoritesRow');
  const searchInput = document.getElementById('effectSearchInput');
  const refreshBtn = document.getElementById('effectRefreshBtn');
  window.JahviEffectPicker.render(
    effectClassRow,
    favoritesRow,
    searchInput,
    refreshBtn,
    classes,
    () => pageState.selectedEffectClassIds,
    (classId) => {
      pageState.selectedEffectClassIds = [classId];
      document.querySelectorAll('.effect-class').forEach(effectBox => {
        effectBox.classList.toggle('selected', Number(effectBox.dataset.classId) === classId);
      });
      updateGenerateButtonState();
    },
  );
}


reloadEffectsButton.addEventListener('click', () => loadEffectCategories().catch(() => {}));

// ---------- Video upload, preview, replace, delete ----------
const clipBoxElement = document.querySelector('.clip-box');
const clipLabelElement = document.querySelector('.clip-label');
const clipCountLabel = document.getElementById('clipCount');
const clipInputElement = document.getElementById('videoInput');

const reelElement = document.getElementById('reel');
const videoPreviewCard = document.getElementById('videoPreviewCard');
const videoPreviewPlayer = document.getElementById('videoPreviewPlayer');
const replaceVideoBtn = document.getElementById('replaceVideoBtn');
const deleteVideoBtn = document.getElementById('deleteVideoBtn');

clipBoxElement.addEventListener('click', () => clipInputElement.click());
replaceVideoBtn.addEventListener('click', () => clipInputElement.click());

clipInputElement.addEventListener('change', () => {
  const selectedFile = clipInputElement.files[0];
  if (!selectedFile) return;

  pageState.videoFile = selectedFile;

  videoPreviewPlayer.src = URL.createObjectURL(selectedFile);
  videoPreviewPlayer.load();

  reelElement.style.display = 'none';
  videoPreviewCard.style.display = 'block';

  clipCountLabel.textContent = '1 / 1';

  updateGenerateButtonState();
});

deleteVideoBtn.addEventListener('click', () => {
  pageState.videoFile = null;

  clipInputElement.value = '';
  videoPreviewPlayer.pause();
  videoPreviewPlayer.removeAttribute('src');
  videoPreviewPlayer.load();

  videoPreviewCard.style.display = 'none';
  reelElement.style.display = 'flex';

  clipBoxElement.classList.remove('filled');
  clipBoxElement.textContent = '1';
  clipLabelElement.textContent = 'Empty';
  clipCountLabel.textContent = '0 / 1';

  updateGenerateButtonState();
});

// ---------- Optional custom audio upload, preview, replace, delete ----------
const audioSlot = document.getElementById('audioSlot');
const audioBox = document.getElementById('audioBox');
const audioInputElement = document.getElementById('audioInput');
const audioPreviewCard = document.getElementById('audioPreviewCard');
const audioPreviewPlayer = document.getElementById('audioPreviewPlayer');
const audioFileName = document.getElementById('audioFileName');
const replaceAudioBtn = document.getElementById('replaceAudioBtn');
const deleteAudioBtn = document.getElementById('deleteAudioBtn');
const musicVolumeInput = document.getElementById('musicVolume');
const musicVolumeValue = document.getElementById('musicVolumeValue');

musicVolumeInput.addEventListener('input', () => {
  pageState.musicVolume = Number(musicVolumeInput.value) / 100;
  musicVolumeValue.textContent = `${musicVolumeInput.value}%`;
});

audioBox.addEventListener('click', () => audioInputElement.click());
replaceAudioBtn.addEventListener('click', () => audioInputElement.click());

audioInputElement.addEventListener('change', () => {
  const selectedFile = audioInputElement.files[0];
  if (!selectedFile) return;

  pageState.audioFile = selectedFile;
  audioFileName.textContent = selectedFile.name;

  // Works whether the file is a real audio file or a video — the <audio>
  // tag just plays whatever audio track exists in it, ignoring any video.
  audioPreviewPlayer.src = URL.createObjectURL(selectedFile);
  audioPreviewPlayer.load();

  audioSlot.style.display = 'none';
  audioPreviewCard.style.display = 'block';
});

deleteAudioBtn.addEventListener('click', () => {
  pageState.audioFile = null;
  audioInputElement.value = '';

  audioPreviewPlayer.pause();
  audioPreviewPlayer.removeAttribute('src');
  audioPreviewPlayer.load();

  audioPreviewCard.style.display = 'none';
  audioSlot.style.display = 'block';
});

// ---------- Export ratio picker ----------
const ratioButtons = document.querySelectorAll('.ratio-btn');

ratioButtons.forEach(ratioButtonElement => {
  ratioButtonElement.addEventListener('click', () => selectExportRatio(ratioButtonElement.dataset.ratio));
});

function selectExportRatio(ratioValue){
  pageState.exportRatio = ratioValue;
  ratioButtons.forEach(ratioButtonElement => {
    ratioButtonElement.classList.toggle('active', ratioButtonElement.dataset.ratio === ratioValue);
  });
}

selectExportRatio('9:16');

// ---------- Generate CTA ----------
const generateButtons = [document.getElementById('generateBtn'), document.getElementById('generateBtnDesktop')];
const ctaStatusLabels = [document.getElementById('ctaStatus'), document.getElementById('ctaStatusDesktop')];

window.addEventListener('jahvi:timelinechange', updateGenerateButtonState);

function updateGenerateButtonState(){
  const hasVideo = !!pageState.videoFile;
  const hasSelectedEffect = pageState.selectedEffectClassIds.length > 0;
  const isReadyToGenerate = hasVideo && hasSelectedEffect && window.JahviTimeline?.hasTimestamps();

  let statusMessage = 'Add video, bars, and an effect';
  if (hasVideo && !window.JahviTimeline?.hasTimestamps()) statusMessage = 'Add at least one timeline bar';
  else if (hasVideo && !hasSelectedEffect) statusMessage = 'Choose an effect';
  else if (isReadyToGenerate) statusMessage = 'Ready to sync';

  generateButtons.forEach(buttonElement => buttonElement.disabled = !isReadyToGenerate);
  ctaStatusLabels.forEach(labelElement => labelElement.textContent = statusMessage);
}

// ============================================================
// Sync montage — main flow
// ============================================================
const previewIdleElement = document.getElementById('previewIdle');
const previewProcessingElement = document.getElementById('previewProcessing');
const previewVideoElement = document.getElementById('previewVideo');
const previewErrorElement = document.getElementById('previewError');
const circleFillElement = document.getElementById('circleFill');
const circleLabelElement = document.getElementById('circleLabel');
const processingSubElement = document.getElementById('processingSub');
const resultStatElement = document.getElementById('resultStat');
const analysisLogElement = document.getElementById('analysisLog');
const miniWaveformElement = document.getElementById('miniWaveform');

let headshotCount = null;
let beatCount = null;
let matchedCount = null;

async function validateBeatSyncRequest() {
  try {
    return await window.JahviAuth.validateProcessingFiles([pageState.videoFile, pageState.audioFile]);
  } catch (error) {
    let refreshResponse;
    try {
      refreshResponse = await fetch(`${API_BASE_URL}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      });
    } catch {
      throw new Error(`BeatSync could not reach the backend at ${API_BASE_URL}. Please check that port 8000 is available.`);
    }
    if (!refreshResponse.ok) {
      throw new Error(`BeatSync could not reach the signed-in session at ${API_BASE_URL}. Please reload the lab and sign in again.`);
    }
    try {
      return await window.JahviAuth.validateProcessingFiles([pageState.videoFile, pageState.audioFile]);
    } catch {
      throw new Error('BeatSync could not verify your account. Please reload the lab and try again.');
    }
  }
}

generateButtons.forEach(buttonElement => buttonElement.addEventListener('click', startSync));

async function startSync(){
  generateButtons.forEach(buttonElement => { buttonElement.disabled = true; buttonElement.textContent = 'Syncing…'; });
  ctaStatusLabels.forEach(labelElement => labelElement.textContent = 'Uploading your video…');
  resultStatElement.style.display = 'none';
  headshotCount = null;
  beatCount = null;
  matchedCount = null;

  try {
    const validationMessage = await validateBeatSyncRequest();
    if (validationMessage) throw new Error(validationMessage);
    showProcessingState();
    const formData = new FormData();
    formData.append('video', pageState.videoFile, pageState.videoFile.name);
    formData.append('headshot_timestamps', JSON.stringify(window.JahviTimeline.getTimestamps()));
    formData.append('effect_class_ids', JSON.stringify(pageState.selectedEffectClassIds));
    formData.append('ratio', pageState.exportRatio);
    formData.append('music_volume', String(pageState.musicVolume));
    if (pageState.audioFile) {
      formData.append('custom_audio', pageState.audioFile, pageState.audioFile.name);
    }

    const response = await fetch(`${API_BASE_URL}/api/beatsync`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
    });

    if (!response.ok) {
      const responseText = await response.text();
      let errorData = {};
      try {
        errorData = responseText ? JSON.parse(responseText) : {};
      } catch {
        errorData = { detail: responseText };
      }
      throw new Error(errorData.detail || `Beat sync failed (${response.status}).`);
    }

    if ((response.headers.get('content-type') || '').includes('application/json')) {
      const queued = await response.json();
      if (queued.queued) {
        showQueuedState(queued);
        toggleWaveform(false);
        await waitForQueuedJob(queued.job_id);
        return;
      }
    }

    if (!response.body) throw new Error('The server returned no progress stream.');
    const reader = response.body.getReader();
    const textDecoder = new TextDecoder();

    let filename = null;
    let leftoverText = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      leftoverText += textDecoder.decode(value, { stream: true });

      const lines = leftoverText.split('\n');
      leftoverText = lines.pop();

      for (const line of lines) {
        if (!line) continue;
        const event = parseStreamEvent(line);
        if (!event) continue;
        if (event.event === 'error') throw new Error(event.message || 'Sync failed.');
        if (event.event === 'progress') {
          updateCircularProgress(event.percent || 0);
          addLogLine(event.message || 'Processing');
          ctaStatusLabels.forEach(labelElement => {
            labelElement.textContent = event.message || `${event.percent || 0}%`;
          });
          toggleWaveform((event.message || '').toLowerCase().includes('beat'));
        }
        if (event.event === 'completed') filename = event.filename;
      }
    }

    leftoverText += textDecoder.decode();
    const finalLine = leftoverText.trim();
    const finalEvent = parseStreamEvent(finalLine);
    if (finalEvent?.event === 'error') throw new Error(finalEvent.message || 'Sync failed.');
    if (finalEvent?.event === 'completed') filename = finalEvent.filename;

    if (!filename) {
      throw new Error('Sync finished but no video was returned.');
    }

    updateCircularProgress(95);
    toggleWaveform(false);
    await showFinishedVideo(filename);

  } catch (err) {
    console.error('Sync failed:', err);
    toggleWaveform(false);
    showGenerateError(err.message || 'Something went wrong. Please try again.');
  }
}

function addLogLine(text){
  const lineElement = document.createElement('div');
  lineElement.className = 'analysis-log-line latest';
  lineElement.textContent = text;

  const previousLatest = analysisLogElement.querySelector('.latest');
  if (previousLatest) previousLatest.classList.remove('latest');

  analysisLogElement.appendChild(lineElement);
  analysisLogElement.scrollTop = analysisLogElement.scrollHeight;
}

function toggleWaveform(shouldAnimate){
  miniWaveformElement.classList.toggle('active', shouldAnimate);
}

function updateCircularProgress(progressPercent){
  const clampedPercent = Math.max(0, Math.min(100, progressPercent));
  circleFillElement.style.setProperty('--progress-percent', clampedPercent);
  circleLabelElement.textContent = `${Math.round(clampedPercent)}%`;
}

function showProcessingState(){
  previewIdleElement.style.display = 'none';
  previewVideoElement.style.display = 'none';
  previewProcessingElement.style.display = 'flex';
  previewErrorElement.style.display = 'none';
  processingSubElement.textContent = 'Applying selected effects and syncing to detected beats';
  analysisLogElement.innerHTML = '';
  updateCircularProgress(0);
}

function showQueuedState(queueData){
  showProcessingState();
  const position = Number(queueData.queue_position ?? 0) + 1;
  processingSubElement.textContent = `Queued • position ${position}`;
  ctaStatusLabels.forEach(label => label.textContent = `Waiting in queue • position ${position}`);
}

async function waitForQueuedJob(jobId){
  while (true) {
    try {
    let response = await fetch(`${API_BASE_URL}/api/videos`, { credentials: 'include' });
    if (response.status === 401) {
      try {
        const refreshResponse = await fetch(`${API_BASE_URL}/auth/refresh`, {
          method: 'POST',
          credentials: 'include',
        });
        if (refreshResponse.ok) {
          const session = await refreshResponse.json();
          localStorage.setItem('jahvi_user', JSON.stringify(session.user));
          response = await fetch(`${API_BASE_URL}/api/videos`, { credentials: 'include' });
        } else {
          window.JahviAuth?.clearSession();
          window.location.replace('login.html');
          return;
        }
      } catch {
        window.JahviAuth?.clearSession();
        window.location.replace('login.html');
        return;
      }
    }
    if (!response.ok) throw new Error(`Could not check queue status (${response.status})`);
    const data = await response.json();
    const job = data.videos.find(item => item.id === jobId);
    if (!job) throw new Error('Queued job could not be found.');
    if (job.status === 'failed') throw new Error(job.error || 'Queued sync failed.');
    if (job.status === 'completed' && job.video_url) {
      await showFinishedVideo(job.video_url);
      return;
    }
    const message = job.status === 'processing' ? 'Processing from queue…' : `Waiting in queue • position ${(job.queue_position ?? 0) + 1}`;
    ctaStatusLabels.forEach(label => label.textContent = message);
    } catch (error) {
      if (error.message === 'Queued job could not be found.' || error.message.includes('Queued sync failed')) {
        throw error;
      }
      ctaStatusLabels.forEach(label => label.textContent = 'Connection interrupted • checking again…');
    }
    await new Promise(resolve => setTimeout(resolve, 5000));
  }
}

function parseStreamEvent(line) {
  if (!line) return null;
  const payload = line.startsWith('data: ') ? line.slice(6) : line;
  try { return JSON.parse(payload); } catch { return null; }
}

async function showFinishedVideo(filename){
  const videoUrl = filename.startsWith('http') ? filename : filename.startsWith('/') ? `${API_BASE_URL}${filename}` : `${API_BASE_URL}/api/video/${encodeURIComponent(filename)}`;
  let response = await fetch(videoUrl, { credentials: 'include' });
  if (response.status === 401) {
    try {
      const refreshResponse = await fetch(`${API_BASE_URL}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      });
      if (refreshResponse.ok) {
        const session = await refreshResponse.json();
        localStorage.setItem('jahvi_user', JSON.stringify(session.user));
        response = await fetch(videoUrl, { credentials: 'include' });
      } else {
        window.JahviAuth?.clearSession();
        window.location.replace('login.html');
        return;
      }
    } catch {
      window.JahviAuth?.clearSession();
      window.location.replace('login.html');
      return;
    }
  }
  if (!response.ok) throw new Error(`Could not fetch finished video (${response.status})`);

  const videoBlob = await response.blob();
  const blobUrl = URL.createObjectURL(videoBlob);

  previewProcessingElement.style.display = 'none';
  previewIdleElement.style.display = 'none';
  previewVideoElement.style.display = 'block';
  previewVideoElement.src = blobUrl;

  previewVideoElement.addEventListener('loadedmetadata', () => {
    const durationSeconds = Math.round(previewVideoElement.duration);
    const matchedText = matchedCount !== null ? `${matchedCount} headshots matched to beats` : 'Headshots matched to beats';
    resultStatElement.textContent = `${matchedText} • ${durationSeconds}s final video`;
    resultStatElement.style.display = 'block';
  }, { once: true });
  previewVideoElement.load();

  generateButtons.forEach(buttonElement => { buttonElement.disabled = false; buttonElement.textContent = 'Sync montage'; });
  ctaStatusLabels.forEach(labelElement => labelElement.textContent = 'Done — sync again anytime');

  window.JahviAuth.loadUserAndCredits();
}

function showGenerateError(message){
  previewProcessingElement.style.display = 'none';
  previewVideoElement.style.display = 'none';
  previewIdleElement.style.display = 'flex';
  previewErrorElement.style.display = 'block';
  previewErrorElement.textContent = message;

  generateButtons.forEach(buttonElement => { buttonElement.disabled = false; buttonElement.textContent = 'Sync montage'; });
  updateGenerateButtonState();
}

async function restoreLatestQueueJob(){
  let response = await fetch(`${API_BASE_URL}/api/videos`, { credentials: 'include' });
  if (response.status === 401) {
    try {
      const refreshResponse = await fetch(`${API_BASE_URL}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      });
      if (refreshResponse.ok) {
        const session = await refreshResponse.json();
        localStorage.setItem('jahvi_user', JSON.stringify(session.user));
        response = await fetch(`${API_BASE_URL}/api/videos`, { credentials: 'include' });
      } else {
        window.JahviAuth?.clearSession();
        window.location.replace('login.html');
        return;
      }
    } catch {
      window.JahviAuth?.clearSession();
      window.location.replace('login.html');
      return;
    }
  }
  if (!response.ok) return;
  const data = await response.json();
  const job = data.videos.find(item => item.edit_type === 'beatsync');
  if (!job) return;
  if (job.status === 'completed' && job.video_url) {
    await showFinishedVideo(job.video_url);
    return;
  }
  if (job.status === 'queued' || job.status === 'processing') {
    showQueuedState(job);
    await waitForQueuedJob(job.id);
  }
}

// ---------- Init ----------
window.JahviAuth.initialize()
  .then(async user => {
    if (!user) {
      window.location.replace('login.html');
      return;
    }
    await loadEffectCategories();
    await restoreLatestQueueJob();
  })
  .catch(error => console.error('BeatSync initialization failed:', error));

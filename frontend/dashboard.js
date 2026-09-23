// ============================================================
// Jahvi Dashboard — plain JS, no framework.
// Everything the page needs to know lives in `dashboardState`.
// ============================================================

const API_BASE_URL = window.JAHVI_API_BASE_URL || '';

const dashboardState = {
  videoFile: null,
  selectedEffectClassIds: [],
  exportRatio: '9:16',
  gapMode: 'normal',
  gapValue: 3,
};

// ---------- Icons (small inline SVGs, kept in one place) ----------
const iconLibrary = {
  beatsync:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h3l2-7 4 14 3-11 2 4h4"/></svg>',
  // style-picker icons (one per style name used in styleOptions below)
  rage:      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2c1 3-1 4-1 6 0 1.5 1 2 2 2 1.5 0 2-1.5 1.5-3 3 2 4 5 4 7a6.5 6.5 0 0 1-13 0c0-1 .3-2 1-3 .3 1 1 1.5 1.5 1.5A8 8 0 0 1 12 2z"/></svg>',
  snap:      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 3 14h7l-1 8 10-14h-7z"/></svg>',
  flow:      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.7 0-3.1.6-4.5 2-1.4-1.4-2.8-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7z"/></svg>',
  cinematic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 9h18M3 15h18"/></svg>',
  phonk:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14v-3a8 8 0 0 1 16 0v3"/><path d="M3 14h2a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-3a2 2 0 0 1 1-2Z"/><path d="M21 14h-2a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-3a2 2 0 0 0-1-2Z"/></svg>',
};

document.getElementById('bslCard').querySelector('.bsl-icon').innerHTML = iconLibrary.beatsync;

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
    () => dashboardState.selectedEffectClassIds,
    (classId, box) => {
      const selected = dashboardState.selectedEffectClassIds;
      const index = selected.indexOf(classId);
      if (index >= 0) selected.splice(index, 1); else selected.push(classId);
      box.classList.toggle('selected', index < 0);
      updateGenerateButtonState();
    },
  );
}

reloadEffectsButton.addEventListener('click', () => loadEffectCategories().catch(() => {}));

// ---------- Export ratio picker ----------
const ratioButtons = document.querySelectorAll('.ratio-btn');
const gapModeButtons = document.querySelectorAll('.mode-btn');
const gapCustomRow = document.getElementById('gapCustomRow');
const customGapInput = document.getElementById('customGapInput');

ratioButtons.forEach(ratioButtonElement => {
  ratioButtonElement.addEventListener('click', () => selectExportRatio(ratioButtonElement.dataset.ratio));
});

function selectExportRatio(ratioValue){
  dashboardState.exportRatio = ratioValue;
  ratioButtons.forEach(ratioButtonElement => {
    ratioButtonElement.classList.toggle('active', ratioButtonElement.dataset.ratio === ratioValue);
  });
}

function selectGapMode(modeName) {
  dashboardState.gapMode = modeName;
  const showCustom = modeName === 'maximum' || modeName === 'exact';
  gapCustomRow.style.display = showCustom ? 'flex' : 'none';
  gapModeButtons.forEach(button => button.classList.toggle('active', button.dataset.gapMode === modeName));
}

customGapInput.addEventListener('input', () => {
  const value = Number(customGapInput.value);
  if (Number.isFinite(value) && value > 0) dashboardState.gapValue = value;
});

gapModeButtons.forEach(button => {
  button.addEventListener('click', () => selectGapMode(button.dataset.gapMode));
});

selectExportRatio('9:16');
selectGapMode('normal');

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

  dashboardState.videoFile = selectedFile;

  // Show the file in a real playable preview, swapping out the empty box.
  videoPreviewPlayer.src = URL.createObjectURL(selectedFile);
  videoPreviewPlayer.load();

  reelElement.style.display = 'none';
  videoPreviewCard.style.display = 'block';

  clipCountLabel.textContent = '1 / 1';

  updateGenerateButtonState();
});

deleteVideoBtn.addEventListener('click', () => {
  dashboardState.videoFile = null;

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

// ---------- Generate CTA (kept in sync across mobile bar + desktop sidebar) ----------
const generateButtons = [document.getElementById('generateBtn'), document.getElementById('generateBtnDesktop')];
const ctaStatusLabels = [document.getElementById('ctaStatus'), document.getElementById('ctaStatusDesktop')];

window.addEventListener('jahvi:timelinechange', updateGenerateButtonState);

function updateGenerateButtonState(){
  const hasVideo = !!dashboardState.videoFile;
  const hasSelectedEffect = dashboardState.selectedEffectClassIds.length > 0;
  const isReadyToGenerate = hasVideo && hasSelectedEffect && window.JahviTimeline?.hasTimestamps();

  let statusMessage = 'Add video, bars, and an effect';
  if (hasVideo && !window.JahviTimeline?.hasTimestamps()) statusMessage = 'Add at least one timeline bar';
  else if (hasVideo && !hasSelectedEffect) statusMessage = 'Choose an effect';
  else if (isReadyToGenerate) statusMessage = 'Ready to generate';

  generateButtons.forEach(buttonElement => buttonElement.disabled = !isReadyToGenerate);
  ctaStatusLabels.forEach(labelElement => labelElement.textContent = statusMessage);
}

// ============================================================
// Generate montage — this is the main flow, fires only on button click.
//
// How it works, in plain steps:
//   1. We POST the video + style + ratio to /api/generate.
//   2. That request stays open and streams back plain text lines as the
//      backend works through each stage — this is how we get live progress
//      without having to poll a separate endpoint every few seconds.
//   3. The last two lines of that stream give us the job's id and a
//      signature proving the video belongs to us.
//   4. Once we have both, we fetch the actual finished video from
//      /video/{jobId}?sig=... and show it in the player.
// ============================================================
const previewIdleElement = document.getElementById('previewIdle');
const previewProcessingElement = document.getElementById('previewProcessing');
const previewVideoElement = document.getElementById('previewVideo');
const previewErrorElement = document.getElementById('previewError');
const circleFillElement = document.getElementById('circleFill');
const circleLabelElement = document.getElementById('circleLabel');
const processingSubElement = document.getElementById('processingSub');

generateButtons.forEach(buttonElement => buttonElement.addEventListener('click', startGenerate));

async function startGenerate(){
  generateButtons.forEach(buttonElement => { buttonElement.disabled = true; buttonElement.textContent = 'Generating…'; });
  ctaStatusLabels.forEach(labelElement => labelElement.textContent = 'Uploading your video…');

  try {
    const validationMessage = await window.JahviAuth.validateProcessingFiles([dashboardState.videoFile]);
    if (validationMessage) throw new Error(validationMessage);
    showProcessingState();
    const formData = new FormData();
    formData.append('video', dashboardState.videoFile, dashboardState.videoFile.name);
    formData.append('headshot_timestamps', JSON.stringify(window.JahviTimeline.getTimestamps()));
    formData.append('effect_class_ids', JSON.stringify(dashboardState.selectedEffectClassIds));
    formData.append('ratio', dashboardState.exportRatio);
    formData.append('gap_mode', dashboardState.gapMode);
    formData.append('gap_value', String(dashboardState.gapValue));

    const response = await fetch(`${API_BASE_URL}/api/generate`, {
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
      throw new Error(errorData.detail || `Generate failed (${response.status}).`);
    }

    if ((response.headers.get('content-type') || '').includes('application/json')) {
      const queued = await response.json();
      if (queued.queued) {
        showQueuedState(queued);
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
        if (event.event === 'error') throw new Error(event.message || 'Render failed.');
        if (event.event === 'progress') {
          updateCircularProgress(event.percent ?? 0);
          ctaStatusLabels.forEach(label => label.textContent = event.message || `${event.percent}%`);
        }
        if (event.event === 'completed') filename = event.filename;
      }
    }

    leftoverText += textDecoder.decode();
    const finalEvent = parseStreamEvent(leftoverText.trim());
    if (finalEvent?.event === 'error') throw new Error(finalEvent.message || 'Render failed.');
    if (finalEvent?.event === 'completed') filename = finalEvent.filename;
    if (!filename) throw new Error('Render finished but no video was returned.');

    updateCircularProgress(95);
    await showFinishedVideo(filename);

  } catch (err) {
    console.error('Generate failed:', err);
    showGenerateError(err.message || 'Something went wrong. Please try again.');
  }
}

function parseStreamEvent(line) {
  if (!line) return null;
  const payload = line.startsWith('data: ') ? line.slice(6) : line;
  try { return JSON.parse(payload); } catch { return null; }
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
  processingSubElement.textContent = 'Applying your selected effects to the timeline';
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
      const response = await fetch(`${API_BASE_URL}/api/videos`, { credentials: 'include' });
      if (!response.ok) throw new Error(`Could not check queue status (${response.status})`);
      const data = await response.json();
      const job = data.videos.find(item => item.id === jobId);
      if (!job) throw new Error('Queued job could not be found.');
      if (job.status === 'failed') throw new Error(job.error || 'Queued render failed.');
      if (job.status === 'completed' && job.video_url) {
        await showFinishedVideo(job.video_url);
        return;
      }
      const message = job.status === 'processing' ? 'Processing from queue…' : `Waiting in queue • position ${(job.queue_position ?? 0) + 1}`;
      ctaStatusLabels.forEach(label => label.textContent = message);
    } catch (error) {
      if (error.message === 'Queued job could not be found.' || error.message.includes('Queued render failed')) {
        throw error;
      }
      ctaStatusLabels.forEach(label => label.textContent = 'Connection interrupted • checking again…');
    }
    await new Promise(resolve => setTimeout(resolve, 5000));
  }
}

async function showFinishedVideo(filename){
  const videoUrl = filename.startsWith('http') ? filename : filename.startsWith('/') ? `${API_BASE_URL}${filename}` : `${API_BASE_URL}/api/video/${encodeURIComponent(filename)}`;
  const response = await fetch(videoUrl, { credentials: 'include' });
  if (!response.ok) throw new Error(`Could not fetch finished video (${response.status})`);

  const videoBlob = await response.blob();
  if (!videoBlob.size || !videoBlob.type.startsWith('video/')) {
    throw new Error('The server returned an empty or invalid video file.');
  }
  const blobUrl = URL.createObjectURL(videoBlob);

  previewProcessingElement.style.display = 'none';
  previewIdleElement.style.display = 'none';
  previewVideoElement.style.display = 'block';
  previewVideoElement.src = blobUrl;
  previewVideoElement.addEventListener('loadedmetadata', () => {
    const durationSeconds = Math.round(previewVideoElement.duration);
    previewVideoElement.setAttribute('aria-label', `Generated montage, ${durationSeconds} seconds`);
  }, { once: true });
  previewVideoElement.load();

  generateButtons.forEach(buttonElement => { buttonElement.disabled = false; buttonElement.textContent = 'Generate montage'; });
  ctaStatusLabels.forEach(labelElement => labelElement.textContent = 'Done — generate again anytime');

  // The backend deducts the credit server-side, so refresh the real number
  // instead of guessing it on the frontend.
  window.JahviAuth.loadUserAndCredits();
}

function showGenerateError(message){
  previewProcessingElement.style.display = 'none';
  previewVideoElement.style.display = 'none';
  previewIdleElement.style.display = 'flex';
  previewErrorElement.style.display = 'block';
  previewErrorElement.textContent = message;

  generateButtons.forEach(buttonElement => { buttonElement.disabled = false; buttonElement.textContent = 'Generate montage'; });
  updateGenerateButtonState();
}

async function restoreLatestQueueJob(){
  const response = await fetch(`${API_BASE_URL}/api/videos`, { credentials: 'include' });
  if (!response.ok) return;
  const data = await response.json();
  const job = data.videos.find(item => item.edit_type === 'dashboard');
  if (!job) return;
  if (job.status === 'queued' || job.status === 'processing') {
    showQueuedState(job);
    await waitForQueuedJob(job.id);
    return;
  }
  if (job.status === 'completed' && job.video_url) {
    showProcessingState();
    await waitForQueuedJob(job.id);
  }
}

// ---------- Beat Sync Lab ----------
document.getElementById('bslCard').addEventListener('click', () => {
  window.location.href = 'beat_sync_lab.html';
});

// ---------- Init ----------
window.JahviAuth.initialize().then(async user => {
  if (!user) {
    window.location.replace('login.html');
    return;
  }
  await loadEffectCategories();
  await restoreLatestQueueJob();
}).catch(error => {
  console.error('Could not load dashboard effects:', error);
});
updateGenerateButtonState();

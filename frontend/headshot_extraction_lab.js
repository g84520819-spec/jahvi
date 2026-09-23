// ============================================================
// Jahvi Headshot Extraction Lab — plain JS, no framework.
// Same pattern as dashboard.js, just different fields: no style picker,
// instead we send a "max gap" number and export ratio.
// ============================================================

const API_BASE_URL = window.JAHVI_API_BASE_URL || '';

const pageState = {
  videoFile: null,      // the one video the user picked, or null if none yet
  maxGapSeconds: 5,      // default max gap, matches the "5s" chip being pre-selected
  exportRatio: '9:16',   // default export ratio
  patch: false,          // patchless mode is the default
};

const authenticatedHeaders = () => ({});

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
  window.JahviTimeline?.clear();

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
  window.JahviTimeline?.clear();

  updateGenerateButtonState();
});

// ---------- Max gap picker (chips + custom input) ----------
const gapButtons = document.querySelectorAll('.gap-btn');
const customGapInput = document.getElementById('customGapInput');

gapButtons.forEach(button => {
  button.addEventListener('click', () => {
    pageState.maxGapSeconds = Number(button.dataset.gap);

    gapButtons.forEach(b => b.classList.remove('active'));
    button.classList.add('active');

    // A chip was picked, so clear any custom value the user typed
    customGapInput.value = '';
  });
});

// 5s is selected by default
document.querySelector('[data-gap="5"]').classList.add('active');

customGapInput.addEventListener('input', () => {
  const typedValue = Number(customGapInput.value);
  if (typedValue > 0) {
    pageState.maxGapSeconds = typedValue;
    gapButtons.forEach(b => b.classList.remove('active')); // custom value overrides the chips
  }
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

document.querySelectorAll('.mode-btn').forEach(button => {
  button.addEventListener('click', () => {
    pageState.patch = button.dataset.patch === 'true';
    document.querySelectorAll('.mode-btn').forEach(item => item.classList.toggle('active', item === button));
  });
});

// ---------- Generate CTA (kept in sync across mobile bar + desktop sidebar) ----------
const generateButtons = [document.getElementById('generateBtn'), document.getElementById('generateBtnDesktop')];
const ctaStatusLabels = [document.getElementById('ctaStatus'), document.getElementById('ctaStatusDesktop')];

function updateGenerateButtonState(){
  const hasVideo = !!pageState.videoFile;
  const hasTimestamps = window.JahviTimeline?.hasTimestamps() === true;

  generateButtons.forEach(buttonElement => buttonElement.disabled = !(hasVideo && hasTimestamps));
  ctaStatusLabels.forEach(labelElement => {
    if (!hasVideo) labelElement.textContent = 'Add a video to continue';
    else if (!hasTimestamps) labelElement.textContent = 'Add at least one timeline bar';
    else labelElement.textContent = 'Ready to extract';
  });
}

// ============================================================
// Extract headshots — the main flow, fires only on button click.
// Same SSE-streaming pattern as dashboard.js's startGenerate().
// ============================================================
const previewIdleElement = document.getElementById('previewIdle');
const previewProcessingElement = document.getElementById('previewProcessing');
const previewVideoElement = document.getElementById('previewVideo');
const previewErrorElement = document.getElementById('previewError');
const circleFillElement = document.getElementById('circleFill');
const circleLabelElement = document.getElementById('circleLabel');
const processingSubElement = document.getElementById('processingSub');
const resultStatElement = document.getElementById('resultStat');

window.addEventListener('jahvi:timelinechange', updateGenerateButtonState);

let headshotsFoundCount = null; // filled in as we read the progress stream

generateButtons.forEach(buttonElement => buttonElement.addEventListener('click', startExtraction));

async function startExtraction(){
  generateButtons.forEach(buttonElement => { buttonElement.disabled = true; buttonElement.textContent = 'Extracting…'; });
  ctaStatusLabels.forEach(labelElement => labelElement.textContent = 'Uploading your video…');
  resultStatElement.style.display = 'none';
  headshotsFoundCount = null;

  try {
    const validationMessage = await window.JahviAuth.validateProcessingFiles([pageState.videoFile]);
    if (validationMessage) throw new Error(validationMessage);
    showProcessingState();
    const formData = new FormData();
    formData.append('video', pageState.videoFile, pageState.videoFile.name);
    formData.append('headshot_timestamps', JSON.stringify(window.JahviTimeline.getTimestamps()));
    formData.append('max_gap', String(pageState.maxGapSeconds));
    formData.append('ratio', pageState.exportRatio);
    formData.append('patch', String(pageState.patch));

    const response = await fetch(`${API_BASE_URL}/api/extract`, {
      method: 'POST',
      headers: authenticatedHeaders(),
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
      if (response.status === 401) {
        window.JahviAuth?.clearSession();
        window.location.replace('login.html');
        return;
      }
      throw new Error(errorData.detail || `Extraction failed (${response.status}).`);
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
        if (event.event === 'error') throw new Error(event.message || 'Extraction failed.');
        if (event.event === 'progress') {
          updateCircularProgress(event.percent);
          ctaStatusLabels.forEach(labelElement => labelElement.textContent = event.message || `${event.percent}%`);
        }
        if (event.event === 'completed') filename = event.filename;
      }
    }

    leftoverText += textDecoder.decode();
    const finalEvent = parseStreamEvent(leftoverText.trim());
    if (finalEvent?.event === 'error') throw new Error(finalEvent.message || 'Extraction failed.');
    if (finalEvent?.event === 'completed') filename = finalEvent.filename;

    if (!filename) {
      throw new Error('Extraction finished but no video was returned.');
    }

    updateCircularProgress(95);
    await showFinishedVideo(filename);

  } catch (err) {
    console.error('Extraction failed:', err);
    const message = err instanceof TypeError && err.message === 'Failed to fetch'
      ? `Could not reach the extraction server at ${API_BASE_URL}/api/extract. Check that port 8000 is public.`
      : (err.message || 'Something went wrong. Please try again.');
    showGenerateError(message);
  }
}

function parseStreamEvent(line){
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
  processingSubElement.textContent = `Max gap ${pageState.maxGapSeconds}s • this usually takes under a minute`;
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
      const response = await fetch(`${API_BASE_URL}/api/videos`, { credentials: 'include', headers: authenticatedHeaders() });
      if (!response.ok) throw new Error(`Could not check queue status (${response.status})`);
      const data = await response.json();
      const job = data.videos.find(item => item.id === jobId);
      if (!job) throw new Error('Queued job could not be found.');
      if (job.status === 'failed') throw new Error(job.error || 'Queued extraction failed.');
      if (job.status === 'completed' && job.video_url) {
        await showFinishedVideo(job.video_url);
        return;
      }
      const message = job.status === 'processing' ? 'Processing from queue…' : `Waiting in queue • position ${(job.queue_position ?? 0) + 1}`;
      ctaStatusLabels.forEach(label => label.textContent = message);
    } catch (error) {
      if (error.message === 'Queued job could not be found.' || error.message.includes('Queued extraction failed')) {
        throw error;
      }
      ctaStatusLabels.forEach(label => label.textContent = 'Connection interrupted • checking again…');
    }
    await new Promise(resolve => setTimeout(resolve, 5000));
  }
}

async function showFinishedVideo(filename){
  const videoUrl = filename.startsWith('http') ? filename : filename.startsWith('/') ? `${API_BASE_URL}${filename}` : `${API_BASE_URL}/api/video/${encodeURIComponent(filename)}`;
  const response = await fetch(videoUrl, { credentials: 'include', headers: authenticatedHeaders() });
  if (!response.ok) throw new Error(`Could not fetch finished video (${response.status})`);

  const videoBlob = await response.blob();
  const blobUrl = URL.createObjectURL(videoBlob);

  previewProcessingElement.style.display = 'none';
  previewIdleElement.style.display = 'none';
  previewVideoElement.style.display = 'block';
  previewVideoElement.src = blobUrl;

  // Once the video's real length is known, show the small payoff stat —
  // headshot count (from the stream) + final duration (from the video itself).
  previewVideoElement.addEventListener('loadedmetadata', () => {
    const durationSeconds = Math.round(previewVideoElement.duration);
    const headshotText = headshotsFoundCount !== null ? `${headshotsFoundCount} headshots found` : 'Headshots found';
    resultStatElement.textContent = `${headshotText} • ${durationSeconds}s final video`;
    resultStatElement.style.display = 'block';
  }, { once: true });
  previewVideoElement.load();

  generateButtons.forEach(buttonElement => { buttonElement.disabled = false; buttonElement.textContent = 'Extract headshots'; });
  ctaStatusLabels.forEach(labelElement => labelElement.textContent = 'Done — extract again anytime');

  window.JahviAuth.loadUserAndCredits();
}

function showGenerateError(message){
  previewProcessingElement.style.display = 'none';
  previewVideoElement.style.display = 'none';
  previewIdleElement.style.display = 'flex';
  previewErrorElement.style.display = 'block';
  previewErrorElement.textContent = message;

  generateButtons.forEach(buttonElement => { buttonElement.disabled = false; buttonElement.textContent = 'Extract headshots'; });
  updateGenerateButtonState();
}

async function restoreLatestQueueJob(){
  const response = await fetch(`${API_BASE_URL}/api/videos`, { credentials: 'include', headers: authenticatedHeaders() });
  if (!response.ok) return;
  const data = await response.json();
  const job = data.videos.find(item => item.edit_type === 'extraction');
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

// ---------- Init ----------
window.JahviAuth.initialize().then(async user => {
  if (!user) {
    window.location.replace('login.html');
    return;
  }
  await restoreLatestQueueJob();
});
updateGenerateButtonState();

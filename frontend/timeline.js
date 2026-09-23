(() => {
  const state = { timestamps: [], stepSizes: [], duration: 0, activeIndex: -1, dragging: false };
  const track = document.getElementById('timelineTrack');
  const emptyLabel = document.getElementById('timelineEmpty');
  const addButton = document.getElementById('addBarBtn');
  const currentTimeLabel = document.getElementById('timelineCurrentTime');
  const video = document.getElementById('videoPreviewPlayer');
  const list = document.getElementById('timestampList');

  if (!track || !video) return;

  const emitChange = () => window.dispatchEvent(new CustomEvent('jahvi:timelinechange'));
  const clampTime = value => Math.max(0, Math.min(state.duration || 0, value));
  const formatTime = value => {
    const minutes = Math.floor(value / 60);
    const seconds = value - minutes * 60;
    return `${String(minutes).padStart(2, '0')}:${seconds.toFixed(2).padStart(5, '0')}`;
  };

  function getNeighborBoundaries(index) {
    return {
      previous: state.timestamps[index - 1],
      next: state.timestamps[index + 1],
    };
  }

  function setTimestampAtIndex(index, nextValue, { seek = true, rerender = true } = {}) {
    const parsedValue = Number(nextValue);
    if (!Number.isFinite(parsedValue)) return false;

    const candidate = clampTime(parsedValue);
    const { previous, next } = getNeighborBoundaries(index);

    if ((previous !== undefined && candidate <= previous)
      || (next !== undefined && candidate >= next)) {
      window.alert('This timestamp cannot pass the neighboring bar.');
      return false;
    }

    state.timestamps[index] = candidate;
    state.activeIndex = index;
    if (rerender) render();
    if (seek) seekTo(candidate);
    emitChange();
    return true;
  }

  function render() {
    track.querySelectorAll('.timeline-bar').forEach(bar => bar.remove());
    if (emptyLabel) emptyLabel.style.display = state.timestamps.length ? 'none' : 'block';

    state.timestamps.forEach((timestamp, index) => {
      const bar = document.createElement('button');
      bar.type = 'button';
      bar.className = `timeline-bar${index === state.activeIndex ? ' active' : ''}`;
      bar.style.left = `${state.duration ? (timestamp / state.duration) * 100 : 0}%`;
      bar.title = `${timestamp.toFixed(2)}s`;
      bar.setAttribute('aria-label', `Headshot at ${timestamp.toFixed(2)} seconds`);
      bar.addEventListener('pointerdown', event => beginDrag(event, index));
      bar.addEventListener('click', () => {
        if (!bar.dataset.dragged) advanceBar(index);
        delete bar.dataset.dragged;
      });
      track.appendChild(bar);
    });

    if (list) {
      list.replaceChildren();
      state.timestamps.forEach((timestamp, index) => {
        const item = document.createElement('li');
        item.className = `timestamp-item${index === state.activeIndex ? ' active' : ''}`;
        item.innerHTML = `<span class="timestamp-label">Bar ${index + 1}</span><button type="button" class="timestamp-step timestamp-step-back" aria-label="Move headshot backward">-</button><button type="button" class="timestamp-jump">${formatTime(timestamp)}</button><label class="timestamp-field"><span>Time (s)</span><input type="number" class="timestamp-input" min="0" step="0.01" value="${timestamp.toFixed(2)}" aria-label="Headshot time in seconds" /></label><select class="timestamp-step-select" aria-label="Timestamp adjustment"><option value="0.5">0.5s</option><option value="0.3">0.3s</option><option value="0.2">0.2s</option><option value="0.1">0.1s</option><option value="0.4">0.4s</option><option value="0.01">0.01s</option><option value="0.02">0.02s</option></select><button type="button" class="timestamp-step timestamp-step-forward" aria-label="Move headshot forward">+</button><button type="button" class="timestamp-delete" aria-label="Delete headshot at ${formatTime(timestamp)}">×</button>`;
        const stepSelect = item.querySelector('.timestamp-step-select');
        const timestampInput = item.querySelector('.timestamp-input');
        stepSelect.value = state.stepSizes[index] || '0.4';
        stepSelect.addEventListener('change', () => { state.stepSizes[index] = stepSelect.value; });
        item.querySelector('.timestamp-jump').addEventListener('click', () => seekTo(timestamp));
        timestampInput.addEventListener('change', event => {
          const nextValue = Number(event.target.value);
          if (!Number.isFinite(nextValue)) {
            event.target.value = timestamp.toFixed(2);
            return;
          }
          setTimestampAtIndex(index, nextValue, { seek: true, rerender: true });
        });
        item.querySelector('.timestamp-step-back').addEventListener('click', event => {
          event.stopPropagation();
          setTimestampAtIndex(index, timestamp - Number(item.querySelector('.timestamp-step-select').value), { seek: true, rerender: true });
        });
        item.querySelector('.timestamp-step-forward').addEventListener('click', event => {
          event.stopPropagation();
          setTimestampAtIndex(index, timestamp + Number(item.querySelector('.timestamp-step-select').value), { seek: true, rerender: true });
        });
        item.querySelector('.timestamp-delete').addEventListener('click', () => {
          state.timestamps.splice(index, 1);
          state.stepSizes.splice(index, 1);
          state.activeIndex = -1;
          render();
          emitChange();
        });
        list.appendChild(item);
      });
    }
  }

  function seekTo(timestamp) {
    video.currentTime = clampTime(timestamp);
    video.pause();
    updateActive(video.currentTime);
  }

  function advanceBar(index) {
    setTimestampAtIndex(index, state.timestamps[index] + 0.4, { seek: true, rerender: true });
  }

  function adjustTimestamp(index, delta) {
    setTimestampAtIndex(index, state.timestamps[index] + delta, { seek: true, rerender: true });
  }

  function updateActive(currentTime) {
    currentTimeLabel.textContent = `${currentTime.toFixed(2)}s`;
    if (state.dragging) return;
    const nextIndex = state.timestamps.findIndex(timestamp => Math.abs(timestamp - currentTime) <= 0.12);
    if (nextIndex !== state.activeIndex) {
      state.activeIndex = nextIndex;
      render();
    }
  }

  function beginDrag(event, index) {
    event.preventDefault();
    const bar = event.currentTarget;
    const timestampItem = list?.children[index];
    let moved = false;
    let freeDrag = false;
    let pendingClientX = event.clientX;
    let animationFrameId = 0;
    let pendingSeekTime = null;
    let holdTimerId = 0;
    video.pause();
    bar.setPointerCapture(event.pointerId);

    // Only ever have one seek in flight. If a new position comes in while
    // the video is still decoding to the last target, don't fire another
    // seek on top of it — browsers commonly drop/ignore overlapping seeks,
    // which is what causes small movements to feel like they're not
    // updating the frame. Instead, queue it and apply it the instant the
    // current seek finishes.
    const applyPendingSeek = () => {
      if (pendingSeekTime === null) return;
      const target = pendingSeekTime;
      pendingSeekTime = null;
      video.currentTime = target;
    };
    const onSeeked = () => applyPendingSeek();
    video.addEventListener('seeked', onSeeked);

    const getTimestamp = clientX => {
      const rect = track.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      return ratio * state.duration;
    };

    const updateDragUi = () => {
      animationFrameId = 0;
      const timestamp = state.timestamps[index];
      state.activeIndex = index;
      bar.style.left = `${state.duration ? (timestamp / state.duration) * 100 : 0}%`;
      bar.title = `${timestamp.toFixed(2)}s`;
      bar.setAttribute('aria-label', `Headshot at ${timestamp.toFixed(2)} seconds`);
      if (timestampItem) {
        const jumpButton = timestampItem.querySelector('.timestamp-jump');
        const timestampInput = timestampItem.querySelector('.timestamp-input');
        const deleteButton = timestampItem.querySelector('.timestamp-delete');
        if (jumpButton) jumpButton.textContent = formatTime(timestamp);
        if (timestampInput) timestampInput.value = timestamp.toFixed(2);
        if (deleteButton) deleteButton.setAttribute('aria-label', `Delete headshot at ${formatTime(timestamp)}`);
        timestampItem.classList.add('active');
      }
      currentTimeLabel.textContent = `${timestamp.toFixed(2)}s`;
      bar.classList.add('active');
      emitChange();
    };

    const move = moveEvent => {
      pendingClientX = moveEvent.clientX;
      if (!freeDrag) startFreeDrag();
      const timestamp = getTimestamp(pendingClientX);
      moved = true;
      state.timestamps[index] = timestamp;
      pendingSeekTime = timestamp;
      if (!video.seeking) applyPendingSeek();
      if (!animationFrameId) animationFrameId = requestAnimationFrame(updateDragUi);
    };
    const startFreeDrag = () => {
      holdTimerId = 0;
      freeDrag = true;
      moved = true;
      state.dragging = true;
      const timestamp = getTimestamp(pendingClientX);
      state.timestamps[index] = timestamp;
      pendingSeekTime = timestamp;
      if (!video.seeking) applyPendingSeek();
      updateDragUi();
    };
    holdTimerId = window.setTimeout(startFreeDrag, 3000);
    const end = () => {
      bar.removeEventListener('pointermove', move);
      bar.removeEventListener('pointerup', end);
      bar.removeEventListener('pointercancel', end);
      video.removeEventListener('seeked', onSeeked);
      if (holdTimerId) window.clearTimeout(holdTimerId);
      if (animationFrameId) {
        cancelAnimationFrame(animationFrameId);
        animationFrameId = 0;
      }
      if (freeDrag) {
        pendingSeekTime = getTimestamp(pendingClientX);
        state.timestamps[index] = pendingSeekTime;
        if (!video.seeking) applyPendingSeek();
        updateDragUi();
      }
      video.pause();
      if (bar.hasPointerCapture(event.pointerId)) bar.releasePointerCapture(event.pointerId);
      state.dragging = false;
      if (freeDrag) {
        bar.dataset.dragged = 'true';
        render();
      }
    };
    bar.addEventListener('pointermove', move);
    bar.addEventListener('pointerup', end);
    bar.addEventListener('pointercancel', end);
  }

  addButton?.addEventListener('click', () => {
    if (!state.duration) return;
    const timestamp = Number(video.currentTime.toFixed(2));
    if (state.timestamps.some(existing => Math.abs(existing - timestamp) < 0.05)) return;
    const insertIndex = state.timestamps.findIndex(existing => existing > timestamp);
    const targetIndex = insertIndex === -1 ? state.timestamps.length : insertIndex;
    state.timestamps.splice(targetIndex, 0, timestamp);
    state.stepSizes.splice(targetIndex, 0, '0.4');
    state.activeIndex = targetIndex;
    render();
    emitChange();
  });

  video.addEventListener('loadedmetadata', () => {
    state.duration = Number.isFinite(video.duration) ? video.duration : 0;
    addButton.disabled = !state.duration;
    render();
  });
  video.addEventListener('timeupdate', () => updateActive(video.currentTime));
  video.addEventListener('seeking', () => updateActive(video.currentTime));

  window.JahviTimeline = {
    hasTimestamps: () => state.timestamps.length > 0,
    getTimestamps: () => [...state.timestamps],
    getDuration: () => state.duration,
    clear: () => { state.timestamps = []; state.stepSizes = []; state.activeIndex = -1; render(); emitChange(); },
  };
  render();
})();

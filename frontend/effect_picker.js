/*
 * effect_picker.js
 * Shared across Dashboard and Beat Sync Lab — per the project's own rule
 * that any function used on multiple pages lives in its own file, not
 * duplicated per page.
 *
 * Provides:
 *   - fuzzy subsequence search (command-palette style: query letters must
 *     appear in order in the effect name, not necessarily contiguous)
 *   - five random effects by default, with full-list search
 *   - a standalone favorites section (localStorage only, global across pages)
 *
 * Usage: window.JahviEffectPicker.render(rowEl, favoritesRowEl, searchInputEl, refreshBtnEl, effects, selectedIds, onToggle)
 */
(() => {
  const FAVORITES_KEY = 'jahvi_favorite_effects';

  function getFavorites() {
    try {
      return JSON.parse(localStorage.getItem(FAVORITES_KEY) || '[]');
    } catch {
      return [];
    }
  }

  function isFavorite(classId) {
    return getFavorites().includes(String(classId));
  }

  function toggleFavorite(classId) {
    const favs = getFavorites();
    const idStr = String(classId);
    const index = favs.indexOf(idStr);
    if (index >= 0) favs.splice(index, 1); else favs.push(idStr);
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(favs));
    return favs;
  }

  // Fuzzy subsequence match: every character in `query` (in order) must
  // appear somewhere in `text`, not necessarily contiguous.
  function fuzzyMatch(query, text) {
    if (!query) return true;
    const q = query.toLowerCase();
    const t = text.toLowerCase();
    let qi = 0;
    for (let ti = 0; ti < t.length && qi < q.length; ti++) {
      if (t[ti] === q[qi]) qi++;
    }
    return qi === q.length;
  }

  function pickRandom(arr, count) {
    const copy = [...arr];
    for (let index = copy.length - 1; index > 0; index -= 1) {
      const randomIndex = Math.floor(Math.random() * (index + 1));
      [copy[index], copy[randomIndex]] = [copy[randomIndex], copy[index]];
    }
    return copy.slice(0, count);
  }

  function makeEffectBox(effect, isSelected, onToggle, onFavoriteToggle) {
    const box = document.createElement('div');
    box.className = 'effect-class';
    box.dataset.classId = String(effect.class_id);
    box.classList.toggle('selected', isSelected);
    box.setAttribute('role', 'button');
    box.setAttribute('tabindex', '0');
    const statesHtml = (effect.states || [])
      .map(state => `<span class="effect-state">${state === 'per_beat' ? 'Per Beat' : 'Global'}</span>`)
      .join('');
    box.innerHTML = `
      <button type="button" class="effect-favorite-star" aria-label="Toggle favorite">${isFavorite(effect.class_id) ? '★' : '☆'}</button>
      <span class="effect-class-name"></span>
      <span class="effect-states">${statesHtml}</span>
    `;
    box.querySelector('.effect-class-name').textContent = effect.name;
    box.querySelector('.effect-favorite-star').addEventListener('click', (event) => {
      event.stopPropagation();
      toggleFavorite(effect.class_id);
      onFavoriteToggle();
    });
    box.addEventListener('click', () => onToggle(effect.class_id, box));
    box.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        onToggle(effect.class_id, box);
      }
    });
    return box;
  }

  function render(rowEl, favoritesRowEl, searchInputEl, refreshBtnEl, allEffects, selectedIds, onToggle) {
    let currentRandomFive = pickRandom(allEffects, Math.min(5, allEffects.length));

    function renderFavorites() {
      if (!favoritesRowEl) return;
      const favIds = getFavorites();
      const favEffects = allEffects.filter(e => favIds.includes(String(e.class_id)));
      favoritesRowEl.replaceChildren();
      if (favEffects.length === 0) {
        favoritesRowEl.classList.add('empty');
        return;
      }
      favoritesRowEl.classList.remove('empty');
      favEffects.forEach(effect => {
        favoritesRowEl.appendChild(
          makeEffectBox(effect, selectedIds().includes(effect.class_id), handleToggle, renderAll)
        );
      });
    }

    function renderMainRow() {
      const query = searchInputEl ? searchInputEl.value.trim() : '';
      const list = query
        ? allEffects.filter(effect => fuzzyMatch(query, effect.name))
        : currentRandomFive;
      rowEl.replaceChildren();
      list.forEach(effect => {
        rowEl.appendChild(
          makeEffectBox(effect, selectedIds().includes(effect.class_id), handleToggle, renderAll)
        );
      });
    }

    // Same effect can appear in both the favorites row and the main row
    // at once — re-rendering both from selectedIds() on every click (
    // instead of only toggling the one DOM node that was clicked) keeps
    // both copies' highlight state in sync, since they're both derived
    // fresh from the same source of truth every time.
    function handleToggle(classId, box) {
      onToggle(classId, box);
      renderAll();
    }

    function renderAll() {
      renderFavorites();
      renderMainRow();
    }

    if (searchInputEl) {
      searchInputEl.addEventListener('input', renderMainRow);
    }
    if (refreshBtnEl) {
      refreshBtnEl.addEventListener('click', () => {
        currentRandomFive = pickRandom(allEffects, Math.min(5, allEffects.length));
        if (searchInputEl) searchInputEl.value = '';
        renderMainRow();
      });
    }
    renderAll();
    return { refresh: renderAll };
  }

  window.JahviEffectPicker = { render, fuzzyMatch };
})();

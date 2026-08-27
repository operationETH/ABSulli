const LIVE_ACTIVITY_REFRESH_MS = 15000;
let liveActivityRows = [];
let liveActivityTimer = null;

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function formatDateTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds || 0)));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  return `${m}:${String(sec).padStart(2, '0')}`;
}

function livePosition(row) {
  const duration = Number(row.duration || 0);
  let current = Number(row.current_time || 0);
  const updatedRaw = row.updated_at || row.last_seen_at;
  if (updatedRaw) {
    const updated = new Date(updatedRaw);
    if (!Number.isNaN(updated.getTime())) {
      current += Math.max(0, (Date.now() - updated.getTime()) / 1000);
    }
  }
  if (duration > 0) return Math.min(duration, Math.max(0, current));
  return Math.max(0, current);
}

function liveProgress(row) {
  const duration = Number(row.duration || 0);
  const current = livePosition(row);
  if (duration > 0) return Math.max(0, Math.min(100, (current / duration) * 100));
  return Math.max(0, Math.min(100, Number(row.progress || 0)));
}

function activitySummary(count) {
  return count === 1 ? '1 active listening session.' : count > 1 ? `${count} active listening sessions.` : 'Nothing is currently being played.';
}

function renderStreamCard(row) {
  const progress = liveProgress(row);
  const position = livePosition(row);
  const title = row.title || 'Unknown title';
  const author = row.author || row.media_type || 'Unknown';
  const user = row.username || 'Unknown';
  const product = row.client || 'Unknown';
  const player = row.device || row.model || 'Unknown';
  const mediaType = row.media_type || 'unknown';
  const library = row.library_name || 'Unknown';
  const duration = Number(row.duration || 0);
  const positionText = duration > 0 ? `${formatDuration(position)} / ${formatDuration(duration)}` : formatDuration(position);
  const cover = row.abs_item_id
    ? `<img src="/covers/items/${encodeURIComponent(row.abs_item_id)}?width=260" alt="${escapeHtml(title)} cover" loading="lazy">`
    : '<span>♫</span>';
  const titleLink = row.abs_item_id
    ? `<a href="/media/${encodeURIComponent(row.abs_item_id)}">${escapeHtml(title)}</a>`
    : escapeHtml(title);

  return `
    <article class="stream-card activity-card" data-session-key="${escapeHtml(row.session_key || '')}">
      <div class="stream-main">
        <div class="stream-cover">${cover}</div>
        <div class="stream-details">
          <div class="stream-client-badge" title="${escapeHtml(player || product)}">${escapeHtml(String(product || player || '?').slice(0, 1))}</div>
          <dl>
            <div><dt>Product</dt><dd>${escapeHtml(product)}</dd></div>
            <div><dt>Player</dt><dd>${escapeHtml(player)}</dd></div>
            <div><dt>Media</dt><dd>${escapeHtml(mediaType)}</dd></div>
            <div><dt>Library</dt><dd>${escapeHtml(library)}</dd></div>
            <div><dt>Position</dt><dd><span data-live-position>${escapeHtml(positionText)}</span></dd></div>
            <div><dt>Progress</dt><dd><span data-live-progress>${progress.toFixed(1)}</span>%</dd></div>
          </dl>
        </div>
      </div>
      <div class="progress stream-progress"><span data-live-progress-bar data-initial-progress="${progress.toFixed(1)}"></span></div>
      <div class="stream-footer">
        <div class="stream-title-wrap">
          <h3>${titleLink}</h3>
          <p>${escapeHtml(author)}</p>
        </div>
        <div class="stream-user" title="${escapeHtml(user)}">
          <span>${escapeHtml(user.slice(0, 1) || '?')}</span>
          <strong>${escapeHtml(user)}</strong>
        </div>
      </div>
    </article>`;
}

function renderDashboardActivity(rows) {
  const summary = document.getElementById('live-activity-summary');
  if (summary) summary.textContent = activitySummary(rows.length);

  const panel = document.getElementById('now-listening-panel');
  const grid = document.getElementById('now-listening-grid');
  if (!panel || !grid) return;

  panel.classList.toggle('is-hidden', rows.length === 0);
  grid.innerHTML = rows.map(renderStreamCard).join('');
  initializeStaticProgressBars(grid);
}

function renderActivityTable(rows) {
  const activeCount = document.getElementById('activity-active-count');
  if (activeCount) activeCount.textContent = rows.length;

  const summary = document.getElementById('activity-page-summary');
  if (summary) summary.textContent = activitySummary(rows.length);

  const lastRefresh = document.getElementById('activity-last-refresh');
  if (lastRefresh) lastRefresh.textContent = `Updated ${new Date().toLocaleTimeString()}`;

  const tableBody = document.getElementById('activity-table-body');
  if (!tableBody) return;

  if (!rows.length) {
    tableBody.innerHTML = '<tr><td colspan="6" class="empty">No active listening sessions.</td></tr>';
    return;
  }

  tableBody.innerHTML = rows.map((row) => {
    const progress = liveProgress(row);
    const title = row.title || 'Unknown title';
    const titleCell = row.abs_item_id
      ? `<a class="table-link" href="/media/${encodeURIComponent(row.abs_item_id)}">${escapeHtml(title)}</a>`
      : escapeHtml(title);
    return `<tr data-session-key="${escapeHtml(row.session_key || '')}">
      <td><span class="status-dot green"></span>Active</td>
      <td>${escapeHtml(row.username || 'Unknown')}</td>
      <td>${titleCell}</td>
      <td><span data-live-progress>${progress.toFixed(1)}</span>%</td>
      <td>${escapeHtml(row.device || row.client || 'Unknown')}</td>
      <td>${escapeHtml(formatDateTime(row.last_seen_at || row.updated_at))}</td>
    </tr>`;
  }).join('');
}

function tickLiveProgress() {
  document.querySelectorAll('[data-live-progress]').forEach((node) => {
    const holder = node.closest('.stream-card, tr[data-session-key]');
    if (!holder) return;
    const key = holder.dataset.sessionKey;
    const row = liveActivityRows.find((candidate) => String(candidate.session_key || '') === String(key || ''));
    if (!row) return;
    const progress = liveProgress(row);
    node.textContent = progress.toFixed(1);
    const positionNode = holder.querySelector('[data-live-position]');
    if (positionNode) {
      const duration = Number(row.duration || 0);
      const position = livePosition(row);
      positionNode.textContent = duration > 0 ? `${formatDuration(position)} / ${formatDuration(duration)}` : formatDuration(position);
    }
    const bar = holder.querySelector('[data-live-progress-bar], [data-progress]');
    if (bar) bar.style.width = `${progress.toFixed(1)}%`;
  });
}

async function refreshStatus() {
  try {
    const r = await fetch('/api/status', { cache: 'no-store' });
    if (!r.ok) return;
    const data = await r.json();
    document.title = `ABSulli · ${data.active_sessions} active`;
  } catch (e) {}
}

async function refreshLiveActivity() {
  const needsDashboard = document.getElementById('now-listening-panel') || document.getElementById('live-activity-summary');
  const needsActivityPage = document.getElementById('activity-table-body');
  if (!needsDashboard && !needsActivityPage) return;

  try {
    const r = await fetch('/api/activity', { cache: 'no-store' });
    if (!r.ok) return;
    liveActivityRows = await r.json();
    if (needsDashboard) renderDashboardActivity(liveActivityRows);
    if (needsActivityPage) renderActivityTable(liveActivityRows);
  } catch (e) {}
}



function initializeCardBackgrounds(root = document) {
  root.querySelectorAll('[data-card-bg]').forEach((card) => {
    card.style.setProperty('--card-bg', card.dataset.cardBg || 'linear-gradient(135deg,#303030,#242424)');
  });
}

function initializeMediaBackgrounds(root = document) {
  root.querySelectorAll('[data-media-bg]').forEach((hero) => {
    const value = hero.dataset.mediaBg || '';
    if (value) hero.style.setProperty('--media-bg', `url('${value.replaceAll("'", "%27")}')`);
  });
}

function initializeAutoSubmit(root = document) {
  root.querySelectorAll('[data-auto-submit]').forEach((field) => {
    field.addEventListener('change', () => {
      if (field.form) field.form.submit();
    });
  });
}

function initializeHoverCovers(root = document) {
  root.querySelectorAll('.stat-card').forEach((card) => {
    const media = card.querySelector('.stat-media');
    if (!media) return;

    const originalHtml = media.innerHTML;
    const originalHadCover = media.classList.contains('has-cover');

    const ensureImage = () => {
      let image = media.querySelector('img');
      if (!image) {
        media.innerHTML = '';
        image = document.createElement('img');
        media.appendChild(image);
      }
      media.classList.add('has-cover');
      return image;
    };

    const restoreOriginal = () => {
      media.innerHTML = originalHtml;
      media.classList.toggle('has-cover', originalHadCover);
      media.classList.remove('is-hovering');
    };

    card.querySelectorAll('[data-hover-cover]').forEach((row) => {
      const setHover = () => {
        const image = ensureImage();
        image.src = row.dataset.hoverCover;
        if (row.dataset.hoverFallbackCover) {
          image.dataset.fallbackSrc = row.dataset.hoverFallbackCover;
        } else {
          image.removeAttribute('data-fallback-src');
        }
        image.alt = `${row.querySelector('.item-name')?.textContent?.trim() || 'Selected item'} cover`;
        media.classList.add('is-hovering');
      };

      row.addEventListener('mouseenter', setHover);
      row.addEventListener('mouseleave', restoreOriginal);
      row.addEventListener('focusin', setHover);
      row.addEventListener('focusout', restoreOriginal);
    });
  });
}

function initializeStaticProgressBars(root = document) {
  root.querySelectorAll('[data-live-progress-bar][data-initial-progress]').forEach((bar) => {
    const progress = Math.max(0, Math.min(100, Number(bar.dataset.initialProgress || 0)));
    bar.style.width = `${progress.toFixed(1)}%`;
  });
}


function initializeSetupConnectionTest(root = document) {
  const form = root.getElementById ? root.getElementById('setup-form') : document.getElementById('setup-form');
  const button = root.getElementById ? root.getElementById('setup-test-connection') : document.getElementById('setup-test-connection');
  const result = root.getElementById ? root.getElementById('setup-test-result') : document.getElementById('setup-test-result');
  if (!form || !button || !result) return;

  const showResult = (state, message) => {
    result.hidden = false;
    result.className = `setup-test-result ${state}`;
    result.textContent = message;
  };

  button.addEventListener('click', async () => {
    showResult('is-loading', '↕ Testing connection...');
    button.disabled = true;

    try {
      const response = await fetch('/setup/test-connection', {
        method: 'POST',
        body: new FormData(form),
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'Accept': 'application/json' },
      });
      const data = await response.json().catch(() => ({}));
      const ok = response.ok && data.ok;
      showResult(
        ok ? 'is-success' : 'is-error',
        data.message || (ok ? '✓ Connection successful!' : '⚠ Connection failed.')
      );
    } catch (e) {
      showResult('is-error', '⚠ Connection test failed. Check the URL and network.');
    } finally {
      button.disabled = false;
    }
  });
}

function initializeGeneralSettingsTest(root = document) {
  const form = root.getElementById ? root.getElementById('general-settings-form') : document.getElementById('general-settings-form');
  const button = root.getElementById ? root.getElementById('general-test-connection') : document.getElementById('general-test-connection');
  const result = root.getElementById ? root.getElementById('general-test-result') : document.getElementById('general-test-result');
  if (!form || !button || !result) return;

  const showResult = (state, message) => {
    result.hidden = false;
    result.className = `setup-test-result ${state}`;
    result.textContent = message;
  };

  button.addEventListener('click', async () => {
    showResult('is-loading', '↕ Testing connection...');
    button.disabled = true;

    try {
      const response = await fetch('/settings/general/test', {
        method: 'POST',
        body: new FormData(form),
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'Accept': 'application/json' },
      });
      const data = await response.json().catch(() => ({}));
      const ok = response.ok && data.ok;
      showResult(ok ? 'is-success' : 'is-error', data.message || (ok ? '✓ Connection successful!' : '⚠ Connection failed.'));
    } catch (e) {
      showResult('is-error', '⚠ Connection test failed. Check the URL and network.');
    } finally {
      button.disabled = false;
    }
  });
}

function initializeNotificationAgentSettings(root = document) {
  const advancedToggles = Array.from(root.querySelectorAll('[data-agent-advanced-toggle]'));
  advancedToggles.forEach((button) => {
    const form = button.closest('[data-agent-form]');
    const content = form?.querySelector('[data-agent-advanced-content]');
    const state = form?.querySelector('[data-agent-advanced-state]');
    if (!form || !content || !state) return;

    button.addEventListener('click', () => {
      const open = content.hidden;
      content.hidden = !open;
      state.value = open ? 'true' : 'false';
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  });

  const libraryScopes = Array.from(root.querySelectorAll('[data-library-scope]'));
  libraryScopes.forEach((scope) => {
    const allLibraries = scope.querySelector('[data-all-libraries]');
    const libraryOptions = Array.from(scope.querySelectorAll('[data-library-option]'));
    if (!allLibraries) return;

    const syncLibraryOptions = () => {
      libraryOptions.forEach((input) => {
        input.disabled = allLibraries.checked;
        input.closest('.event-option')?.classList.toggle('is-disabled', allLibraries.checked);
      });
    };

    allLibraries.addEventListener('change', syncLibraryOptions);
    syncLibraryOptions();
  });

  const buttons = Array.from(root.querySelectorAll('[data-agent-test]'));
  buttons.forEach((button) => {
    const agentId = button.dataset.agentTest;
    const form = root.querySelector(`[data-agent-form="${agentId}"]`);
    const result = root.getElementById ? root.getElementById(`${agentId}-test-result`) : document.getElementById(`${agentId}-test-result`);
    if (!form || !result) return;

    const showResult = (state, message) => {
      result.hidden = false;
      result.className = `setup-test-result ${state}`;
      result.textContent = message;
    };

    button.addEventListener('click', async () => {
      showResult('is-loading', '↕ Sending test notification...');
      button.disabled = true;

      try {
        const response = await fetch(`/settings/notifications/${agentId}/test`, {
          method: 'POST',
          body: new FormData(form),
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { 'Accept': 'application/json' },
        });
        const data = await response.json().catch(() => ({}));
        const ok = response.ok && data.ok;
        showResult(ok ? 'is-success' : 'is-error', data.message || (ok ? '✓ Test notification sent.' : '⚠ Test failed.'));
      } catch (e) {
        showResult('is-error', '⚠ Test failed. Check the settings and network.');
      } finally {
        button.disabled = false;
      }
    });
  });
}

function startLiveActivityRefresh() {
  initializeCardBackgrounds();
  initializeMediaBackgrounds();
  initializeAutoSubmit();
  initializeHoverCovers();
  initializeStaticProgressBars();
  initializeSetupConnectionTest();
  initializeGeneralSettingsTest();
  initializeNotificationAgentSettings();
  refreshStatus();
  refreshLiveActivity();
  setInterval(refreshStatus, 30000);
  setInterval(refreshLiveActivity, LIVE_ACTIVITY_REFRESH_MS);
  if (!liveActivityTimer) liveActivityTimer = setInterval(tickLiveProgress, 1000);
}

startLiveActivityRefresh();

document.addEventListener('error', (event) => {
  const image = event.target;
  if (!(image instanceof HTMLImageElement)) return;

  if (image.classList.contains('js-author-image')) {
    const fallbackSrc = image.dataset.fallbackSrc || '';
    if (fallbackSrc) {
      const fallbackUrl = new URL(fallbackSrc, window.location.href).href;
      if (image.src !== fallbackUrl) {
        image.src = fallbackSrc;
        image.removeAttribute('data-fallback-src');

        const hero = image.closest('[data-fallback-media-bg]');
        const fallbackBg = hero?.dataset.fallbackMediaBg || '';
        if (fallbackBg) {
          hero.style.setProperty('--media-bg', `url('${fallbackBg.replaceAll("'", "%27")}')`);
          hero.removeAttribute('data-fallback-media-bg');
        }
        return;
      }
    }

    const poster = image.closest('.media-poster');
    if (poster) poster.classList.add('poster-fallback');
    image.remove();
    return;
  }

  const statMedia = image.closest('.stat-media');
  if (statMedia) {
    const fallbackSrc = image.dataset.fallbackSrc || '';
    if (fallbackSrc) {
      const fallbackUrl = new URL(fallbackSrc, window.location.href).href;
      if (image.src !== fallbackUrl) {
        image.src = fallbackSrc;
        image.removeAttribute('data-fallback-src');
        statMedia.classList.add('has-cover');
        return;
      }
    }

    const icon = statMedia.dataset.icon || '▤';
    statMedia.classList.remove('has-cover', 'is-hovering');
    statMedia.innerHTML = `<span>${escapeHtml(icon)}</span>`;
    return;
  }
}, true);


document.querySelectorAll('[data-readmore]').forEach((wrap) => {
  const description = wrap.querySelector('.media-description');
  const button = wrap.querySelector('[data-readmore-toggle]');
  if (!description || !button) return;

  const hasOverflow = () => description.scrollHeight > description.clientHeight + 2;

  requestAnimationFrame(() => {
    if (hasOverflow()) wrap.classList.add('has-overflow');
  });

  button.addEventListener('click', () => {
    const expanded = wrap.classList.toggle('is-expanded');
    wrap.classList.toggle('is-collapsed', !expanded);
    button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    button.firstChild.nodeValue = expanded ? 'Show less ' : 'Read more ';
  });
});

function getGraphColor(index) {
  const colors = ['#d1a12c', '#6fa8dc', '#c94f4f', '#7cb342', '#b085c9', '#f39c5a', '#75c9c8', '#d8d8d8'];
  return colors[index % colors.length];
}

function niceMax(value) {
  const raw = Math.max(1, Number(value || 0));
  const power = Math.pow(10, Math.floor(Math.log10(raw)));
  return Math.ceil(raw / power) * power;
}

function graphHasData(chart) {
  if (!chart) return false;
  if (chart.type === 'line') {
    return (chart.series || []).some((series) => (series.data || []).some((value) => Number(value || 0) > 0));
  }
  if (chart.type === 'heatmap') {
    return (chart.columns || []).some((col) => (col.days || []).some((cell) => cell && Number(cell.value || 0) > 0));
  }
  return (chart.values || []).some((value) => Number(value || 0) > 0);
}

function drawAxes(ctx, width, height, pad, maxValue) {
  ctx.strokeStyle = 'rgba(255,255,255,.28)';
  ctx.lineWidth = 1;
  ctx.font = '12px Helvetica, Arial, sans-serif';
  ctx.fillStyle = '#aaa';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';

  for (let i = 0; i <= 4; i += 1) {
    const y = pad.top + ((height - pad.top - pad.bottom) * i) / 4;
    const value = maxValue - (maxValue * i) / 4;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillText(value >= 10 ? Math.round(value).toString() : value.toFixed(1).replace(/\.0$/, ''), pad.left - 8, y);
  }
}

function drawLegend(ctx, labels, width, height) {
  let x = Math.max(60, width / 2 - labels.length * 46);
  const y = height - 16;
  ctx.font = '11px Helvetica, Arial, sans-serif';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  labels.forEach((label, index) => {
    ctx.fillStyle = getGraphColor(index);
    ctx.fillRect(x, y - 3, 10, 3);
    ctx.fillStyle = '#aaa';
    ctx.fillText(String(label).slice(0, 24), x + 14, y);
    x += Math.min(150, 38 + String(label).length * 6);
  });
}

function drawLineChart(canvas, chart) {
  const ctx = canvas.getContext('2d');
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * scale);
  canvas.height = Math.floor(rect.height * scale);
  ctx.setTransform(scale, 0, 0, scale, 0, 0);

  const width = rect.width;
  const height = rect.height;
  const pad = { top: 22, right: 22, bottom: 54, left: 50 };
  ctx.clearRect(0, 0, width, height);

  const allValues = (chart.series || []).flatMap((series) => series.data || []).map(Number);
  const maxValue = niceMax(Math.max(...allValues, 0));
  drawAxes(ctx, width, height, pad, maxValue);

  const labels = chart.labels || [];
  const pointCount = Math.max(1, labels.length - 1);
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;

  (chart.series || []).forEach((series, seriesIndex) => {
    const data = series.data || [];
    ctx.strokeStyle = getGraphColor(seriesIndex);
    ctx.fillStyle = getGraphColor(seriesIndex);
    ctx.lineWidth = 2;
    ctx.beginPath();
    data.forEach((raw, index) => {
      const value = Number(raw || 0);
      const x = pad.left + (plotWidth * index) / pointCount;
      const y = pad.top + plotHeight - (plotHeight * value) / maxValue;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    data.forEach((raw, index) => {
      const value = Number(raw || 0);
      const x = pad.left + (plotWidth * index) / pointCount;
      const y = pad.top + plotHeight - (plotHeight * value) / maxValue;
      ctx.beginPath();
      ctx.arc(x, y, 3, 0, Math.PI * 2);
      ctx.fill();
    });
  });

  ctx.fillStyle = '#aaa';
  ctx.font = '11px Helvetica, Arial, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const perIndex = plotWidth / pointCount;
  const step = Math.max(1, Math.ceil(60 / perIndex));
  const labelCount = Math.max(2, Math.floor(pointCount / step) + 1);
  const shownIndexes = new Set();
  for (let k = 0; k < labelCount; k += 1) {
    shownIndexes.add(Math.round((k * pointCount) / (labelCount - 1)));
  }
  labels.forEach((label, index) => {
    if (!shownIndexes.has(index)) return;
    const x = pad.left + (plotWidth * index) / pointCount;
    ctx.fillText(label, x, height - pad.bottom + 18);
  });
  drawLegend(ctx, (chart.series || []).map((series) => series.name), width, height);
}

function drawBarChart(canvas, chart) {
  const ctx = canvas.getContext('2d');
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * scale);
  canvas.height = Math.floor(rect.height * scale);
  ctx.setTransform(scale, 0, 0, scale, 0, 0);

  const width = rect.width;
  const height = rect.height;
  const labels = chart.labels || [];
  const values = (chart.values || []).map(Number);
  const maxValue = niceMax(Math.max(...values, 0));

  ctx.font = '11px Helvetica, Arial, sans-serif';
  const shownLabels = labels.map((label) => {
    const text = String(label);
    return text.length > 18 ? text.slice(0, 17) + '…' : text;
  });

  const plotWidth = width - 50 - 20;
  const gap = Math.max(4, Math.min(12, (plotWidth / Math.max(1, values.length)) * 0.18));
  const barWidth = Math.max(4, (plotWidth - gap * Math.max(0, values.length - 1)) / Math.max(1, values.length));
  const spacing = barWidth + gap;

  const labelStep = values.length > 16 ? Math.ceil(values.length / 12) : 1;
  const visibleWidths = shownLabels
    .filter((_, index) => index % labelStep === 0)
    .map((text) => ctx.measureText(text).width);
  const longestLabel = Math.max(1, ...visibleWidths);
  const angle = (55 * Math.PI) / 180;
  const labelVertical = Math.min(112, longestLabel * Math.sin(angle) + 16);

  const pad = { top: 22, right: 20, bottom: Math.max(44, labelVertical + 12), left: 50 };
  const plotHeight = height - pad.top - pad.bottom;

  ctx.clearRect(0, 0, width, height);
  drawAxes(ctx, width, height, pad, maxValue);

  values.forEach((value, index) => {
    const x = pad.left + index * (barWidth + gap);
    const barHeight = (plotHeight * value) / maxValue;
    const y = pad.top + plotHeight - barHeight;
    ctx.fillStyle = getGraphColor(index);
    ctx.fillRect(x, y, barWidth, barHeight);
  });

  ctx.fillStyle = '#aaa';
  ctx.font = '11px Helvetica, Arial, sans-serif';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  shownLabels.forEach((label, index) => {
    if (index % labelStep !== 0) return;
    const x = pad.left + index * (barWidth + gap) + barWidth / 2;
    ctx.save();
    ctx.translate(x, height - pad.bottom + 14);
    ctx.rotate(-angle);
    ctx.fillText(label, 0, 0);
    ctx.restore();
  });
}

function heatmapThresholds(values) {
  const nz = values.filter((v) => v > 0).sort((a, b) => a - b);
  if (nz.length === 0) return [1, 1, 1];
  const q = (p) => nz[Math.min(nz.length - 1, Math.floor(p * nz.length))];
  return [q(0.25), q(0.5), q(0.75)];
}

function heatmapColor(value, thresholds) {
  const ramp = ['#242424', '#4d3c14', '#7d611e', '#b08a2a', '#d4ad45'];
  if (value <= 0) return ramp[0];
  if (value <= thresholds[0]) return ramp[1];
  if (value <= thresholds[1]) return ramp[2];
  if (value <= thresholds[2]) return ramp[3];
  return ramp[4];
}

function ensureHeatmapTooltip() {
  let tip = document.getElementById('heatmap-tooltip');
  if (tip) return tip;
  tip = document.createElement('div');
  tip.id = 'heatmap-tooltip';
  tip.style.cssText = 'position:fixed;z-index:9999;display:none;pointer-events:none;background:#000;color:#e6e6e6;border:1px solid #3c3c3c;border-radius:3px;padding:5px 8px;font:12px Helvetica,Arial,sans-serif;white-space:nowrap;box-shadow:0 4px 12px rgba(0,0,0,.5);';
  document.body.appendChild(tip);
  return tip;
}

function formatHeatmapTip(cell, unit) {
  let dateLabel = cell.date;
  if (cell.date) {
    const parts = String(cell.date).split('-').map(Number);
    if (parts.length === 3) {
      const d = new Date(parts[0], parts[1] - 1, parts[2]);
      dateLabel = d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
    }
  }
  let valueText;
  if (/hour/i.test(unit || '')) valueText = `${cell.value} hours`;
  else if (cell.value === 0) valueText = 'No plays';
  else valueText = `${cell.value} play${cell.value === 1 ? '' : 's'}`;
  return `${valueText} on ${dateLabel}`;
}

function bindHeatmapTooltip(canvas) {
  if (canvas.__heatmapBound) return;
  if (!window.matchMedia || !window.matchMedia('(hover: hover)').matches) return;
  canvas.__heatmapBound = true;
  const tip = ensureHeatmapTooltip();
  canvas.addEventListener('mousemove', (event) => {
    const cells = canvas.__heatmapCells || [];
    const rect = canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    let hit = null;
    for (let i = 0; i < cells.length; i += 1) {
      const c = cells[i];
      if (mx >= c.x && mx <= c.x + c.size && my >= c.y && my <= c.y + c.size) { hit = c; break; }
    }
    if (!hit) { tip.style.display = 'none'; return; }
    tip.textContent = formatHeatmapTip(hit, canvas.__heatmapUnit);
    tip.style.display = 'block';
    let left = event.clientX + 12;
    let top = event.clientY - tip.offsetHeight - 10;
    if (top < 6) top = event.clientY + 16;
    const maxLeft = window.innerWidth - tip.offsetWidth - 8;
    if (left > maxLeft) left = maxLeft;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  });
  canvas.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
}

function drawHeatmap(canvas, chart) {
  const ctx = canvas.getContext('2d');
  const columns = chart.columns || [];
  const cols = Math.max(1, columns.length);
  const gap = 2;
  const padLeft = 34;
  const padTop = 20;
  const padBottom = 26;
  const compact = window.matchMedia('(max-width: 1180px)').matches;

  canvas.style.height = '';
  const rect = canvas.getBoundingClientRect();
  const width = rect.width || 320;
  const availWidth = width - padLeft - 8;
  const cell = Math.max(4, (availWidth - gap * (cols - 1)) / cols);
  const gridHeight = 7 * cell + gap * 6;

  let height;
  let originY;
  if (compact) {
    height = Math.ceil(padTop + gridHeight + padBottom);
    canvas.style.height = height + 'px';
    originY = padTop;
  } else {
    height = rect.height || 284;
    originY = padTop + Math.max(0, (height - padTop - padBottom - gridHeight) / 2);
  }
  const gridWidth = cols * cell + (cols - 1) * gap;

  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.floor(width * scale);
  canvas.height = Math.floor(height * scale);
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const dayValues = [];
  columns.forEach((col) => (col.days || []).forEach((c) => {
    if (c) dayValues.push(Number(c.value || 0));
  }));
  const thresholds = heatmapThresholds(dayValues);

  ctx.font = '11px Helvetica, Arial, sans-serif';
  ctx.fillStyle = '#888';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  const weekdayLabels = ['Mon', '', 'Wed', '', 'Fri', '', ''];
  for (let r = 0; r < 7; r += 1) {
    if (!weekdayLabels[r]) continue;
    ctx.fillText(weekdayLabels[r], padLeft - 6, originY + r * (cell + gap) + cell / 2);
  }

  ctx.textAlign = 'left';
  ctx.textBaseline = 'alphabetic';
  const cells = [];
  let lastMonthX = -999;
  columns.forEach((col, c) => {
    const x = padLeft + c * (cell + gap);
    if (col.month && x - lastMonthX >= 24) {
      ctx.fillStyle = '#888';
      ctx.fillText(col.month, x, originY - 8);
      lastMonthX = x;
    }
    (col.days || []).forEach((cellData, r) => {
      if (!cellData) return;
      const y = originY + r * (cell + gap);
      const value = Number(cellData.value || 0);
      ctx.fillStyle = heatmapColor(value, thresholds);
      ctx.fillRect(x, y, cell, cell);
      cells.push({ x, y, size: cell, date: cellData.date, value });
    });
  });

  const ramp = ['#242424', '#4d3c14', '#7d611e', '#b08a2a', '#d4ad45'];
  const swatch = 11;
  const swatchGap = 3;
  const legendY = height - 10;
  let legendX = padLeft + gridWidth - (ramp.length * (swatch + swatchGap) + 66);
  if (legendX < padLeft) legendX = padLeft;
  ctx.font = '11px Helvetica, Arial, sans-serif';
  ctx.fillStyle = '#888';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  ctx.fillText('Less', legendX, legendY);
  legendX += 6;
  for (let i = 0; i < ramp.length; i += 1) {
    ctx.fillStyle = ramp[i];
    ctx.fillRect(legendX, legendY - swatch / 2, swatch, swatch);
    legendX += swatch + swatchGap;
  }
  ctx.fillStyle = '#888';
  ctx.textAlign = 'left';
  ctx.fillText('More', legendX + 4, legendY);

  canvas.__heatmapCells = cells;
  canvas.__heatmapUnit = chart.unit || 'Plays';
  bindHeatmapTooltip(canvas);
}

function renderGraphs() {
  const dataNode = document.getElementById('graphs-data');
  if (!dataNode) return;
  let charts = [];
  try {
    charts = JSON.parse(dataNode.textContent || '[]');
  } catch (e) {
    return;
  }

  document.querySelectorAll('.abs-graph[data-chart-index]').forEach((canvas) => {
    const chart = charts[Number(canvas.dataset.chartIndex || 0)];
    const card = canvas.closest('.graph-card');
    if (!graphHasData(chart)) {
      if (card) card.classList.add('is-empty');
      return;
    }
    if (card) card.classList.remove('is-empty');
    if (chart.type === 'line') drawLineChart(canvas, chart);
    else if (chart.type === 'heatmap') drawHeatmap(canvas, chart);
    else drawBarChart(canvas, chart);
  });
}

renderGraphs();
window.addEventListener('resize', () => {
  if (document.getElementById('graphs-data')) window.requestAnimationFrame(renderGraphs);
});

function initializeNotificationAgentTabs(root = document) {
  const tabs = Array.from(root.querySelectorAll('[data-agent-tab]'));
  if (!tabs.length) return;

  const panels = Array.from(root.querySelectorAll('[data-agent-panel]'));
  const activate = (agentId) => {
    tabs.forEach((tab) => {
      const active = tab.dataset.agentTab === agentId;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    panels.forEach((panel) => {
      panel.classList.toggle('active', panel.dataset.agentPanel === agentId);
    });
  };

  tabs.forEach((tab) => {
    tab.addEventListener('click', () => activate(tab.dataset.agentTab || 'gotify'));
  });
}

initializeNotificationAgentTabs();

async function copyTextToClipboard(value) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    return;
  }

  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);
  const copied = document.execCommand('copy');
  textarea.remove();
  if (!copied) throw new Error('Copy command failed');
}

document.addEventListener('submit', (event) => {
  const form = event.target.closest('[data-notification-clear]');
  if (form && !window.confirm('Clear all notification log entries? This cannot be undone.')) {
    event.preventDefault();
  }
});

let notificationTemplateTarget = null;

document.addEventListener('focusin', (event) => {
  if (event.target.matches('.notification-template-fields input, .notification-template-fields textarea, .webhook-payload-fields textarea')) {
    notificationTemplateTarget = event.target;
  }
});

document.addEventListener('click', async (event) => {
  const templateVariable = event.target.closest('[data-template-variable]');
  if (templateVariable) {
    const form = templateVariable.closest('form');
    let target = notificationTemplateTarget;
    if (!target || !target.isConnected || target.form !== form) {
      target = form?.querySelector('.notification-template[open] .webhook-payload-fields textarea, .notification-template[open] .notification-template-fields textarea, .notification-template[open] .notification-template-fields input');
    }
    if (target) {
      const variable = templateVariable.dataset.templateVariable || '';
      const start = target.selectionStart ?? target.value.length;
      const end = target.selectionEnd ?? start;
      target.setRangeText(variable, start, end, 'end');
      target.dispatchEvent(new Event('input', { bubbles: true }));
      target.focus();
      notificationTemplateTarget = target;
    }
    return;
  }

  const addWebhookHeader = event.target.closest('[data-webhook-add-header]');
  if (addWebhookHeader) {
    const container = addWebhookHeader.closest('[data-webhook-custom-headers]');
    const list = container?.querySelector('[data-webhook-header-list]');
    if (!list) return;
    const row = document.createElement('div');
    row.className = 'webhook-custom-header-row';
    row.innerHTML = '<input name="webhook_custom_header_name" type="text" placeholder="Header Name" autocomplete="off"><input name="webhook_custom_header_value" type="text" placeholder="Header Value" autocomplete="off"><button type="button" class="secondary" data-webhook-remove-header>Remove</button>';
    list.appendChild(row);
    row.querySelector('input')?.focus();
    return;
  }

  const removeWebhookHeader = event.target.closest('[data-webhook-remove-header]');
  if (removeWebhookHeader) {
    removeWebhookHeader.closest('.webhook-custom-header-row')?.remove();
    return;
  }

  const resetWebhookPayload = event.target.closest('[data-webhook-reset-payload]');
  if (resetWebhookPayload) {
    const form = resetWebhookPayload.closest('form');
    const editor = form?.querySelector('textarea[name="webhook_payload_template"]');
    const source = form?.querySelector('[data-webhook-default-payload]');
    if (!editor || !source) return;
    try {
      editor.value = JSON.parse(source.textContent || '""');
      editor.dispatchEvent(new Event('input', { bubbles: true }));
    } catch (_error) {
      return;
    }
    return;
  }

  const absToggle = event.target.closest('[data-abs-api-key-toggle]');
  if (absToggle) {
    const input = document.getElementById('abs-api-key');
    if (!input) return;
    const fromEnv = input.dataset.absApiKeyFromEnv === 'true';
    if (fromEnv) {
      const masked = input.dataset.absApiKeyMasked !== 'false';
      const nextMasked = !masked;
      input.value = nextMasked ? 'Configured via .env read only' : (input.dataset.absApiKeyValue || '');
      input.dataset.absApiKeyMasked = nextMasked ? 'true' : 'false';
    } else {
      const showing = input.type === 'text';
      input.type = showing ? 'password' : 'text';
    }
    const masked = fromEnv ? input.dataset.absApiKeyMasked !== 'false' : input.type === 'password';
    const showIcon = absToggle.querySelector('.abs-api-key-icon-show');
    const hideIcon = absToggle.querySelector('.abs-api-key-icon-hide');
    if (showIcon) showIcon.hidden = !masked;
    if (hideIcon) hideIcon.hidden = masked;
    const label = masked ? 'Show Audiobookshelf API key' : 'Hide Audiobookshelf API key';
    absToggle.setAttribute('aria-label', label);
    absToggle.setAttribute('title', label);
    return;
  }

  const absCopy = event.target.closest('[data-abs-api-key-copy]');
  if (absCopy) {
    const input = document.getElementById('abs-api-key');
    const status = document.getElementById('abs-api-key-copy-status');
    if (!input) return;
    const value = input.dataset.absApiKeyFromEnv === 'true' ? (input.dataset.absApiKeyValue || '') : input.value;
    try {
      await copyTextToClipboard(value);
      if (status) status.textContent = 'Copied.';
    } catch {
      if (status) status.textContent = 'Copy failed.';
    }
    if (status) {
      status.hidden = false;
      window.setTimeout(() => { status.hidden = true; }, 2000);
    }
    return;
  }

  const toggle = event.target.closest('[data-api-key-toggle]');
  if (toggle) {
    const input = document.getElementById('absulli-api-key');
    if (!input) return;
    const masked = input.dataset.apiKeyMasked !== 'false';
    const nextMasked = !masked;
    input.value = nextMasked ? '•'.repeat(48) : (input.dataset.apiKeyValue || '');
    input.dataset.apiKeyMasked = nextMasked ? 'true' : 'false';
    const showIcon = toggle.querySelector('.api-key-icon-show');
    const hideIcon = toggle.querySelector('.api-key-icon-hide');
    if (showIcon) showIcon.hidden = !nextMasked;
    if (hideIcon) hideIcon.hidden = nextMasked;
    const label = nextMasked ? 'Show API key' : 'Hide API key';
    toggle.setAttribute('aria-label', label);
    toggle.setAttribute('title', label);
    return;
  }

  const copy = event.target.closest('[data-api-key-copy]');
  if (copy) {
    const input = document.getElementById('absulli-api-key');
    const status = document.getElementById('api-key-copy-status');
    if (!input) return;
    try {
      await copyTextToClipboard(input.dataset.apiKeyValue || '');
      if (status) status.textContent = 'Copied.';
    } catch {
      if (status) status.textContent = 'Copy failed.';
    }
    if (status) {
      status.hidden = false;
      window.setTimeout(() => { status.hidden = true; }, 2000);
    }
    return;
  }

  const regenerate = event.target.closest('[data-api-key-regenerate]');
  if (regenerate && !window.confirm('Regenerate the API key? Applications using the current key will stop working.')) {
    event.preventDefault();
    return;
  }

  const metricsToggle = event.target.closest('[data-metrics-token-toggle]');
  if (metricsToggle) {
    const input = document.getElementById('metrics-token');
    if (!input) return;
    const fromEnv = input.dataset.metricsTokenFromEnv === 'true';
    if (fromEnv) {
      const masked = input.dataset.metricsTokenMasked !== 'false';
      const nextMasked = !masked;
      input.value = nextMasked ? 'Configured via .env read only' : (input.dataset.metricsTokenValue || '');
      input.dataset.metricsTokenMasked = nextMasked ? 'true' : 'false';
      const showIcon = metricsToggle.querySelector('.metrics-token-icon-show');
      const hideIcon = metricsToggle.querySelector('.metrics-token-icon-hide');
      if (showIcon) showIcon.hidden = !nextMasked;
      if (hideIcon) hideIcon.hidden = nextMasked;
      const label = nextMasked ? 'Show metrics token' : 'Hide metrics token';
      metricsToggle.setAttribute('aria-label', label);
      metricsToggle.setAttribute('title', label);
      return;
    }
    if (!input.value) return;
    const showing = input.type === 'text';
    input.type = showing ? 'password' : 'text';
    const showIcon = metricsToggle.querySelector('.metrics-token-icon-show');
    const hideIcon = metricsToggle.querySelector('.metrics-token-icon-hide');
    if (showIcon) showIcon.hidden = !showing;
    if (hideIcon) hideIcon.hidden = showing;
    const label = showing ? 'Show metrics token' : 'Hide metrics token';
    metricsToggle.setAttribute('aria-label', label);
    metricsToggle.setAttribute('title', label);
    return;
  }

  const metricsCopy = event.target.closest('[data-metrics-token-copy]');
  if (metricsCopy) {
    const input = document.getElementById('metrics-token');
    const status = document.getElementById('metrics-token-copy-status');
    if (!input) return;
    const value = input.dataset.metricsTokenFromEnv === 'true' ? (input.dataset.metricsTokenValue || '') : input.value;
    if (!value) return;
    try {
      await copyTextToClipboard(value);
      if (status) status.textContent = 'Copied.';
    } catch {
      if (status) status.textContent = 'Copy failed.';
    }
    if (status) {
      status.hidden = false;
      window.setTimeout(() => { status.hidden = true; }, 2000);
    }
    return;
  }

  const metricsRegenerate = event.target.closest('[data-metrics-token-regenerate]');
  if (metricsRegenerate && !window.confirm('Regenerate the metrics token? Prometheus clients using the current token will stop working.')) {
    event.preventDefault();
  }
});

document.addEventListener('input', (event) => {
  if (event.target.id !== 'metrics-token') return;
  const hasValue = Boolean(event.target.value);
  const toggle = document.querySelector('[data-metrics-token-toggle]');
  const copy = document.querySelector('[data-metrics-token-copy]');
  if (toggle) toggle.disabled = !hasValue;
  if (copy) copy.disabled = !hasValue;
  if (!hasValue) {
    event.target.type = 'password';
    const showIcon = toggle?.querySelector('.metrics-token-icon-show');
    const hideIcon = toggle?.querySelector('.metrics-token-icon-hide');
    if (showIcon) showIcon.hidden = false;
    if (hideIcon) hideIcon.hidden = true;
    if (toggle) {
      toggle.setAttribute('aria-label', 'Show metrics token');
      toggle.setAttribute('title', 'Show metrics token');
    }
  }
});

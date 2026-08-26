const $ = (selector) => document.querySelector(selector);
const form = $('#researchForm');
const questionInput = $('#question');
const composerView = $('#composerView');
const workspaceView = $('#workspaceView');
const timeline = $('#timeline');
let controller = null;
let timer = null;
let startedAt = 0;

const escapeHtml = (value = '') => String(value).replace(/[&<>"']/g, (character) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[character]));

function inlineMarkdown(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}

function markdown(text) {
  const lines = String(text || '').split('\n');
  let html = '';
  let list = null;
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const line = raw.trim();
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    const bullet = line.match(/^[-*]\s+(.+)$/);
    const numbered = line.match(/^\d+\.\s+(.+)$/);
    if (heading) { closeList(); const level = heading[1].length; html += `<h${level}>${inlineMarkdown(heading[2])}</h${level}>`; }
    else if (bullet || numbered) { const next = bullet ? 'ul' : 'ol'; if (list !== next) { closeList(); list = next; html += `<${list}>`; } html += `<li>${inlineMarkdown((bullet || numbered)[1])}</li>`; }
    else if (!line) closeList();
    else { closeList(); html += `<p>${inlineMarkdown(line)}</p>`; }
  }
  closeList();
  return html;
}

function addTimeline(message, active = true) {
  timeline.querySelectorAll('.active').forEach((item) => item.classList.remove('active'));
  const item = document.createElement('li');
  item.className = active ? 'active' : '';
  item.textContent = message;
  timeline.append(item);
  timeline.scrollTop = timeline.scrollHeight;
}

function updateMetric(name, value, target) {
  $(`#metric${name}`).innerHTML = `${value}<span>/${target}</span>`;
  $(`#bar${name}`).style.width = `${Math.min(100, (value / target) * 100)}%`;
}

function resetMetrics() {
  updateMetric('Angles', 0, 3); updateMetric('Sources', 0, 4);
  updateMetric('Domains', 0, 3); updateMetric('Authority', 0, 2);
  $('#tokenCount').textContent = '—';
}

function formatRole(role = 'context') { return role.replaceAll('_', ' '); }
function host(url) { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; } }

function renderSources(sources) {
  $('#sourceCount').textContent = `${sources.length} source${sources.length === 1 ? '' : 's'}`;
  $('#sourceList').innerHTML = sources.map((source) => `
    <a class="source-card" href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">
      <span class="source-id">${escapeHtml(source.source_id)}</span>
      <span><strong>${escapeHtml(source.title || host(source.url))}</strong><small>${escapeHtml(host(source.url))}</small></span>
      <span class="source-role">${escapeHtml(formatRole(source.source_role))}</span>
    </a>`).join('');
}

function handleEvent(event) {
  if (event.event === 'run_started') {
    $('#modelName').textContent = event.model;
    addTimeline('Research session opened');
  } else if (event.event === 'phase') {
    $('#currentPhase').textContent = event.message;
    addTimeline(event.message);
  } else if (event.event === 'tool_started') {
    const message = event.tool === 'web_search'
      ? `Searching · ${event.focus || event.query}`
      : `Reading · ${host(event.url)}`;
    $('#currentPhase').textContent = message;
    addTimeline(message);
  } else if (event.event === 'tool_completed') {
    updateMetric('Angles', event.distinct_searches || 0, 3);
    updateMetric('Sources', event.sources_read || 0, 4);
    updateMetric('Domains', event.domains || 0, 3);
    updateMetric('Authority', event.authoritative || 0, 2);
    if (event.ok && event.tool === 'read_url') addTimeline(`Verified ${event.source_id} · ${event.title || host(event.url)}`, false);
    if (!event.ok) addTimeline(`Source skipped · ${event.error}`, false);
  } else if (event.event === 'depth_check') {
    $('#currentPhase').textContent = event.message;
    addTimeline(`Depth check · ${event.gaps.join(', ')}`);
  } else if (event.event === 'result') {
    $('#currentPhase').textContent = 'Research complete';
    addTimeline('Evidence audit passed');
    $('#reportBody').innerHTML = markdown(event.report);
    renderSources(event.sources || []);
    $('#tokenCount').textContent = event.tokens ? event.tokens.toLocaleString() : '—';
    $('#reportEmpty').hidden = true;
    $('#reportContent').hidden = false;
    const stats = event.research || {};
    updateMetric('Angles', stats.distinct_searches || 0, 3); updateMetric('Sources', stats.sources_read || 0, 4);
    updateMetric('Domains', stats.source_domains || 0, 3); updateMetric('Authority', stats.authoritative_sources || 0, 2);
  } else if (event.event === 'error') {
    $('#currentPhase').textContent = 'The run stopped';
    addTimeline(event.message || 'Unexpected research error');
  }
}

async function runResearch(question) {
  controller = new AbortController();
  composerView.hidden = true; workspaceView.hidden = false;
  $('#activeQuestion').textContent = question; timeline.innerHTML = '';
  $('#reportEmpty').hidden = false; $('#reportContent').hidden = true; resetMetrics();
  startedAt = Date.now();
  timer = setInterval(() => { const seconds = Math.floor((Date.now() - startedAt) / 1000); $('#elapsed').textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`; }, 1000);
  try {
    const response = await fetch('/api/research', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question}), signal:controller.signal });
    if (!response.ok) { const problem = await response.json(); throw new Error(problem.error || 'Research could not start'); }
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
    while (true) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), {stream:!done});
      const lines = buffer.split('\n'); buffer = lines.pop();
      lines.filter(Boolean).forEach((line) => handleEvent(JSON.parse(line)));
      if (done) { if (buffer.trim()) handleEvent(JSON.parse(buffer)); break; }
    }
  } catch (error) {
    if (error.name !== 'AbortError') handleEvent({event:'error', message:error.message});
  } finally { clearInterval(timer); controller = null; }
}

form.addEventListener('submit', (event) => { event.preventDefault(); const question = questionInput.value.trim(); if (question) runResearch(question); });
document.querySelectorAll('[data-question]').forEach((button) => button.addEventListener('click', () => { questionInput.value = button.dataset.question; questionInput.focus(); }));
$('#newResearch').addEventListener('click', () => { if (controller) controller.abort(); clearInterval(timer); workspaceView.hidden = true; composerView.hidden = false; questionInput.focus(); });

fetch('/api/health').then((response) => response.json()).then((health) => {
  $('#healthDot').classList.add(health.ok ? 'online' : 'offline');
  $('#healthText').textContent = health.ok ? 'Agent online' : 'Setup required';
  if (health.model) $('#modelName').textContent = health.model;
}).catch(() => { $('#healthDot').classList.add('offline'); $('#healthText').textContent = 'Agent offline'; });

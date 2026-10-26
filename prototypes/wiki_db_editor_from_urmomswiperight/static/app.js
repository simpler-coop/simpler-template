let currentUser = null;
let gateEnabled = true;
let allRecords = [];

document.addEventListener('DOMContentLoaded', async () => {
  await fetchCurrentUser();
  await fetchGateStatus();
  await loadRecords();
  await loadPendingEdits();
  await loadAuditTrail();
});

async function fetchCurrentUser() {
  try {
    const res = await fetch('/api/me');
    const data = await res.json();
    currentUser = data.user;
    updateUserUI();
  } catch (err) {
    console.error('Failed to fetch user:', err);
  }
}

function updateUserUI() {
  if (!currentUser) return;
  document.getElementById('user-name').textContent = currentUser.name;
  document.getElementById('user-role').textContent = `${currentUser.role} (${currentUser.provider})`;
  document.getElementById('persona-select').value = currentUser.user_id;

  const isAdmin = currentUser.role === 'admin';
  document.getElementById('admin-gate-controls').style.display = isAdmin ? 'flex' : 'none';
  document.getElementById('moderation-tab-btn').style.display = isAdmin ? 'inline-block' : 'none';
}

async function switchPersona(userId) {
  try {
    const res = await fetch('/api/auth/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId })
    });
    if (res.ok) {
      await fetchCurrentUser();
      await loadRecords();
      await loadPendingEdits();
    }
  } catch (err) {
    alert('Failed to switch persona');
  }
}

async function fetchGateStatus() {
  try {
    const res = await fetch('/api/gate');
    const data = await res.json();
    gateEnabled = data.admin_review_gate;
    updateGateUI();
  } catch (err) {
    console.error('Failed to get gate status:', err);
  }
}

function updateGateUI() {
  const pill = document.getElementById('gate-pill');
  const desc = document.getElementById('gate-description');
  const checkbox = document.getElementById('gate-checkbox');

  checkbox.checked = gateEnabled;
  if (gateEnabled) {
    pill.textContent = 'GATE: ACTIVE';
    pill.style.background = '#2563eb';
    desc.textContent = 'Admin Review Gate is ENABLED. Changes enter the staging queue and require admin approval before updating the main database.';
  } else {
    pill.textContent = 'GATE: BYPASS (DIRECT)';
    pill.style.background = '#16a34a';
    desc.textContent = 'Admin Review Gate is DISABLED. Changes apply directly to the main database and are logged to the audit history.';
  }
}

async function toggleGate(checked) {
  try {
    const res = await fetch('/api/gate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: checked })
    });
    if (res.ok) {
      gateEnabled = checked;
      updateGateUI();
    } else {
      const err = await res.json();
      alert(`Error: ${err.detail}`);
      checkbox.checked = gateEnabled;
    }
  } catch (err) {
    alert('Failed to toggle review gate');
  }
}

async function loadRecords() {
  const tbody = document.getElementById('records-tbody');
  try {
    const res = await fetch('/api/records');
    const data = await res.json();
    allRecords = data.records;

    if (allRecords.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #64748b;">No active records found.</td></tr>';
      return;
    }

    tbody.innerHTML = allRecords.map(rec => `
      <tr>
        <td><strong>#${rec.record_id}</strong></td>
        <td><strong>${escapeHtml(rec.title)}</strong></td>
        <td><span class="badge-category">${escapeHtml(rec.category)}</span></td>
        <td>${escapeHtml(rec.content)}</td>
        <td>
          <small>${escapeHtml(rec.last_modified_by_name || rec.last_modified_by)}<br>
          <span style="color: #94a3b8;">${new Date(rec.updated_at).toLocaleDateString()}</span></small>
        </td>
        <td>
          <button class="btn btn-secondary btn-sm" onclick="openEditModal(${rec.record_id})">✏️ Edit</button>
        </td>
      </tr>
    `).join('');
  } catch (err) {
    tbody.innerHTML = '<tr><td colspan="6" style="color: red;">Failed to load records.</td></tr>';
  }
}

async function loadPendingEdits() {
  const container = document.getElementById('pending-list');
  const countBadge = document.getElementById('pending-count');
  try {
    const res = await fetch('/api/pending');
    const data = await res.json();
    const pending = data.pending_edits;
    countBadge.textContent = pending.length;

    if (pending.length === 0) {
      container.innerHTML = '<div style="padding: 24px; text-align: center; color: #64748b; background: white; border: 1px solid #e2e8f0; border-radius: 8px;">No pending edits in the review queue. All changes are up to date! 🎉</div>';
      return;
    }

    const isAdmin = currentUser && currentUser.role === 'admin';

    container.innerHTML = pending.map(item => `
      <div class="proposal-card">
        <div class="proposal-header">
          <div>
            <strong>Proposal #${item.edit_id} — ${item.operation} Record ${item.record_id ? '#' + item.record_id : '(New Record)'}</strong>
            <div class="proposal-meta">
              Submitted by <strong>${escapeHtml(item.submitter_name)}</strong> (${escapeHtml(item.submitter_email)}) on ${new Date(item.submitted_at).toLocaleString()}
            </div>
          </div>
          <div>
            <span class="gate-status-pill" style="background: #f59e0b;">Pending Review</span>
          </div>
        </div>

        <div class="diff-grid">
          <div class="diff-box old">
            <div class="diff-label">Previous Value</div>
            ${item.operation === 'INSERT' ? '<em>None (New Entry)</em>' : `
              <div><strong>Title:</strong> ${escapeHtml(item.previous_title || '')}</div>
              <div><strong>Category:</strong> ${escapeHtml(item.previous_category || '')}</div>
              <div><strong>Content:</strong> ${escapeHtml(item.previous_content || '')}</div>
            `}
          </div>
          <div class="diff-box new">
            <div class="diff-label">Proposed Value</div>
            <div><strong>Title:</strong> ${escapeHtml(item.proposed_title || '')}</div>
            <div><strong>Category:</strong> ${escapeHtml(item.proposed_category || '')}</div>
            <div><strong>Content:</strong> ${escapeHtml(item.proposed_content || '')}</div>
          </div>
        </div>

        ${isAdmin ? `
          <div class="proposal-actions">
            <button class="btn btn-secondary btn-sm" onclick="rejectProposal(${item.edit_id})">Reject</button>
            <button class="btn btn-success btn-sm" onclick="approveProposal(${item.edit_id})">✓ Approve & Commit</button>
          </div>
        ` : `
          <div style="font-size: 12px; color: #64748b; text-align: right;">Admin approval required to merge this change.</div>
        `}
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = '<div style="color: red;">Failed to load pending queue.</div>';
  }
}

async function loadAuditTrail() {
  const tbody = document.getElementById('audit-tbody');
  try {
    const res = await fetch('/api/audit');
    const data = await res.json();
    const trail = data.audit_trail;

    if (trail.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: #64748b;">Audit trail is empty.</td></tr>';
      return;
    }

    tbody.innerHTML = trail.map(t => `
      <tr>
        <td>#${t.log_id}</td>
        <td><strong>${t.operation}</strong></td>
        <td>#${t.record_id}</td>
        <td><code>${escapeHtml(t.field_name)}</code></td>
        <td style="color: #dc2626;">${escapeHtml(t.old_value || '-')}</td>
        <td style="color: #16a34a;">${escapeHtml(t.new_value || '-')}</td>
        <td>${escapeHtml(t.user_name || t.applied_by)}</td>
        <td><small>${new Date(t.applied_at).toLocaleString()}</small></td>
      </tr>
    `).join('');
  } catch (err) {
    tbody.innerHTML = '<tr><td colspan="8" style="color: red;">Failed to load audit history.</td></tr>';
  }
}

// Modal Handlers
function openNewModal() {
  document.getElementById('modal-title').textContent = 'Submit New Entry';
  document.getElementById('form-record-id').value = '';
  document.getElementById('form-title').value = '';
  document.getElementById('form-category').value = '';
  document.getElementById('form-content').value = '';
  document.getElementById('entry-modal').style.display = 'flex';
}

function openEditModal(recordId) {
  const record = allRecords.find(r => r.record_id === recordId);
  if (!record) return;

  document.getElementById('modal-title').textContent = `Edit Record #${record.record_id}`;
  document.getElementById('form-record-id').value = record.record_id;
  document.getElementById('form-title').value = record.title;
  document.getElementById('form-category').value = record.category;
  document.getElementById('form-content').value = record.content;
  document.getElementById('entry-modal').style.display = 'flex';
}

function closeModal() {
  document.getElementById('entry-modal').style.display = 'none';
}

async function submitRecordForm(e) {
  e.preventDefault();
  const recordId = document.getElementById('form-record-id').value;
  const payload = {
    title: document.getElementById('form-title').value.trim(),
    category: document.getElementById('form-category').value.trim(),
    content: document.getElementById('form-content').value.trim()
  };

  const isEdit = Boolean(recordId);
  const url = isEdit ? `/api/records/${recordId}` : '/api/records';
  const method = isEdit ? 'PUT' : 'POST';

  try {
    const res = await fetch(url, {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const result = await res.json();

    if (!res.ok) {
      alert(`Error: ${result.detail || 'Failed to submit'}`);
      return;
    }

    closeModal();
    if (result.status === 'pending') {
      alert('✓ Submission received! It has been staged in the Review Queue awaiting admin approval.');
    } else {
      alert('✓ Submission saved and applied directly to the database!');
    }

    await loadRecords();
    await loadPendingEdits();
    await loadAuditTrail();
  } catch (err) {
    alert('Network error while saving changes');
  }
}

async function approveProposal(editId) {
  try {
    const res = await fetch(`/api/pending/${editId}/approve`, { method: 'POST' });
    if (res.ok) {
      await loadRecords();
      await loadPendingEdits();
      await loadAuditTrail();
    } else {
      const err = await res.json();
      alert(`Approval error: ${err.detail}`);
    }
  } catch (err) {
    alert('Failed to approve proposal');
  }
}

async function rejectProposal(editId) {
  const reason = prompt('Reason for rejection:', 'Does not meet contribution criteria');
  if (reason === null) return;

  try {
    const res = await fetch(`/api/pending/${editId}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason })
    });
    if (res.ok) {
      await loadPendingEdits();
    } else {
      const err = await res.json();
      alert(`Rejection error: ${err.detail}`);
    }
  } catch (err) {
    alert('Failed to reject proposal');
  }
}

async function revertUser() {
  const userId = document.getElementById('purge-user-select').value;
  if (!confirm(`Are you sure you want to revert all changes made by user ${userId}? This will roll back all modifications and discard pending edits.`)) {
    return;
  }

  try {
    const res = await fetch('/api/admin/revert-user', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId })
    });
    const result = await res.json();
    alert(`Reverted ${result.reverted_actions} actions by ${result.user_id}`);
    await loadRecords();
    await loadPendingEdits();
    await loadAuditTrail();
  } catch (err) {
    alert('Failed to revert user');
  }
}

function showTab(tabId) {
  document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById(tabId).style.display = 'block';

  const tabButtons = {
    'records-tab': 0,
    'pending-tab': 1,
    'audit-tab': 2,
    'moderation-tab': 3
  };
  const btns = document.querySelectorAll('.tab-btn');
  if (btns[tabButtons[tabId]]) {
    btns[tabButtons[tabId]].classList.add('active');
  }
}

function escapeHtml(text) {
  if (!text) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

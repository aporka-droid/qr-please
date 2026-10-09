const form = document.querySelector('#generator');
const submit = document.querySelector('#generate');
const message = document.querySelector('#form-message');
const history = document.querySelector('#history');
const historyMessage = document.querySelector('#history-message');
let codes = [];
let selectedId = null;

async function api(path, options = {}) {
    const response = await fetch(path, {
        ...options,
        headers: { 'Content-Type': 'application/json', 'X-QR-Request': '1' }
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
    return data;
}

function selectCode(code) {
    selectedId = code.id;
    const image = document.querySelector('#qr-image');
    image.src = `/api/codes/${code.id}/png`;
    image.hidden = false;
    document.querySelector('#empty-preview').hidden = true;
    document.querySelector('#preview-title').textContent = code.title;
    document.querySelector('#preview-url').textContent = code.url;
    document.querySelector('#preview-status').textContent = 'SAVED TO COLLECTION';
    for (const type of ['png', 'svg']) {
        const link = document.querySelector(`#${type}-download`);
        link.href = `/api/codes/${code.id}/${type}?download=1`;
        link.classList.remove('disabled');
        link.removeAttribute('aria-disabled');
    }
}

function clearPreview() {
    selectedId = null;
    document.querySelector('#qr-image').hidden = true;
    document.querySelector('#empty-preview').hidden = false;
    document.querySelector('#preview-title').textContent = 'Your next QR code';
    document.querySelector('#preview-url').textContent = 'Add a URL to get started.';
    document.querySelector('#preview-status').textContent = 'READY WHEN YOU ARE';
    for (const type of ['png', 'svg']) {
        const link = document.querySelector(`#${type}-download`);
        link.removeAttribute('href');
        link.classList.add('disabled');
        link.setAttribute('aria-disabled', 'true');
    }
}

function renderHistory() {
    history.replaceChildren();
    document.querySelector('#count').textContent = codes.length;
    historyMessage.hidden = codes.length > 0;
    historyMessage.textContent = 'No codes yet. Your first one will be saved here.';
    for (const code of codes) {
        const card = document.createElement('article');
        card.className = 'history-card';
        const image = document.createElement('img');
        image.src = `/api/codes/${code.id}/png`;
        image.alt = '';
        image.loading = 'lazy';
        const details = document.createElement('div');
        const title = document.createElement('h3');
        title.textContent = code.title;
        const url = document.createElement('p');
        url.textContent = code.url;
        url.title = code.url;
        details.append(title, url);
        const actions = document.createElement('div');
        actions.className = 'card-actions';
        const date = document.createElement('span');
        date.textContent = new Date(code.created).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
        const buttons = document.createElement('div');
        const open = document.createElement('button');
        open.textContent = 'Open';
        open.setAttribute('aria-label', `Open ${code.title}`);
        open.onclick = () => {
            selectCode(code);
            document.querySelector('#paper').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
        };
        const remove = document.createElement('button');
        remove.textContent = 'Delete';
        remove.className = 'delete';
        remove.setAttribute('aria-label', `Delete ${code.title}`);
        remove.onclick = async () => {
            if (!confirm(`Delete “${code.title}” from your collection?`)) return;
            remove.disabled = true;
            try {
                await api(`/api/codes/${code.id}`, { method: 'DELETE' });
                codes = codes.filter(item => item.id !== code.id);
                if (selectedId === code.id) codes.length ? selectCode(codes[0]) : clearPreview();
                renderHistory();
            } catch (error) {
                historyMessage.hidden = false;
                historyMessage.textContent = error.message;
                remove.disabled = false;
            }
        };
        buttons.append(open, remove);
        actions.append(date, buttons);
        card.append(image, details, actions);
        history.append(card);
    }
}

form.addEventListener('submit', async event => {
    event.preventDefault();
    submit.disabled = true;
    message.textContent = 'Creating your code…';
    try {
        const code = await api('/api/codes', {
            method: 'POST',
            body: JSON.stringify(Object.fromEntries(new FormData(form)))
        });
        codes.unshift(code);
        selectCode(code);
        renderHistory();
        message.textContent = 'Created and saved.';
    } catch (error) {
        message.textContent = error.message;
    } finally {
        submit.disabled = false;
    }
});

document.querySelector('#qr-image').addEventListener('error', () => {
    message.textContent = 'The image could not load. Reopen this saved code to try again.';
});

async function loadHistory() {
    try {
        codes = await api('/api/codes');
        renderHistory();
        if (codes.length) selectCode(codes[0]);
    } catch (error) {
        historyMessage.textContent = 'Could not load your collection. Refresh to try again.';
    }
}
loadHistory();

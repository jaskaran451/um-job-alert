const form = document.querySelector('#subscription-form');

if (form) {
    const input = document.querySelector('#keyword-input');
    const list = document.querySelector('#keyword-list');
    const add = document.querySelector('#add-keyword');
    const telegramPanel = document.querySelector('#telegram-connect-panel');
    const telegramLink = document.querySelector('#telegram-connect-link');
    const keywords = [];

    function renderKeywords() {
        list.replaceChildren();
        keywords.forEach((keyword, index) => {
            const tag = document.createElement('span');
            tag.className = 'tag';
            tag.append(keyword);

            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.textContent = '×';
            removeButton.setAttribute('aria-label', `Remove ${keyword}`);
            removeButton.addEventListener('click', () => {
                keywords.splice(index, 1);
                renderKeywords();
            });

            tag.append(removeButton);
            list.append(tag);
        });
    }

    function addKeyword(value) {
        const keyword = value.trim().replace(/\s+/g, ' ').replace(/^,+|,+$/g, '');
        if (keyword.length < 2 || keyword.length > 40 || keywords.length >= 8) {
            return;
        }
        if (!keywords.some(existing => existing.toLowerCase() === keyword.toLowerCase())) {
            keywords.push(keyword);
        }
        input.value = '';
        renderKeywords();
    }

    add.addEventListener('click', () => addKeyword(input.value));
    input.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ',') {
            event.preventDefault();
            addKeyword(input.value);
        }
    });
    document.querySelectorAll('[data-keyword]').forEach(button => {
        button.addEventListener('click', () => addKeyword(button.dataset.keyword));
    });

    const allRoles = form.querySelector('input[value="all"]');
    const specificRoles = [...form.querySelectorAll('input[name="role_type"]')]
        .filter(role => role !== allRoles);
    allRoles.addEventListener('change', () => {
        if (allRoles.checked) {
            specificRoles.forEach(role => {
                role.checked = false;
            });
        }
    });
    specificRoles.forEach(role => {
        role.addEventListener('change', () => {
            if (role.checked) {
                allRoles.checked = false;
            }
        });
    });

    form.addEventListener('submit', async event => {
        event.preventDefault();
        ['email', 'preferences', 'consent'].forEach(name => {
            document.querySelector(`#${name}-error`).textContent = '';
        });

        const message = document.querySelector('#form-message');
        const submitButton = form.querySelector('.primary');
        message.className = 'message';
        message.textContent = '';
        telegramPanel.hidden = true;
        telegramLink.removeAttribute('href');
        submitButton.disabled = true;

        const payload = {
            email: document.querySelector('#email').value,
            role_types: [...form.querySelectorAll('input[name="role_type"]:checked')]
                .map(role => role.value),
            keywords,
            consent: document.querySelector('#consent').checked,
            company: document.querySelector('#company').value
        };

        try {
            const response = await fetch('/api/subscriptions', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const result = await response.json();

            if (!response.ok) {
                Object.entries(result.errors || {}).forEach(([name, error]) => {
                    const element = document.querySelector(`#${name}-error`);
                    if (element) {
                        element.textContent = error;
                    }
                });
                throw new Error(result.message || 'Could not save your alert.');
            }

            message.className = 'message success';
            message.textContent = `Alert saved. We’ll email ${result.email} when a new title matches.`;

            if (result.telegram_connect_url) {
                telegramLink.href = result.telegram_connect_url;
                telegramLink.textContent = result.telegram_connected
                    ? 'Reconnect Telegram ↗'
                    : 'Connect Telegram ↗';
                telegramPanel.hidden = false;
            }
        } catch (error) {
            if (!message.textContent) {
                message.className = 'message error-box';
                message.textContent = error.message;
            }
        } finally {
            submitButton.disabled = false;
        }
    });
}
